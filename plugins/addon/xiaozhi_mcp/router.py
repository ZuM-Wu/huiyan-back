# -*- coding: utf-8 -*-
"""小智 AI MCP 管理员端配置与运行监控接口。"""
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.config_service import delete_config, get_config, set_config
from core.log.active_log import active_log
from core.response import ok
from plugins.addon.xiaozhi_mcp.models import XiaozhiMcpServerModel
from plugins.addon.xiaozhi_mcp.schemas import (
    XiaozhiConfigUpdate,
    XiaozhiMcpServerUpsert as McpServerUpsert,
)
from plugins.addon.xiaozhi_mcp.service import (
    BridgeLimitError,
    xiaozhi_bridge,
)
from services.mcp.custom_server import (
    McpConfigError,
    McpNameConflictError,
    ensure_unique_server_name,
    find_mcp_server,
    load_or_discover_tools,
    parse_server_request,
    server_payload,
    test_and_cache_tools,
)

PREFIX = "xiaozhi_mcp"
CONFIG_KEYS = {
    "enabled": f"{PREFIX}.enabled",
    "endpoint_url": f"{PREFIX}.endpoint_url",
}

router = APIRouter(
    prefix="/api/admin/v1/xiaozhi-mcp",
    tags=["小智 AI MCP"],
    dependencies=[Depends(check_admin)],
)


def _parse_request(data: McpServerUpsert, *, strict_json: bool = False) -> tuple[str, str, str]:
    """将共享配置校验错误转换为小智接口的 422 响应。"""
    try:
        return parse_server_request(data, strict_json=strict_json)
    except McpConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _server_payload(server) -> dict:
    """返回小智专属服务器的脱敏列表项。"""
    return server_payload(server)


async def _find_server(db, server_id: int):
    """仅在小智插件物理表内查找服务器。"""
    return await find_mcp_server(db, XiaozhiMcpServerModel, server_id)


async def _ensure_unique_name(db, name: str, server_id: int = 0) -> str:
    """仅在小智插件物理表内校验名称。"""
    try:
        return await ensure_unique_server_name(
            db,
            XiaozhiMcpServerModel,
            name,
            server_id,
        )
    except McpNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except McpConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


async def _raw_config() -> dict[str, str]:
    values = {}
    for name, key in CONFIG_KEYS.items():
        values[name] = await get_config(key) or ""
    return values



def _validate_endpoint(endpoint_url: str) -> None:
    parsed = urlsplit(endpoint_url)
    try:
        invalid_port = parsed.port is not None and not 1 <= parsed.port <= 65535
    except ValueError:
        invalid_port = True
    if parsed.scheme != "wss" or not parsed.hostname or parsed.fragment or invalid_port:
        raise HTTPException(status_code=422, detail="接入点必须是有效的 wss:// 地址")


def _config_payload(raw: dict[str, str]) -> dict:
    return {
        "enabled": raw["enabled"] == "1",
        "endpoint_url": raw["endpoint_url"],
        "endpoint_configured": bool(raw["endpoint_url"]),
    }


@router.get("/config", dependencies=[Depends(require_permission("xiaozhi_mcp:list"))])
async def get_xiaozhi_config():
    """获取小智插件配置，返回完整 WSS 接入点供管理员编辑。"""
    return ok(_config_payload(await _raw_config()))


@router.put("/config", dependencies=[Depends(require_permission("xiaozhi_mcp:manage"))])
async def update_xiaozhi_config(data: XiaozhiConfigUpdate, request: Request):
    """保存配置并根据启用状态停止或重建常驻连接。"""
    current = await _raw_config()
    endpoint = (data.endpoint_url or "").strip()
    resulting = dict(current)

    if data.clear_endpoint_url:
        resulting["endpoint_url"] = ""
    elif endpoint:
        _validate_endpoint(endpoint)
        resulting["endpoint_url"] = endpoint
    if data.enabled is not None:
        resulting["enabled"] = "1" if data.enabled else "0"
    if resulting["enabled"] == "1" and not resulting["endpoint_url"]:
        raise HTTPException(status_code=422, detail="启用前必须配置小智 WSS 接入点")

    await _save_changes(current, resulting)
    if resulting["enabled"] == "1":
        await xiaozhi_bridge.restart()
    else:
        await xiaozhi_bridge.stop("插件配置为停用")
    await active_log("更新小智 AI MCP 配置", "xiaozhi_mcp_config", request=request)
    return ok(_config_payload(resulting), msg="配置已保存")


async def _save_changes(current: dict[str, str], resulting: dict[str, str]) -> None:
    for name, value in resulting.items():
        if value == current[name]:
            continue
        key = CONFIG_KEYS[name]
        if value or name == "enabled":
            await set_config(key, value)
        else:
            await delete_config(key)


@router.get("/status", dependencies=[Depends(require_permission("xiaozhi_mcp:list"))])
async def get_xiaozhi_status():
    """获取连接状态和最近一次工具统计。"""
    return ok(xiaozhi_bridge.status())


@router.get("/tools", dependencies=[Depends(require_permission("xiaozhi_mcp:list"))])
async def get_xiaozhi_tools():
    """预览已启用小智专属 MCP 的缓存工具，不依赖连接配置。"""
    return ok(await xiaozhi_bridge.preview_tools())


