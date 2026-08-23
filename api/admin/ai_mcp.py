# -*- coding: utf-8 -*-
"""AI 设置中的系统工具预览和管理员外部 MCP 兼容接口。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok
from services.mcp.custom_server import (
    McpConfigError,
    McpNameConflictError,
    decode_tools_cache,
    discover_tools,
    ensure_unique_server_name,
    find_mcp_server,
    load_or_discover_tools,
    parse_mcp_config,
    parse_server_request,
    server_payload,
    test_and_cache_tools,
)

router = APIRouter(prefix="/api/admin/v1/ai/setting", tags=["AI设置"])
_PERM = Depends(require_permission("ai:setting"))


@router.get("/mcp-tools", dependencies=[_PERM])
async def list_mcp_tools(_: None = Depends(check_admin)):
    """系统 MCP 工具列表预览。"""
    from core.config import settings as app_settings
    from core.db.base import async_session_factory
    from services.ai.service import get_ai_settings
    async with async_session_factory() as db:
        ai_settings = await get_ai_settings(db)
    env_enabled = bool(app_settings.MCP_ENABLED)
    system_tools_enabled = bool(ai_settings["system_tools_enabled"])
    if not env_enabled or not system_tools_enabled:
        return ok({
            "list": [], "enabled": False, "env_enabled": env_enabled,
            "system_tools_enabled": system_tools_enabled,
        })
    from services.mcp.server import mcp
    from services.mcp.registry import get_tool_meta
    tools = await mcp.list_tools(run_middleware=False)
    result = []
    for tool in tools:
        meta = get_tool_meta(tool.name) or {}
        result.append({
            "name": tool.name,
            "description": tool.description or "",
            "owner": meta.get("owner", ""),
            "audience": meta.get("audience", ""),
            "permission_code": meta.get("permission_code") or "",
        })
    return ok({
        "list": result, "enabled": True, "env_enabled": True,
        "system_tools_enabled": True,
    })


class McpServerUpsert(BaseModel):
    """外部 MCP 服务器创建/更新请求。"""

    name: str
    url: str = ""
    api_key: str = ""
    config_json: str = ""
    status: int = 1
    clear_api_key: bool = False


def _parse_mcp_config(config_json: str) -> tuple[str, str]:
    """旧模块兼容函数：多服务配置取首个服务。"""
    try:
        parsed = parse_mcp_config(config_json, require_single=False)
    except McpConfigError:
        return "", ""
    return parsed.url, parsed.api_key


def _decode_tools_cache(raw: str | None, server_id: int) -> list:
    """保留旧测试和农户端使用的缓存解析门面。"""
    return decode_tools_cache(raw, server_id)


def _parse_request(data: McpServerUpsert, *, strict_json: bool = False) -> tuple[str, str, str]:
    """解析请求并返回 URL、密钥和规范化 JSON。"""
    try:
        return parse_server_request(data, strict_json=strict_json)
    except McpConfigError as exc:
        raise HTTPException(
            status_code=422 if strict_json else 400,
            detail=str(exc),
        ) from None


def _server_payload(server) -> dict:
    """生成列表响应，所有敏感信息均脱敏。"""
    return server_payload(server)


async def _find_server(db, server_id: int):
    from core.db.ai import AiMcpServerModel
    return await find_mcp_server(db, AiMcpServerModel, server_id)


async def _ensure_unique_name(db, name: str, server_id: int = 0):
    from core.db.ai import AiMcpServerModel
    try:
        return await ensure_unique_server_name(db, AiMcpServerModel, name, server_id)
    except McpNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except McpConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/mcp-servers", dependencies=[_PERM])
async def list_mcp_servers(_: None = Depends(check_admin)):
    """旧接口：列出管理员外部 MCP 服务器。"""
    from core.db.base import async_session_factory
    from core.db.ai import AiMcpServerModel
    async with async_session_factory() as db:
        rows = (await db.execute(select(AiMcpServerModel).order_by(AiMcpServerModel.id.asc()))).scalars().all()
    return ok({"list": [_server_payload(row) for row in rows]})


@router.post("/mcp-servers", dependencies=[_PERM])
async def create_mcp_server(data: McpServerUpsert, _: None = Depends(check_admin)):
    """旧接口：添加管理员外部 MCP。"""
    from core.db.base import async_session_factory
    from core.db.ai import AiMcpServerModel
    url, api_key, normalized = _parse_request(data)
    async with async_session_factory() as db:
        await _ensure_unique_name(db, data.name.strip())
        server = AiMcpServerModel(
            name=data.name.strip(), url=url, api_key=api_key,
            config_json=normalized or None, status=1 if data.status == 1 else 2,
        )
        db.add(server)
        await db.commit()
        return ok({"id": server.id}, msg="服务器已添加")


@router.put("/mcp-servers/{server_id}", dependencies=[_PERM])
async def update_mcp_server(server_id: int, data: McpServerUpsert, _: None = Depends(check_admin)):
    """旧接口：更新管理员外部 MCP。"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        server = await _find_server(db, server_id)
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        url, api_key, normalized = _parse_request(data)
        await _ensure_unique_name(db, data.name.strip(), server_id)
        server.name, server.url = data.name.strip(), url
        if data.clear_api_key:
            server.api_key = ""
        elif api_key:
            server.api_key = api_key
        server.config_json = normalized or None
        server.status = 1 if data.status == 1 else 2
        server.tools_cache = None
        server.last_test_status, server.last_error = 0, ""
        await db.commit()
    return ok(msg="服务器已更新")


@router.delete("/mcp-servers/{server_id}", dependencies=[_PERM])
async def delete_mcp_server(server_id: int, _: None = Depends(check_admin)):
    """旧接口：删除管理员外部 MCP。"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        server = await _find_server(db, server_id)
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        await db.delete(server)
        await db.commit()
    return ok(msg="服务器已删除")


@router.post("/mcp-servers/{server_id}/test", dependencies=[_PERM])
async def test_mcp_server(server_id: int, _: None = Depends(check_admin)):
    """旧接口：测试并写入工具缓存与测试状态。"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        server = await _find_server(db, server_id)
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        result = await test_and_cache_tools(server, db)
    return ok(result)


@router.get("/mcp-servers/{server_id}/tools", dependencies=[_PERM])
async def list_mcp_server_tools(server_id: int, _: None = Depends(check_admin)):
    """旧接口：读取缓存，未命中时实时发现工具。"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        server = await _find_server(db, server_id)
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        result = await load_or_discover_tools(server, db)
    return ok(result)


# 由旧农户端模块导入的共享发现门面。
_discover_tools = discover_tools
