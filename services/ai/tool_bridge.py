# -*- coding: utf-8 -*-
"""
MCP 工具桥 — AI 对话复用系统 MCP 工具注册表 + 外部 MCP 服务器

职责:
- 系统工具声明导出: 从 FastMCP 单例进程内读取工具 JSON Schema，转为 OpenAI function
  声明；按登录身份过滤（复用 core/mcp/middleware.py 的 _tool_allowed 判定）
- 外部工具声明导出: 管理员使用 AiMcpServerModel；农户使用
  AiFarmerMcpServerModel 与 FarmerMcpCredentialModel 的个人凭据关联，使用个人
  tools_cache 中缓存的工具列表（含参数 Schema），转为 OpenAI function 声明
  （工具名加前缀防冲突）
- 进程内调用（系统工具）: 通过 SDK auth 上下文变量注入与 ApiKeyVerifier 同构的
  AccessToken，保证工具 handler 内 get_access_token() 正常取到身份，随后直调 Tool.run()
- 远程调用（外部工具）: 通过 fastmcp.Client + StreamableHttpTransport 连接外部
  MCP 服务器执行工具，30 秒超时
- 越权双保险: 声明列表按权限过滤（无权工具默认不暴露给模型）；执行前二次校验，
  越权时不抛异常，返回含工具名的拒绝文案由 Agent 循环回填给模型，并标记
  denied=True 以触发被拒工具从后续回合移除

外部工具命名约定:
- 管理员: ext__{server_id}__{original_name}
- 农户端: fext__{server_id}__{original_name}
  前缀区分管理员/农户端，避免 ID 冲突，call_tool 据此前缀路由到对应表
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from core.config import settings

logger = logging.getLogger(__name__)

# 越权调用时回填给模型的统一提示（兼容旧引用，新逻辑使用 _denied_text 构造完整文案）
PERMISSION_DENIED_TEXT = "没有权限调用该工具"


def _denied_text(tool_name: str) -> str:
    """构造越权拒绝文案（含工具名，引导模型停止重试）

    Agent 循环会在 denied=True 时将工具名加入 denied_tools 集合，
    后续回合不再向模型暴露该工具，此文案同时引导模型停止重试。
    """
    return f"没有权限调用工具「{tool_name}」，该工具已从可用列表中移除，请不要再次尝试。"


# 外部 MCP 工具名前缀
_EXT_PREFIX = "ext__"           # 管理员外部工具前缀（ext__{server_id}__{original_name}）
_EXT_FARMER_PREFIX = "fext__"   # 农户端外部工具前缀（fext__{server_id}__{original_name}）
# 外部 MCP 服务器连接超时（秒）
_EXT_TIMEOUT = 30


async def build_access_token(user_type: str, user_id: int, db) -> Any:
    """
    构造与 ApiKeyVerifier 同构的 AccessToken（进程内身份注入用）

    - client_id: "admin:3" / "farmer:7"
    - scopes:    管理员为 RBAC 权限 code 列表；农户为空列表
    - claims:    {"user_type", "user_id", "is_super"}
    """
    from fastmcp.server.auth import AccessToken
    from services.mcp.auth import SUPER_ADMIN_ID

    scopes: List[str] = []
    is_super = False
    if user_type == "admin":
        from core.auth.rbac import get_admin_permissions
        is_super = user_id == SUPER_ADMIN_ID
        scopes = await get_admin_permissions(user_id, db)

    return AccessToken(
        token="internal-ai-agent",  # 进程内调用占位，不经网络鉴权
        client_id=f"{user_type}:{user_id}",
        scopes=scopes,
        claims={"user_type": user_type, "user_id": user_id, "is_super": is_super},
    )


async def list_tool_declarations(token: Any,
                                 whitelist: Optional[List[str]] = None,
                                 db: Any = None,
                                 system_tools_enabled: Optional[bool] = None
                                 ) -> List[Dict[str, Any]]:
    """
    导出当前身份可用的全部工具（系统 MCP + 外部 MCP 服务器）为 OpenAI function 声明

    参数:
        token: build_access_token 构造的 AccessToken
        whitelist: 已废弃（保留参数兼容旧调用），始终为 None 不限制
        db: 数据库会话（传入时合并外部 MCP 服务器工具）
        system_tools_enabled: 数据库登记的系统 MCP 开关；未传入时从 db 读取
    返回:
        [{"type": "function", "function": {"name", "description", "parameters"}}]
    """
    declarations: List[Dict[str, Any]] = []

    if system_tools_enabled is None:
        if db is not None:
            from services.ai.service import get_ai_settings
            system_tools_enabled = (await get_ai_settings(db))["system_tools_enabled"]
        else:
            # 兼容无数据库调用方（如 MCP 权限单测）；业务请求始终传入 db。
            system_tools_enabled = True

    # ---- 系统内置 MCP 工具 ----
    if settings.MCP_ENABLED and system_tools_enabled is True:
        declarations.extend(await _list_system_declarations(token))

    # ---- 外部 MCP 服务器工具 ----
    if db is not None:
        declarations.extend(await _list_external_declarations(db, token))

    return declarations


async def _list_system_declarations(token: Any) -> List[Dict[str, Any]]:
    """导出系统内置 MCP 工具为 OpenAI function 声明（按身份过滤）"""
    from services.mcp.server import mcp
    from services.mcp.registry import get_tool_meta
    from services.mcp.middleware import _tool_allowed

    declarations: List[Dict[str, Any]] = []
    try:
        tools = await mcp.list_tools(run_middleware=False)
    except Exception:
        logger.exception("[工具桥] 读取系统 MCP 工具列表失败")
        return []

    for tool in tools:
        meta = get_tool_meta(tool.name)
        # 仅暴露本注册表管理且当前身份有权的工具（FastMCP 内建工具不暴露给模型）
        if meta is None or not _tool_allowed(meta, token):
            continue
        declarations.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            },
        })
    return declarations


async def _list_external_declarations(db: Any,
                                    token: Any) -> List[Dict[str, Any]]:
    """导出外部 MCP 服务器工具为 OpenAI function 声明（按身份查询对应表）

    管理员身份查询 AiMcpServerModel，工具名加 ext__ 前缀；
    农户身份查询 AiFarmerMcpServerModel，工具名加 fext__ 前缀。
    从 DB 读取已启用服务器的 tools_cache 缓存，避免每次对话都连接外部服务器。
    缓存为空时跳过该服务器（用户需先在设置页点击「测试」触发工具发现）。
    """
    from sqlalchemy import select
    from core.db.ai import AiMcpServerModel, AiFarmerMcpServerModel

    user_type = (token.claims or {}).get("user_type", "") if token else ""
    if user_type == "farmer":
        model_cls = AiFarmerMcpServerModel
        prefix = _EXT_FARMER_PREFIX
    else:
        model_cls = AiMcpServerModel
        prefix = _EXT_PREFIX

    try:
        if user_type == "farmer":
            from core.db.ai import FarmerMcpCredentialModel
            result = await db.execute(
                select(model_cls, FarmerMcpCredentialModel)
                .join(
                    FarmerMcpCredentialModel,
                    FarmerMcpCredentialModel.server_id == model_cls.id,
                )
                .where(
                    model_cls.status == 1,
                    FarmerMcpCredentialModel.farmer_id == (token.claims or {}).get("user_id"),
                    FarmerMcpCredentialModel.status == 1,
                    FarmerMcpCredentialModel.api_key != "",
                )
            )
            servers = result.all()
        else:
            return await list_cached_external_declarations(db, model_cls, prefix)
    except Exception:
        logger.exception("[工具桥] 读取外部 MCP 服务器列表失败")
        return []

    return _build_cached_external_declarations(servers, prefix)


def _build_cached_external_declarations(
    servers: list[tuple[Any, Any]],
    prefix: str,
) -> List[Dict[str, Any]]:
    """将服务器及可选个人凭据的缓存转换为工具声明。"""
    declarations: List[Dict[str, Any]] = []

    for server, credential in servers:
        tools_cache = credential.tools_cache if credential else server.tools_cache
        if not tools_cache:
            continue
        from services.mcp.custom_server import decode_tools_cache
        tools_data = decode_tools_cache(tools_cache, server.id)
        for tool_info in tools_data:
            if not isinstance(tool_info, dict):
                continue
            original_name = tool_info.get("name", "")
            if not original_name:
                continue
            prefixed_name = f"{prefix}{server.id}__{original_name}"
            # 描述中追加服务器名，帮助模型区分工具来源
            desc = tool_info.get("description", "") or ""
            desc_with_source = f"[{server.name}] {desc}" if desc else f"[{server.name}]"
            declarations.append({
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": desc_with_source,
                    "parameters": tool_info.get("parameters")
                    or {"type": "object", "properties": {}},
                },
            })
    return declarations


async def list_cached_external_declarations(
    db: Any,
    model_cls: Any,
    prefix: str = _EXT_PREFIX,
) -> List[Dict[str, Any]]:
    """从指定 MCP 服务器模型读取已启用记录的缓存工具声明。"""
    from sqlalchemy import select

    try:
        result = await db.execute(select(model_cls).where(model_cls.status == 1))
        servers = [(server, None) for server in result.scalars().all()]
    except Exception:
        logger.exception("[工具桥] 读取指定范围的外部 MCP 服务器失败")
        return []
    return _build_cached_external_declarations(servers, prefix)


async def list_admin_external_declarations(db: Any) -> List[Dict[str, Any]]:
    """列出系统管理员已启用外部 MCP 的缓存工具。"""
    from core.db.ai import AiMcpServerModel

    return await list_cached_external_declarations(
        db,
        AiMcpServerModel,
        _EXT_PREFIX,
    )


def _parse_external_tool_name(name: str) -> Optional[Tuple[str, int, str]]:
    """解析外部工具名，返回 (prefix, server_id, original_name)；非外部工具返回 None

    支持 ext__（管理员）和 fext__（农户端）两种前缀，
    call_tool 据前缀路由到对应 ORM 表。
    """
    for prefix in (_EXT_PREFIX, _EXT_FARMER_PREFIX):
        if name.startswith(prefix):
            remainder = name[len(prefix):]
            parts = remainder.split("__", 1)
            if len(parts) != 2:
                return None
            try:
                return prefix, int(parts[0]), parts[1]
            except ValueError:
                return None
    return None


async def call_admin_external_tool(name: str,
                                   arguments: Dict[str, Any]) -> Tuple[str, bool]:
    """调用系统管理员外部 MCP 的已缓存工具。"""
    from core.db.ai import AiMcpServerModel

    return await call_cached_external_tool(
        name,
        arguments,
        AiMcpServerModel,
        _EXT_PREFIX,
    )


async def call_cached_external_tool(
    name: str,
    arguments: Dict[str, Any],
    model_cls: Any,
    prefix: str = _EXT_PREFIX,
) -> Tuple[str, bool]:
    """调用指定 MCP 服务器模型中的缓存白名单工具。"""
    if not name.startswith(prefix):
        return _denied_text(name), True
    parts = name[len(prefix):].split("__", 1)
    if len(parts) != 2 or not parts[1]:
        return _denied_text(name), True
    try:
        server_id = int(parts[0])
    except ValueError:
        return _denied_text(name), True
    return await _call_external_tool(
        server_id,
        parts[1],
        arguments,
        model_cls,
        name,
        token=None,
        require_cached=True,
    )


async def call_tool(name: str, arguments: Dict[str, Any],
                    token: Any, db: Any = None,
                    system_tools_enabled: Optional[bool] = None
                    ) -> Tuple[str, bool]:
    """
    调用工具（自动路由系统 MCP 或外部 MCP 服务器）

    返回:
        (结果文本, 是否越权被拒)；越权时返回含工具名的拒绝文案且不抛异常，
        执行异常时返回错误描述文本（同样回填给模型，保证对话不中断）
    """
    # ---- 外部 MCP 服务器工具 ----
    ext_info = _parse_external_tool_name(name)
    if ext_info is not None:
        prefix, server_id, original_name = ext_info
        user_type = (token.claims or {}).get("user_type", "") if token else ""
        if user_type not in ("farmer", "admin") or (
                user_type == "farmer" and prefix != _EXT_FARMER_PREFIX) or (
                user_type == "admin" and prefix != _EXT_PREFIX):
            logger.warning("[工具桥] 外部工具身份前缀不匹配: user_type=%s tool=%s",
                           user_type, name)
            return _denied_text(name), True
        # 按前缀路由到对应 ORM 表
        if prefix == _EXT_FARMER_PREFIX:
            from core.db.ai import AiFarmerMcpServerModel
            model_cls = AiFarmerMcpServerModel
        else:
            from core.db.ai import AiMcpServerModel
            model_cls = AiMcpServerModel
        return await _call_external_tool(server_id, original_name, arguments,
                                         model_cls, name, token)

    # ---- 系统内置 MCP 工具 ----
    if system_tools_enabled is None:
        if db is not None:
            from services.ai.service import get_ai_settings
            system_tools_enabled = (await get_ai_settings(db))["system_tools_enabled"]
        else:
            system_tools_enabled = True
    if not settings.MCP_ENABLED or not system_tools_enabled:
        return "系统 MCP 服务未启用", True

    from services.mcp.registry import get_tool_meta
    from services.mcp.middleware import _tool_allowed

    meta = get_tool_meta(name)
    # 越权双保险第二道：执行前强制校验（不依赖声明列表隐藏）
    if meta is None or not _tool_allowed(meta, token):
        logger.warning("[工具桥] 越权调用被拒: client=%s tool=%s",
                       getattr(token, "client_id", "?"), name)
        return _denied_text(name), True

    try:
        result = await _run_tool_with_identity(name, arguments, token)
        return result, False
    except Exception as e:
        logger.exception("[工具桥] 工具执行失败: %s", name)
        return f"工具执行失败：{e}", False


async def _call_external_tool(server_id: int, tool_name: str,
                              arguments: Dict[str, Any],
                              model_cls: Any,
                              full_name: str,
                              token: Any,
                              require_cached: bool = False) -> Tuple[str, bool]:
    """连接外部 MCP 服务器执行工具调用（30 秒超时）

    :param model_cls: ORM 模型类（AiMcpServerModel 或 AiFarmerMcpServerModel）
    :param full_name: 带前缀的完整工具名（用于拒绝文案）
    """
    from sqlalchemy import select
    from core.db.base import async_session_factory

    try:
        async with async_session_factory() as db:
            server = (await db.execute(
                select(model_cls).where(
                    model_cls.id == server_id,
                    model_cls.status == 1,
                )
            )).scalar_one_or_none()
        if not server:
            return f"外部 MCP 服务器不存在或已停用（ID={server_id}）", require_cached

        if require_cached:
            from services.mcp.custom_server import decode_tools_cache
            cached_tools = decode_tools_cache(server.tools_cache, server.id)
            allowed_names = {
                item.get("name") for item in cached_tools if isinstance(item, dict)
            }
            if tool_name not in allowed_names:
                logger.warning(
                    "[工具桥] 小智调用未缓存的外部工具: server=%s tool=%s",
                    server_id,
                    tool_name,
                )
                return _denied_text(full_name), True

        api_key = server.api_key
        farmer_id = None
        if model_cls.__name__ == "AiFarmerMcpServerModel":
            from core.db.ai import FarmerMcpCredentialModel
            farmer_id = (token.claims or {}).get("user_id") if token else None
            if not farmer_id:
                return "未获取到农户身份，无法调用外部 MCP", True
            async with async_session_factory() as db:
                credential = (await db.execute(
                    select(FarmerMcpCredentialModel).where(
                        FarmerMcpCredentialModel.server_id == server_id,
                        FarmerMcpCredentialModel.farmer_id == farmer_id,
                        FarmerMcpCredentialModel.status == 1,
                        FarmerMcpCredentialModel.api_key != "",
                    )
                )).scalar_one_or_none()
            if not credential:
                return "尚未配置该外部 MCP 的个人凭据，请先在个人中心完成配置", True
            api_key = credential.api_key
            area_error = await _validate_farmer_area_arguments(farmer_id, arguments)
            if area_error:
                return area_error, True

        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        transport = StreamableHttpTransport(server.url, headers=headers)
        async with Client(transport) as client:
            result = await asyncio.wait_for(
                client.call_tool(tool_name, arguments or {}),
                timeout=_EXT_TIMEOUT,
            )
        return _serialize_result(result), False
    except asyncio.TimeoutError:
        logger.warning("[工具桥] 外部工具调用超时: farmer=%s server=%s tool=%s",
                       farmer_id, server_id, tool_name)
        return f"外部工具调用超时（{_EXT_TIMEOUT}秒）", False
    except Exception as e:
        logger.exception("[工具桥] 外部工具执行失败: farmer=%s server=%s tool=%s",
                         farmer_id, server_id, tool_name)
        return f"外部工具执行失败：{e}", False


async def _validate_farmer_area_arguments(farmer_id: int,
                                          arguments: Dict[str, Any]) -> str:
    """外部工具显式携带本地产区 ID 时校验当前农户绑定关系。"""
    area_values = []
    if arguments and arguments.get("area_id") is not None:
        area_values.append(arguments.get("area_id"))
    if arguments and isinstance(arguments.get("area_ids"), list):
        area_values.extend(arguments["area_ids"])
    if not area_values:
        return ""

    from sqlalchemy import select
    from core.db.base import async_session_factory
    from core.db.production_area import AreaFarmer
    async with async_session_factory() as db:
        for raw_area_id in area_values:
            try:
                area_id = int(raw_area_id)
            except (TypeError, ValueError):
                return f"外部工具产区参数非法: {raw_area_id}"
            bound = (await db.execute(
                select(AreaFarmer.id).where(
                    AreaFarmer.farmer_id == farmer_id,
                    AreaFarmer.area_id == area_id,
                ).limit(1)
            )).scalar_one_or_none()
            if bound is None:
                return f"您未绑定产区 {area_id}，无权调用外部 MCP 查询该产区"
    return ""


async def _run_tool_with_identity(name: str, arguments: Dict[str, Any], token: Any) -> str:
    """注入 SDK auth 上下文后直调工具，返回序列化结果文本"""
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from services.mcp.server import mcp

    tool = await mcp.get_tool(name)
    if tool is None:
        return f"工具不存在：{name}"

    # 注入身份上下文（工具 handler 内 get_access_token() 由此取到身份）
    reset_token = auth_context_var.set(AuthenticatedUser(token))
    try:
        result = await tool.run(arguments or {})
    finally:
        auth_context_var.reset(reset_token)
    return _serialize_result(result)


def _serialize_result(result: Any) -> str:
    """ToolResult → 文本（优先结构化结果 JSON，回落拼接文本内容块）"""
    structured = getattr(result, "structured_content", None)
    if structured:
        if (isinstance(structured, dict) and set(structured) == {"result"}
                and isinstance(structured["result"], str)):
            return structured["result"]
        return json.dumps(structured, ensure_ascii=False)
    parts: List[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts) if parts else "（工具无返回内容）"
