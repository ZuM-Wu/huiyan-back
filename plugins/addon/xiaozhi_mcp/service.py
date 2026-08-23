# -*- coding: utf-8 -*-
"""小智 WebSocket 与慧眼 MCP 工具桥的运行时服务。"""
import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import mcp.types as mcp_types
import websockets
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from pydantic import ValidationError

from core.config_service import get_config
from core.db.base import async_session_factory
from services.ai import tool_bridge

logger = logging.getLogger(__name__)
# websockets 的调试日志可能包含握手路径与查询令牌，本桥只输出自行脱敏的状态日志。
wire_logger = logging.getLogger(f"{__name__}.wire")
wire_logger.disabled = True

PLUGIN_NAME = "xiaozhi_mcp"
INITIAL_BACKOFF_SECONDS = 1
MAX_BACKOFF_SECONDS = 600
CONNECT_TIMEOUT_SECONDS = 10
TEST_TIMEOUT_SECONDS = 15
TOOL_RESULT_MAX_BYTES = 1024
TOOL_LIST_MAX_ESTIMATED_TOKENS = 8000
RESULT_TOO_LARGE_TEXT = "结果过长，请缩小查询范围"


class BridgeLimitError(Exception):
    """小智侧协议容量限制被触发。"""


@dataclass
class BridgeState:
    """单接入点运行状态；不保存任何密钥或完整地址。"""

    status: str = "stopped"
    last_connected_at: str = ""
    last_disconnected_reason: str = ""
    reconnect_attempts: int = 0
    last_tool_count: int = 0
    last_tool_token_estimate: int = 0
    tool_list_over_limit: bool = False
    source_stats: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_stats"] = self.source_stats or {
            "core": 0, "plugin": 0, "external": 0,
        }
        return data


