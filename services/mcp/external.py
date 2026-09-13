# -*- coding: utf-8 -*-
"""AgentScope MCP 工具门面。

旧 AI ORM 已删除；外部 MCP 资源和工具均通过 AgentScope Storage 访问，系统工具
则只通过已登记的 MCP 注册表调用。
"""
from __future__ import annotations

import json
import logging
import asyncio
from typing import Any

from core.config import settings
from services.mcp.compact import compact_tool_list
from services.mcp.registry import get_tool_meta, list_registered_tool_declarations

PERMISSION_DENIED_TEXT = "没有权限调用该工具"
_EXT_PREFIX = "ext__"
_EXT_FARMER_PREFIX = "fext__"
EXTERNAL_CALL_TIMEOUT_SECONDS = 15


def _denied_text(name: str) -> str:
    return f"没有权限调用工具「{name}」，该工具已从可用列表中移除，请不要再次尝试。"


async def build_access_token(user_type: str, user_id: int, db) -> Any:
    from fastmcp.server.auth import AccessToken
    from services.mcp.auth import SUPER_ADMIN_ID

    scopes = []
    if user_type == "admin":
        from core.auth.rbac import get_admin_permissions
        scopes = await get_admin_permissions(user_id, db, use_cache=False)
    return AccessToken(
        token="internal-ai-agent", client_id=f"{user_type}:{user_id}", scopes=scopes,
        claims={"user_type": user_type, "user_id": user_id, "is_super": user_type == "admin" and user_id == SUPER_ADMIN_ID},
    )


def _allowed(meta: dict | None, token: Any) -> bool:
    from services.mcp.middleware import _capability_allowed
    return _capability_allowed(meta, token)


async def _system_declarations(token: Any) -> list[dict]:
    result = []
    for item in list_registered_tool_declarations():
        name = item.get("name", "")
        if _allowed(get_tool_meta(name), token):
            result.append({"name": name, "description": item.get("description", ""), "parameters": item.get("parameters") or {"type": "object", "properties": {}}})
    core_names = {item["name"] for item in result if (get_tool_meta(item["name"]) or {}).get("owner") == "core"}
    return [{"type": "function", "function": item} for item in compact_tool_list(result, core_names=core_names)]


def _record_declarations(records: list, prefix: str) -> list[dict]:
    result = []
    for record in records:
        if not record.enabled:
            continue
        try:
            tools = record.client._cached_tools or []
        except Exception:
            tools = []
        for tool in tools:
            result.append({"type": "function", "function": {"name": f"{prefix}{record.id}__{tool.name}", "description": f"[{record.display_name or record.client.name}] {tool.description or ''}", "parameters": getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}}})
    return result


async def list_cached_external_declarations(db: Any, model_cls: Any = None, prefix: str = _EXT_PREFIX) -> list[dict]:
    if model_cls is not None and model_cls.__name__ != "AgentScopeMcpRecord":
        from sqlalchemy import select
        try:
            rows = (await db.execute(select(model_cls).where(model_cls.status == 1))).scalars().all()
            from services.mcp.custom_server import decode_tools_cache
            result = []
            for row in rows:
                for tool in decode_tools_cache(row.tools_cache, row.id):
                    result.append({"type": "function", "function": {"name": f"{prefix}{row.id}__{tool.get('name', '')}", "description": tool.get("description", ""), "parameters": tool.get("parameters") or {"type": "object", "properties": {}}}})
            return result
        except Exception:
            return []
    from services.agentscope.runtime import agentscope_runtime
    records = await agentscope_runtime.storage.list_mcps("admin:1")
    return _record_declarations(records, prefix)


async def list_tool_declarations(token: Any, whitelist=None, db=None, system_tools_enabled=True) -> list[dict]:
    del whitelist
    result = await _system_declarations(token) if settings.MCP_ENABLED and system_tools_enabled else []
    if db is not None:
        from services.agentscope.runtime import agentscope_runtime
        user_type = (token.claims or {}).get("user_type", "admin")
        records = await agentscope_runtime.storage.list_mcps(f"{user_type}:{(token.claims or {}).get('user_id', 1)}")
        result.extend(_record_declarations(records, _EXT_FARMER_PREFIX if user_type == "farmer" else _EXT_PREFIX))
    functions = [item["function"] for item in result]
    core_names = {item["name"] for item in functions if (get_tool_meta(item["name"]) or {}).get("owner") == "core"}
    return [{"type": "function", "function": item} for item in compact_tool_list(functions, core_names=core_names)]


async def list_admin_external_declarations(db: Any) -> list[dict]:
    return await list_tool_declarations(await build_access_token("admin", 1, db), db=db, system_tools_enabled=False)