@router.post("/test", dependencies=[Depends(require_permission("xiaozhi_mcp:manage"))])
async def test_xiaozhi_connection():
    """执行一次有超时限制的临时连接、初始化和工具发现。"""
    try:
        return ok(await xiaozhi_bridge.test_connection(), msg="连接测试通过")
    except (BridgeLimitError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@router.post("/reconnect", dependencies=[Depends(require_permission("xiaozhi_mcp:manage"))])
async def reconnect_xiaozhi(request: Request):
    """回收旧任务并按最新配置重连。"""
    started = await xiaozhi_bridge.restart()
    await active_log("重连小智 AI MCP", "xiaozhi_mcp_runtime", request=request)
    return ok({"started": started}, msg="重连任务已提交" if started else "当前配置未启动连接")


@router.post("/disconnect", dependencies=[Depends(require_permission("xiaozhi_mcp:manage"))])
async def disconnect_xiaozhi(request: Request):
    """手动断开当前常驻连接但保留配置。"""
    await xiaozhi_bridge.stop("管理员手动断开")
    await active_log("断开小智 AI MCP", "xiaozhi_mcp_runtime", request=request)
    return ok(msg="连接已断开")


# ==================================================================
# 小智专属外部 MCP 配置接口
# ==================================================================

@router.get("/custom-servers", dependencies=[Depends(require_permission("xiaozhi_mcp:list"))])
async def list_xiaozhi_custom_servers():
    """列出小智可用的远程外部 MCP，每条记录独立管理。"""
    from core.db.base import async_session_factory
    from sqlalchemy import select

    async with async_session_factory() as db:
        rows = (await db.execute(
            select(XiaozhiMcpServerModel).order_by(XiaozhiMcpServerModel.id.asc())
        )).scalars().all()
    return ok({"list": [_server_payload(row) for row in rows]})


@router.post("/custom-servers", dependencies=[Depends(require_permission("xiaozhi_mcp:manage"))])
async def create_xiaozhi_custom_server(data: McpServerUpsert):
    """创建一条只包含一个远程 MCP 服务的配置。"""
    from core.db.base import async_session_factory
    url, api_key, normalized = _parse_request(data, strict_json=True)
    async with async_session_factory() as db:
        name = await _ensure_unique_name(db, data.name)
        server = XiaozhiMcpServerModel(
            name=name, url=url, api_key=api_key,
            config_json=normalized or None, status=1 if data.status == 1 else 2,
        )
        db.add(server)
        await db.commit()
    return ok({"id": server.id}, msg="MCP 配置已保存")


@router.put("/custom-servers/{server_id}", dependencies=[Depends(require_permission("xiaozhi_mcp:manage"))])
async def update_xiaozhi_custom_server(server_id: int, data: McpServerUpsert):
    """更新单条小智外部 MCP，并清理旧工具缓存。"""
    from core.db.base import async_session_factory

    async with async_session_factory() as db:
        server = await _find_server(db, server_id)
        if not server:
            raise HTTPException(status_code=404, detail="MCP 服务器不存在")
        url, api_key, normalized = _parse_request(data, strict_json=True)
        name = await _ensure_unique_name(db, data.name, server_id)
        server.name, server.url = name, url
        if data.clear_api_key:
            server.api_key = ""
        elif api_key:
            server.api_key = api_key
        server.config_json = normalized or None
        server.status = 1 if data.status == 1 else 2
        server.tools_cache = None
        server.last_test_status, server.last_error = 0, ""
        await db.commit()
    return ok(msg="MCP 配置已更新")


@router.delete("/custom-servers/{server_id}", dependencies=[Depends(require_permission("xiaozhi_mcp:manage"))])
async def delete_xiaozhi_custom_server(server_id: int):
    """删除单条小智外部 MCP。"""
    from core.db.base import async_session_factory

    async with async_session_factory() as db:
        server = await _find_server(db, server_id)
        if not server:
            raise HTTPException(status_code=404, detail="MCP 服务器不存在")
        await db.delete(server)
        await db.commit()
    return ok(msg="MCP 配置已删除")


@router.post("/custom-servers/{server_id}/test", dependencies=[Depends(require_permission("xiaozhi_mcp:manage"))])
async def test_xiaozhi_custom_server(server_id: int):
    """测试单条小智外部 MCP，并持久化成功或失败状态。"""
    from core.db.base import async_session_factory

    async with async_session_factory() as db:
        server = await _find_server(db, server_id)
        if not server:
            raise HTTPException(status_code=404, detail="MCP 服务器不存在")
        result = await test_and_cache_tools(server, db)
    return ok(result)


@router.get("/custom-servers/{server_id}/tools", dependencies=[Depends(require_permission("xiaozhi_mcp:list"))])
async def list_xiaozhi_custom_server_tools(server_id: int):
    """获取单条小智外部 MCP 的缓存或实时工具。"""
    from core.db.base import async_session_factory

    async with async_session_factory() as db:
        server = await _find_server(db, server_id)
        if not server:
            raise HTTPException(status_code=404, detail="MCP 服务器不存在")
        result = await load_or_discover_tools(server, db)
    return ok(result)
