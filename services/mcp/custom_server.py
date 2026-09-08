# -*- coding: utf-8 -*-
"""外部 MCP 配置的共享解析、校验、脱敏与工具发现服务。"""
import asyncio
import json
import logging
from dataclasses import dataclass
from core.time_utils import china_now
from urllib.parse import urlsplit

from sqlalchemy import select

logger = logging.getLogger(__name__)


class McpConfigError(ValueError):
    """MCP JSON 或服务地址不符合管理端约束。"""


class McpNameConflictError(McpConfigError):
    """同一 MCP 配置范围内存在同名服务器。"""


@dataclass(frozen=True)
class ParsedMcpConfig:
    """规范化后的单 MCP 配置。"""

    url: str
    api_key: str
    config: dict


def _validate_url(url: object) -> str:
    """仅允许带主机的 HTTP/HTTPS Streamable HTTP 地址。"""
    value = str(url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise McpConfigError("MCP 服务地址仅支持 HTTP/HTTPS，且必须包含主机名")
    if parsed.username or parsed.password or parsed.fragment:
        raise McpConfigError("MCP 服务地址不支持内嵌账号、密码或片段")
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise McpConfigError("MCP 服务地址端口无效")
    except ValueError as exc:
        raise McpConfigError("MCP 服务地址端口无效") from exc
    return value


def _extract_api_key(item: dict) -> str:
    """从旧字段或 Authorization Bearer 头中取出密钥。"""
    api_key = str(item.get("api_key") or "").strip()
    if api_key and "***" not in api_key:
        return api_key
    headers = item.get("headers") or {}
    if isinstance(headers, dict):
        authorization = str(headers.get("Authorization") or headers.get("authorization") or "")
        if authorization.lower().startswith("bearer "):
            candidate = authorization[7:].strip()
            if "***" not in candidate:
                return candidate
    return ""


def parse_mcp_config(config_json: str, *, require_single: bool = True) -> ParsedMcpConfig:
    """解析单服务 mcpServers JSON，并兼容旧直接配置格式。"""
    raw = str(config_json or "").strip()
    if not raw:
        raise McpConfigError("请输入 MCP JSON 配置")
    try:
        config = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise McpConfigError("JSON 格式无效") from exc
    if not isinstance(config, dict):
        raise McpConfigError("MCP 配置必须是 JSON 对象")
    if "url" in config:
        url = _validate_url(config.get("url"))
        return ParsedMcpConfig(url, _extract_api_key(config), config)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        raise McpConfigError("JSON 必须包含非空的 mcpServers 对象")
    if require_single and len(servers) != 1:
        raise McpConfigError("每条配置只能包含一个 MCP 服务")
    name, item = next(iter(servers.items()))
    if not isinstance(item, dict):
        raise McpConfigError(f"MCP「{name}」配置必须是对象")
    url = _validate_url(item.get("url"))
    return ParsedMcpConfig(url, _extract_api_key(item), config)


def mask_config_json(config_json: str | None) -> str:
    """返回可回显的脱敏 JSON，不泄露 API Key。"""
    if not config_json:
        return ""
    try:
        config = json.loads(config_json)
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(config, dict):
        return ""
    safe = json.loads(json.dumps(config, ensure_ascii=False))
    if "api_key" in safe:
        safe["api_key"] = "***" if safe["api_key"] else ""
    servers = safe.get("mcpServers")
    if isinstance(servers, dict):
        for item in servers.values():
            if not isinstance(item, dict):
                continue
            if item.get("api_key"):
                item["api_key"] = "***"
            headers = item.get("headers")
            if isinstance(headers, dict):
                for key in list(headers):
                    if key.lower() == "authorization" and headers[key]:
                        headers[key] = "Bearer ***"
    return json.dumps(safe, ensure_ascii=False, indent=2)


def editable_config_json(server) -> str:
    """生成管理页可编辑的脱敏 JSON，并兼容没有 config_json 的历史记录。"""
    masked = mask_config_json(getattr(server, "config_json", None))
    if masked:
        return masked
    legacy = {
        "url": getattr(server, "url", ""),
        "api_key": "***" if getattr(server, "api_key", "") else "",
    }
    return json.dumps(legacy, ensure_ascii=False, indent=2)


def parse_server_request(data, *, strict_json: bool = False) -> tuple[str, str, str]:
    """解析服务器写入请求，返回地址、密钥和规范化 JSON。"""
    config_json = str(getattr(data, "config_json", "") or "").strip()
    if strict_json and not config_json:
        raise McpConfigError("请输入 MCP JSON 配置")
    if config_json:
        parsed = parse_mcp_config(config_json, require_single=strict_json)
        return parsed.url, parsed.api_key, json.dumps(parsed.config, ensure_ascii=False)

    url = str(getattr(data, "url", "") or "").strip()
    api_key = str(getattr(data, "api_key", "") or "").strip()
    if not url:
        raise McpConfigError("请提供有效的 MCP 服务地址")
    parsed = parse_mcp_config(
        json.dumps({"url": url, "api_key": api_key}),
        require_single=True,
    )
    return parsed.url, parsed.api_key, ""


def server_payload(server) -> dict:
    """生成不泄露鉴权密钥的 MCP 服务器列表项。"""
    return {
        "id": server.id,
        "name": server.name,
        "url": server.url,
        "config_json": editable_config_json(server),
        "api_key_prefix": mask_api_key(getattr(server, "api_key", "")),
        "api_key_configured": bool(getattr(server, "api_key", "")),
        "tools_cache": decode_tools_cache(
            getattr(server, "tools_cache", None),
            server.id,
        ),
        "status": server.status,
        "last_test_time": str(getattr(server, "last_test_time", "") or ""),
        "last_test_status": getattr(server, "last_test_status", 0) or 0,
        "last_error": getattr(server, "last_error", "") or "",
        "create_time": str(getattr(server, "create_time", "") or ""),
    }


async def find_mcp_server(db, model_cls, server_id: int):
    """按模型和主键查找 MCP 服务器，确保调用方的数据范围显式可见。"""
    result = await db.execute(select(model_cls).where(model_cls.id == server_id))
    return result.scalar_one_or_none()


async def ensure_unique_server_name(
    db,
    model_cls,
    name: str,
    server_id: int = 0,
) -> str:
    """在指定物理表内校验服务器名称并返回规范化名称。"""
    normalized = str(name or "").strip()
    if not normalized:
        raise McpConfigError("请输入 MCP 名称")
    if len(normalized) > 64:
        raise McpConfigError("MCP 名称不能超过 64 个字符")
    result = await db.execute(select(model_cls).where(model_cls.name == normalized))
    if any(row.id != server_id for row in result.scalars().all()):
        raise McpNameConflictError("同一配置范围内已存在同名 MCP 服务器")
    return normalized


def decode_tools_cache(raw: str | None, server_id: int) -> list:
    """安全读取工具缓存，损坏缓存按空列表处理。"""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("MCP 服务器 %s 的工具缓存损坏", server_id)
        return []
    return value if isinstance(value, list) else []


async def discover_tools(server) -> tuple[list, str | None]:
    """连接外部 MCP 服务发现工具，返回工具列表和错误描述。"""
    try:
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        headers = {"Authorization": f"Bearer {server.api_key}"} if server.api_key else None
        transport = StreamableHttpTransport(server.url, headers=headers)
        async with Client(transport) as client:
            tools = await asyncio.wait_for(client.list_tools(), timeout=10)
        return ([{
            "name": tool.name,
            "description": tool.description or "",
            "parameters": getattr(tool, "inputSchema", None)
            or {"type": "object", "properties": {}},
        } for tool in tools], None)
    except asyncio.TimeoutError:
        return [], "连接超时（10 秒）"
    except Exception as exc:  # 外部服务错误不能暴露调用栈
        logger.warning("外部 MCP 工具发现失败: %s", type(exc).__name__)
        return [], "连接失败，请检查地址和鉴权配置"


async def test_and_cache_tools(server, db) -> dict:
    """测试单个 MCP，统一持久化工具缓存和最近测试状态。"""
    tools, error = await discover_tools(server)
    server.last_test_time = china_now()
    if error:
        server.last_test_status = 2
        server.last_error = error
        server.tools_cache = None
        await db.commit()
        return {"success": False, "message": error}
    server.last_test_status = 1
    server.last_error = ""
    server.tools_cache = json.dumps(tools, ensure_ascii=False)
    await db.commit()
    return {
        "success": True,
        "message": f"连接成功，发现 {len(tools)} 个工具",
        "tools": tools,
    }


async def load_or_discover_tools(server, db) -> dict:
    """优先读取单个 MCP 的缓存，未命中时发现并写入该记录缓存。"""
    cached = decode_tools_cache(getattr(server, "tools_cache", None), server.id)
    if server.tools_cache and cached:
        return {"list": cached, "cached": True}
    tools, error = await discover_tools(server)
    if error:
        return {"list": [], "message": error, "cached": False}
    server.tools_cache = json.dumps(tools, ensure_ascii=False)
    await db.commit()
    return {"list": tools, "cached": False}


def mask_api_key(api_key: str | None) -> str:
    """返回短前缀，便于确认配置但不暴露完整密钥。"""
    value = str(api_key or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"