class XiaozhiMcpBridge:
    """维护唯一 WebSocket 连接并响应小智发来的 MCP JSON-RPC 请求。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._state = BridgeState()

    async def start(self) -> bool:
        """按数据库配置幂等启动常驻连接。"""
        async with self._lock:
            if self._task and not self._task.done():
                return True
            settings = await self._load_settings()
            if not settings["enabled"]:
                self._state.status = "disabled"
                return False
            if not settings["endpoint_url"]:
                self._state.status = "config_missing"
                self._state.last_disconnected_reason = "小智 MCP 接入点未配置"
                return False
            self._state.status = "connecting"
            self._task = asyncio.create_task(self._run(), name="xiaozhi-mcp-bridge")
            return True

    async def stop(self, reason: str = "已手动断开") -> None:
        """取消常驻任务并等待连接资源释放。"""
        async with self._lock:
            task = self._task
            self._task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._state.status = "stopped"
        self._state.last_disconnected_reason = reason

    async def restart(self) -> bool:
        """应用最新配置重建连接，保证同一时刻最多一个任务。"""
        await self.stop("配置已更新，正在重连")
        return await self.start()

    def status(self) -> dict[str, Any]:
        """返回脱敏运行状态。"""
        data = self._state.to_dict()
        data["task_running"] = bool(self._task and not self._task.done())
        return data

    async def preview_tools(self) -> dict[str, Any]:
        """列出已启用小智专属 MCP 的缓存工具。"""
        from plugins.addon.xiaozhi_mcp.models import XiaozhiMcpServerModel

        async with async_session_factory() as db:
            declarations = await tool_bridge.list_cached_external_declarations(
                db,
                XiaozhiMcpServerModel,
            )
        tools, estimate, stats = self._convert_tools(declarations)
        self._update_tool_state(tools, estimate, stats)
        return {
            "list": [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools],
            "estimated_tokens": estimate,
            "capacity_limit": TOOL_LIST_MAX_ESTIMATED_TOKENS,
            "over_limit": estimate > TOOL_LIST_MAX_ESTIMATED_TOKENS,
            "source_stats": stats,
        }

    async def test_connection(self) -> dict[str, Any]:
        """校验本地工具，并完成一次临时 MCP 初始化与工具发现。"""
        preview = await self.preview_tools()
        if preview["over_limit"]:
            raise BridgeLimitError("工具清单超过小智容量限制，请减少可用工具")
        if self._state.status == "connected":
            return {"success": True, "message": "当前连接正常", **preview}

        settings = await self._load_settings()
        if not settings["endpoint_url"]:
            raise ValueError("小智 MCP 接入点未配置")
        try:
            async with websockets.connect(
                settings["endpoint_url"],
                open_timeout=CONNECT_TIMEOUT_SECONDS,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                logger=wire_logger,
            ) as websocket:
                await asyncio.wait_for(
                    self._probe_connection(websocket),
                    timeout=TEST_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            logger.warning("[xiaozhi_mcp] 接入点测试失败: %s", type(exc).__name__)
            raise ConnectionError("小智 MCP 接入点连接失败") from None
        return {"success": True, "message": "连接成功", **preview}

    async def handle_message(self, raw_message: str | bytes) -> tuple[str | None, bool]:
        """解析一个 MCP JSON-RPC 帧，返回响应文本和是否应关闭连接。"""
        if isinstance(raw_message, bytes):
            try:
                raw_message = raw_message.decode("utf-8")
            except UnicodeDecodeError:
                return self._error_response(0, -32700, "请求不是有效 UTF-8"), False
        try:
            message = mcp_types.JSONRPCMessage.model_validate_json(raw_message.strip()).root
        except ValidationError:
            return self._error_response(0, -32700, "JSON-RPC 请求格式错误"), False

        if isinstance(message, mcp_types.JSONRPCNotification):
            return None, False
        if not isinstance(message, mcp_types.JSONRPCRequest):
            return None, False

        try:
            result = await self._dispatch_request(message)
            return self._success_response(message.id, result), False
        except BridgeLimitError as exc:
            return self._error_response(message.id, -32002, str(exc)), False
        except ValidationError:
            return self._error_response(message.id, -32602, "请求参数错误"), False
        except KeyError:
            return self._error_response(message.id, -32601, "不支持的 MCP 方法"), False
        except Exception:
            logger.exception("[xiaozhi_mcp] MCP 请求处理失败: method=%s", message.method)
            return self._error_response(message.id, -32603, "自定义 MCP 调用失败"), False

    async def _run(self) -> None:
        backoff = INITIAL_BACKOFF_SECONDS
        while True:
            try:
                settings = await self._load_settings()
                if not settings["enabled"]:
                    self._state.status = "disabled"
                    return
                self._state.status = "connecting"
                async with websockets.connect(
                    settings["endpoint_url"],
                    open_timeout=CONNECT_TIMEOUT_SECONDS,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    logger=wire_logger,
                ) as websocket:
                    self._state.status = "connected"
                    self._state.last_connected_at = _now_text()
                    self._state.last_disconnected_reason = ""
                    self._state.reconnect_attempts = 0
                    backoff = INITIAL_BACKOFF_SECONDS
                    logger.info("[xiaozhi_mcp] WebSocket 已连接")
                    await self._serve_connection(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._state.status = "reconnecting"
                self._state.reconnect_attempts += 1
                self._state.last_disconnected_reason = _safe_error_name(exc)
                logger.warning(
                    "[xiaozhi_mcp] 连接中断，%s 秒后重试: %s",
                    backoff,
                    type(exc).__name__,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    async def _serve_connection(self, websocket) -> None:
        async for raw_message in websocket:
            response, should_close = await self.handle_message(raw_message)
            if response is not None:
                await websocket.send(response + "\n")
            if should_close:
                await websocket.close(code=1008, reason="MCP tool access failed")
                return

    async def _probe_connection(self, websocket) -> None:
        """服务一次临时握手，收到 tools/list 并响应后即完成探测。"""
        initialized = False
        async for raw_message in websocket:
            method = self._message_method(raw_message)
            response, should_close = await self.handle_message(raw_message)
            if response is not None:
                await websocket.send(response + "\n")
            if should_close:
                raise ConnectionError("小智 MCP 工具探测失败")
            initialized = initialized or method in {
                "initialize", "notifications/initialized",
            }
            if initialized and method == "tools/list":
                return

    async def _dispatch_request(self, request: mcp_types.JSONRPCRequest) -> dict[str, Any]:
        if request.method == "initialize":
            params = mcp_types.InitializeRequestParams.model_validate(request.params or {})
            requested = str(params.protocolVersion)
            protocol = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else mcp_types.LATEST_PROTOCOL_VERSION
            result = mcp_types.InitializeResult(
                protocolVersion=protocol,
                capabilities=mcp_types.ServerCapabilities(
                    tools=mcp_types.ToolsCapability(listChanged=True)
                ),
                serverInfo=mcp_types.Implementation(
                    name="慧眼护农小智MCP", version="1.3.0"
                ),
                instructions="通过小智插件专属的自定义 MCP 服务调用工具。",
            )
            return result.model_dump(by_alias=True, exclude_none=True)
        if request.method == "ping":
            return {}
        if request.method == "tools/list":
            preview = await self.preview_tools()
            if preview["over_limit"]:
                raise BridgeLimitError("工具清单超过小智容量限制，请减少可用工具")
            return {"tools": preview["list"]}
        if request.method == "tools/call":
            params = mcp_types.CallToolRequestParams.model_validate(request.params or {})
            from plugins.addon.xiaozhi_mcp.models import XiaozhiMcpServerModel

            result_text, denied = await tool_bridge.call_cached_external_tool(
                params.name,
                params.arguments or {},
                XiaozhiMcpServerModel,
            )
            if len(result_text.encode("utf-8")) > TOOL_RESULT_MAX_BYTES:
                result_text = RESULT_TOO_LARGE_TEXT
                denied = True
            result = mcp_types.CallToolResult(
                content=[mcp_types.TextContent(type="text", text=result_text)],
                isError=denied,
            )
            return result.model_dump(by_alias=True, exclude_none=True)
        raise KeyError(request.method)

    @staticmethod
    def _convert_tools(declarations: list[dict]) -> tuple[list, int, dict[str, int]]:
        tools = []
        stats = {"core": 0, "plugin": 0, "external": 0}
        for declaration in declarations:
            function = declaration.get("function") or {}
            name = function.get("name") or ""
            if not name:
                continue
            tools.append(mcp_types.Tool(
                name=name,
                description=function.get("description") or "",
                inputSchema=function.get("parameters") or {
                    "type": "object", "properties": {},
                },
            ))
            source = XiaozhiMcpBridge._tool_source(name)
            stats[source] += 1
        raw = json.dumps(
            [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        estimate = max(1, (len(raw.encode("utf-8")) + 3) // 4)
        return tools, estimate, stats

    def _update_tool_state(self, tools: list, estimate: int, stats: dict[str, int]) -> None:
        self._state.last_tool_count = len(tools)
        self._state.last_tool_token_estimate = estimate
        self._state.tool_list_over_limit = estimate > TOOL_LIST_MAX_ESTIMATED_TOKENS
        self._state.source_stats = stats

    @staticmethod
    def _tool_source(name: str) -> str:
        if name.startswith(("ext__", "fext__")):
            return "external"
        from services.mcp.registry import get_tool_meta
        meta = get_tool_meta(name) or {}
        return "core" if meta.get("owner") == "core" else "plugin"

    @staticmethod
    def _message_method(raw_message: str | bytes) -> str:
        """仅为临时握手提取方法名，不参与协议分派。"""
        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            message = mcp_types.JSONRPCMessage.model_validate_json(raw_message.strip()).root
            return getattr(message, "method", "")
        except (UnicodeDecodeError, ValidationError):
            return ""

    @staticmethod
    async def _load_settings() -> dict[str, Any]:
        enabled = await get_config(f"{PLUGIN_NAME}.enabled")
        endpoint_url = await get_config(f"{PLUGIN_NAME}.endpoint_url") or ""
        return {"enabled": enabled == "1", "endpoint_url": endpoint_url}

    @staticmethod
    def _success_response(request_id: str | int, result: dict[str, Any]) -> str:
        return mcp_types.JSONRPCResponse(
            jsonrpc="2.0", id=request_id, result=result
        ).model_dump_json(by_alias=True, exclude_none=True)

    @staticmethod
    def _error_response(request_id: str | int, code: int, message: str) -> str:
        return mcp_types.JSONRPCError(
            jsonrpc="2.0",
            id=request_id,
            error=mcp_types.ErrorData(code=code, message=message),
        ).model_dump_json(by_alias=True, exclude_none=True)


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_error_name(exc: Exception) -> str:
    """仅记录异常类别，避免异常文本携带接入点查询令牌。"""
    if isinstance(exc, BridgeLimitError):
        return "工具清单超过小智容量限制"
    return f"连接异常（{type(exc).__name__}）"


xiaozhi_bridge = XiaozhiMcpBridge()