async def call_tool(name: str, arguments: dict[str, Any], token: Any, db=None, system_tools_enabled=True) -> tuple[str, bool]:
    if name.startswith((_EXT_PREFIX, _EXT_FARMER_PREFIX)):
        prefix = _EXT_FARMER_PREFIX if name.startswith(_EXT_FARMER_PREFIX) else _EXT_PREFIX
        if (token.claims or {}).get("user_type") != ("farmer" if prefix == _EXT_FARMER_PREFIX else "admin"):
            return _denied_text(name), True
        return await _call_external(name, arguments, token)
    if not settings.MCP_ENABLED or not system_tools_enabled or not _allowed(get_tool_meta(name), token):
        return _denied_text(name), True
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        from services.mcp.server import mcp
        result = await asyncio.wait_for(
            mcp.call_tool(name, arguments or {}),
            timeout=EXTERNAL_CALL_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logging.getLogger(__name__).warning("[MCP] 系统工具调用超时: tool=%s", name)
        return "工具执行超时，请稍后重试", True
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "[MCP] 系统工具调用失败: tool=%s error=%s", name, type(exc).__name__
        )
        return "工具暂不可用，请稍后重试", True
    finally:
        auth_context_var.reset(reset)
    from fastmcp.tools import ToolResult
    if isinstance(result, ToolResult):
        return "\n".join(item.text for item in result.content if hasattr(item, "text")), result.is_error
    return _serialize(result), False


async def _call_external(name: str, arguments: dict[str, Any], token: Any) -> tuple[str, bool]:
    prefix = _EXT_FARMER_PREFIX if name.startswith(_EXT_FARMER_PREFIX) else _EXT_PREFIX
    raw = name[len(prefix):].split("__", 1)
    if len(raw) != 2:
        return _denied_text(name), True
    from services.agentscope.runtime import agentscope_runtime
    record = await agentscope_runtime.storage.get_mcp(f"{(token.claims or {}).get('user_type')}:{(token.claims or {}).get('user_id')}", raw[0])
    if record is None or not record.enabled:
        return "MCP 不存在或已停用", True
    try:
        tool = await record.client.get_tool(raw[1])
        from services.mcp.compact import compact_tool_result
        result = await asyncio.wait_for(
            tool(**(arguments or {})), timeout=EXTERNAL_CALL_TIMEOUT_SECONDS
        )
        compacted = compact_tool_result(result)
        return _serialize(compacted), bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logging.getLogger(__name__).warning("[MCP] 外部工具调用超时: tool=%s", name)
        return "外部 MCP 工具执行超时，请稍后重试", True
    except Exception as exc:
        logging.getLogger(__name__).warning("[MCP] 外部工具执行失败: client=%s tool=%s error=%s",
                                            token.client_id, name, type(exc).__name__)
        return "外部 MCP 工具暂不可用，请稍后重试", True


async def call_cached_external_tool(name: str, arguments: dict[str, Any], model_cls: Any = None, prefix: str = _EXT_PREFIX) -> tuple[str, bool]:
    if model_cls is not None:
        try:
            raw = name[len(prefix):].split("__", 1)
            server_id, tool_name = int(raw[0]), raw[1]
            from sqlalchemy import select
            from core.db.base import async_session_factory
            async with async_session_factory() as db:
                row = (await db.execute(select(model_cls).where(model_cls.id == server_id, model_cls.status == 1))).scalar_one_or_none()
            if row is None:
                return _denied_text(name), True
            from fastmcp import Client
            from fastmcp.client.transports import StreamableHttpTransport
            headers = {"Authorization": f"Bearer {row.api_key}"} if row.api_key else None
            async with asyncio.timeout(EXTERNAL_CALL_TIMEOUT_SECONDS):
                async with Client(StreamableHttpTransport(row.url, headers=headers)) as client:
                    result = await client.call_tool(tool_name, arguments or {})
            from services.mcp.compact import compact_tool_result
            return _serialize(compact_tool_result(result)), bool(
                getattr(result, "isError", False)
                or getattr(result, "is_error", False)
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logging.getLogger(__name__).warning(
                "[MCP] 缓存外部工具调用超时: tool=%s", name
            )
            return "外部 MCP 工具执行超时，请稍后重试", True
        except Exception as exc:
            logging.getLogger(__name__).warning("[MCP] 缓存外部工具执行失败: tool=%s error=%s",
                                                name, type(exc).__name__)
            return "外部 MCP 工具暂不可用，请稍后重试", True
    return await _call_external(name, arguments, type("Token", (), {"claims": {"user_type": "admin", "user_id": 1}})())


async def list_cached_external_declarations_for_user(db: Any, user_id: str, prefix: str = _EXT_PREFIX) -> list[dict]:
    from services.agentscope.runtime import agentscope_runtime
    return _record_declarations(await agentscope_runtime.storage.list_mcps(user_id), prefix)


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    return json.dumps(value, ensure_ascii=False, default=str)
