# -*- coding: utf-8 -*-
"""
AI 设置 — 农户端 MCP 能力（农户端外部 MCP 服务器 CRUD）

与 ai_mcp.py（管理员端）结构对齐，操作 AiFarmerMcpServerModel，
路由前缀 mcp-servers-farmer，实现管理员/农户 MCP 服务器物理隔离。
复用 ai_mcp.py 的 _parse_mcp_config 与 _discover_tools（模型无关纯函数），
_cache_tools 因绑定具体 ORM 类故本地实现。
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok
# 复用管理员端的 JSON 解析与工具发现（模型无关纯函数）
from api.admin.ai_mcp import _parse_mcp_config, _discover_tools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/ai/setting", tags=["AI设置"])

_PERM = Depends(require_permission("ai:setting"))


class FarmerMcpServerUpsert(BaseModel):
    """农户端外部 MCP 服务器创建/更新请求"""
    name: str
    url: str = ""
    api_key: str = ""
    config_json: str = ""
    status: int = 1
    clear_api_key: bool = False


def _decode_tools_cache(raw: str | None, server_id: int) -> list:
    """安全解析工具缓存；损坏数据只影响该服务器。"""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[AI设置] 农户端 MCP 服务器 %s 的工具缓存损坏", server_id)
        return []
    return value if isinstance(value, list) else []


async def _cache_farmer_tools(server_id: int, tools_data: list):
    """将工具列表缓存到农户端 MCP 服务器记录"""
    from core.db.base import async_session_factory
    from core.db.ai import AiFarmerMcpServerModel
    async with async_session_factory() as db:
        await db.execute(
            update(AiFarmerMcpServerModel)
            .where(AiFarmerMcpServerModel.id == server_id)
            .values(tools_cache=json.dumps(tools_data, ensure_ascii=False))
        )
        await db.commit()


@router.get("/mcp-servers-farmer", dependencies=[_PERM])
async def list_farmer_mcp_servers(_: None = Depends(check_admin)):
    """农户端外部 MCP 服务器列表"""
    from core.db.base import async_session_factory
    from core.db.ai import AiFarmerMcpServerModel
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(AiFarmerMcpServerModel).order_by(AiFarmerMcpServerModel.id.asc())
        )).scalars().all()
    return ok({"list": [{
        "id": s.id, "name": s.name, "url": s.url,
        "api_key": s.api_key, "config_json": s.config_json or "",
        "tools_cache": _decode_tools_cache(s.tools_cache, s.id),
        "status": s.status,
        "create_time": str(s.create_time),
    } for s in rows]})


@router.get("/mcp-servers-farmer/{server_id}/credential-status", dependencies=[_PERM])
async def list_farmer_mcp_credential_status(
    server_id: int, _: None = Depends(check_admin),
):
    """查看农户个人凭据配置状态，不返回任何农户 Key。"""
    from core.db.base import async_session_factory
    from core.db.ai import FarmerMcpCredentialModel, AiFarmerMcpServerModel
    from core.db.farmer import Farmer
    async with async_session_factory() as db:
        server = (await db.execute(
            select(AiFarmerMcpServerModel).where(AiFarmerMcpServerModel.id == server_id)
        )).scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        farmers = (await db.execute(
            select(Farmer).where(Farmer.status == 1).order_by(Farmer.id.asc())
        )).scalars().all()
        credentials = (await db.execute(
            select(FarmerMcpCredentialModel).where(
                FarmerMcpCredentialModel.server_id == server_id
            )
        )).scalars().all()
    by_farmer = {item.farmer_id: item for item in credentials}
    items = []
    for farmer in farmers:
        credential = by_farmer.get(farmer.id)
        configured = bool(credential and credential.status == 1 and credential.api_key)
        items.append({
            "farmer_id": farmer.id,
            "name": farmer.nickname or farmer.username,
            "configured": configured,
            "last_test_status": credential.last_test_status if credential else 0,
            "last_error": credential.last_error if credential else "",
        })
    return ok({
        "server_id": server_id,
        "configured_count": sum(1 for item in items if item["configured"]),
        "unconfigured_count": sum(1 for item in items if not item["configured"]),
        "list": items,
    })


@router.post("/mcp-servers-farmer", dependencies=[_PERM])
async def create_farmer_mcp_server(data: FarmerMcpServerUpsert,
                                   _: None = Depends(check_admin)):
    """添加农户端外部 MCP 服务器（支持 JSON 配置粘贴或表单直填）"""
    from core.db.base import async_session_factory
    from core.db.ai import AiFarmerMcpServerModel
    url, api_key = _parse_mcp_config(data.config_json)
    if not url:
        url = data.url
    if not api_key:
        api_key = data.api_key
    if not url:
        raise HTTPException(status_code=400, detail="请提供服务地址或有效的 JSON 配置")
    async with async_session_factory() as db:
        server = AiFarmerMcpServerModel(
            name=data.name, url=url, api_key=api_key,
            config_json=data.config_json or None, status=data.status)
        db.add(server)
        await db.commit()
        return ok({"id": server.id}, msg="农户端服务器已添加")


@router.put("/mcp-servers-farmer/{server_id}", dependencies=[_PERM])
async def update_farmer_mcp_server(server_id: int, data: FarmerMcpServerUpsert,
                                    _: None = Depends(check_admin)):
    """更新农户端外部 MCP 服务器"""
    from core.db.base import async_session_factory
    from core.db.ai import AiFarmerMcpServerModel, FarmerMcpCredentialModel
    url, api_key = _parse_mcp_config(data.config_json)
    if not url:
        url = data.url
    if not api_key:
        api_key = data.api_key
    if not url:
        raise HTTPException(status_code=400, detail="请提供服务地址或有效的 JSON 配置")
    async with async_session_factory() as db:
        server = (await db.execute(
            select(AiFarmerMcpServerModel).where(AiFarmerMcpServerModel.id == server_id)
        )).scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        server.name = data.name
        server.url = url
        if data.clear_api_key:
            server.api_key = ""
        elif api_key:
            server.api_key = api_key
        server.config_json = data.config_json or None
        server.status = data.status
        await db.execute(
            update(FarmerMcpCredentialModel)
            .where(FarmerMcpCredentialModel.server_id == server_id)
            .values(tools_cache=None, last_test_status=0, last_error="")
        )
        await db.commit()
    return ok(msg="农户端服务器已更新")


@router.delete("/mcp-servers-farmer/{server_id}", dependencies=[_PERM])
async def delete_farmer_mcp_server(server_id: int, _: None = Depends(check_admin)):
    """删除农户端外部 MCP 服务器"""
    from core.db.base import async_session_factory
    from core.db.ai import AiFarmerMcpServerModel, FarmerMcpCredentialModel
    async with async_session_factory() as db:
        server = (await db.execute(
            select(AiFarmerMcpServerModel).where(AiFarmerMcpServerModel.id == server_id)
        )).scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        await db.execute(
            update(FarmerMcpCredentialModel)
            .where(FarmerMcpCredentialModel.server_id == server_id)
            .values(status=2, tools_cache=None, last_error="公共 MCP 服务已删除")
        )
        await db.delete(server)
        await db.commit()
    return ok(msg="农户端服务器已删除")


@router.post("/mcp-servers-farmer/{server_id}/test", dependencies=[_PERM])
async def test_farmer_mcp_server(server_id: int, _: None = Depends(check_admin)):
    """农户端外部 MCP 服务器连通测试（发现工具并缓存）"""
    from core.db.base import async_session_factory
    from core.db.ai import AiFarmerMcpServerModel
    async with async_session_factory() as db:
        server = (await db.execute(
            select(AiFarmerMcpServerModel).where(AiFarmerMcpServerModel.id == server_id)
        )).scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")

    tools_data, err = await _discover_tools(server)
    if err:
        return ok({"success": False, "message": err})
    await _cache_farmer_tools(server_id, tools_data)
    return ok({"success": True,
               "message": f"连接成功，发现 {len(tools_data)} 个工具",
               "tools": tools_data})


@router.get("/mcp-servers-farmer/{server_id}/tools", dependencies=[_PERM])
async def list_farmer_mcp_server_tools(server_id: int, _: None = Depends(check_admin)):
    """获取农户端外部 MCP 服务器的工具列表（优先返回缓存，无缓存则实时发现）"""
    from core.db.base import async_session_factory
    from core.db.ai import AiFarmerMcpServerModel
    async with async_session_factory() as db:
        server = (await db.execute(
            select(AiFarmerMcpServerModel).where(AiFarmerMcpServerModel.id == server_id)
        )).scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        if server.tools_cache:
            try:
                return ok({"list": json.loads(server.tools_cache), "cached": True})
            except json.JSONDecodeError:
                pass

    tools_data, err = await _discover_tools(server)
    if err:
        return ok({"list": [], "message": err})
    await _cache_farmer_tools(server_id, tools_data)
    return ok({"list": tools_data, "cached": False})
