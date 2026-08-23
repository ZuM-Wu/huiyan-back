# -*- coding: utf-8 -*-
"""农户端外部 MCP 个人凭据管理接口。

管理员仅维护公共 MCP 服务入口，农户在此绑定自己的远程账号 Key。
农户凭据与公共服务物理分离，工具桥不会再回退使用管理员配置的共享 Key。
"""
import json
import logging
from datetime import datetime
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from core.auth.middleware_chain import check_farmer
from core.db import base as db_base
from core.db.ai import AiFarmerMcpServerModel, FarmerMcpCredentialModel
from core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/ai", tags=["农户AI-MCP"])


class FarmerMcpCredentialUpsert(BaseModel):
    """农户个人 MCP 凭据更新请求"""
    api_key: str = ""
    clear_api_key: bool = False


def _mask_key(api_key: str) -> str:
    """默认脱敏展示 Key，仅保留首尾少量字符供农户识别。"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * min(16, len(api_key) - 8)}{api_key[-4:]}"


def _decode_tools_cache(raw: str | None) -> list:
    """安全解析个人工具缓存。"""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _credential_view(server, credential, *, reveal: bool = False) -> dict:
    """组装农户可见的公共服务与个人凭据状态。"""
    api_key = credential.api_key if credential else ""
    return {
        "id": server.id,
        "name": server.name,
        "url": server.url,
        "status": server.status,
        "configured": bool(api_key and credential and credential.status == 1),
        "api_key": api_key if reveal else _mask_key(api_key),
        "tools_cache": _decode_tools_cache(credential.tools_cache if credential else None),
        "last_test_time": str(credential.last_test_time) if credential and credential.last_test_time else "",
        "last_test_status": credential.last_test_status if credential else 0,
        "last_error": credential.last_error if credential else "",
    }


async def _get_server_and_credential(db, farmer_id: int, server_id: int):
    """查询启用的公共服务及当前农户凭据。"""
    server = (await db.execute(
        select(AiFarmerMcpServerModel).where(
            AiFarmerMcpServerModel.id == server_id,
            AiFarmerMcpServerModel.status == 1,
        )
    )).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP服务器不存在或已停用")
    credential = (await db.execute(
        select(FarmerMcpCredentialModel).where(
            FarmerMcpCredentialModel.farmer_id == farmer_id,
            FarmerMcpCredentialModel.server_id == server_id,
        )
    )).scalar_one_or_none()
    return server, credential


@router.get("/mcp-servers")
async def list_personal_mcp_servers(
    request: Request, _: None = Depends(check_farmer),
):
    """获取启用的农户端 MCP 服务及当前农户凭据状态。"""
    farmer_id = request.state.user_id
    async with db_base.async_session_factory() as db:
        servers = (await db.execute(
            select(AiFarmerMcpServerModel)
            .where(AiFarmerMcpServerModel.status == 1)
            .order_by(AiFarmerMcpServerModel.id.asc())
        )).scalars().all()
        credentials = (await db.execute(
            select(FarmerMcpCredentialModel).where(
                FarmerMcpCredentialModel.farmer_id == farmer_id
            )
        )).scalars().all()
    by_server = {item.server_id: item for item in credentials}
    return ok({"list": [_credential_view(server, by_server.get(server.id)) for server in servers]})


@router.get("/mcp-servers/{server_id}/credential")
async def get_personal_mcp_credential(
    server_id: int,
    request: Request,
    reveal: bool = Query(False, description="是否显式返回当前农户 Key 明文"),
    _: None = Depends(check_farmer),
):
    """获取单个 MCP 服务的个人凭据状态；明文仅在显式 reveal 时返回。"""
    async with db_base.async_session_factory() as db:
        server, credential = await _get_server_and_credential(
            db, request.state.user_id, server_id)
    return ok(_credential_view(server, credential, reveal=reveal))


@router.put("/mcp-servers/{server_id}/credential")
async def update_personal_mcp_credential(
    server_id: int,
    data: FarmerMcpCredentialUpsert,
    request: Request,
    _: None = Depends(check_farmer),
):
    """新增、更新或清除当前农户的 MCP Key。"""
    api_key = data.api_key.strip()
    farmer_id = request.state.user_id
    async with db_base.async_session_factory() as db:
        server, credential = await _get_server_and_credential(db, farmer_id, server_id)
        if data.clear_api_key:
            if credential:
                credential.api_key = ""
                credential.status = 2
                credential.tools_cache = None
                credential.last_error = ""
                credential.last_test_status = 0
            await db.commit()
            return ok(msg="MCP个人凭据已清除")
        if not api_key and not credential:
            raise HTTPException(status_code=400, detail="请提供 MCP API Key")
        if credential is None:
            credential = FarmerMcpCredentialModel(
                farmer_id=farmer_id, server_id=server_id, api_key=api_key)
            db.add(credential)
        elif api_key:
            credential.api_key = api_key
            credential.status = 1
            credential.tools_cache = None
            credential.last_test_status = 0
            credential.last_error = ""
        await db.commit()
    return ok(msg=f"已保存 MCP 凭据：{server.name}")


@router.delete("/mcp-servers/{server_id}/credential")
async def delete_personal_mcp_credential(
    server_id: int, request: Request, _: None = Depends(check_farmer),
):
    """删除当前农户的 MCP 个人凭据。"""
    async with db_base.async_session_factory() as db:
        _, credential = await _get_server_and_credential(
            db, request.state.user_id, server_id)
        if credential:
            await db.delete(credential)
            await db.commit()
    return ok(msg="MCP个人凭据已删除")


@router.post("/mcp-servers/{server_id}/credential/test")
async def test_personal_mcp_credential(
    server_id: int, request: Request, _: None = Depends(check_farmer),
):
    """使用当前农户个人 Key 测试 MCP 并缓存该账号可用工具。"""
    farmer_id = request.state.user_id
    async with db_base.async_session_factory() as db:
        server, credential = await _get_server_and_credential(db, farmer_id, server_id)
        if not credential or not credential.api_key or credential.status != 1:
            return ok({"success": False, "message": "请先配置个人 MCP API Key"})
        target = SimpleNamespace(url=server.url, api_key=credential.api_key)
        from api.admin.ai_mcp import _discover_tools
        tools_data, error = await _discover_tools(target)
        credential.last_test_time = datetime.now()
        if error:
            credential.last_test_status = 2
            credential.last_error = error[:512]
            credential.tools_cache = None
            await db.commit()
            logger.warning("[农户MCP] 个人凭据测试失败 farmer=%s server=%s", farmer_id, server_id)
            return ok({"success": False, "message": error})
        credential.last_test_status = 1
        credential.last_error = ""
        credential.tools_cache = json.dumps(tools_data, ensure_ascii=False)
        await db.commit()
    logger.info("[农户MCP] 个人凭据测试成功 farmer=%s server=%s tools=%s",
                farmer_id, server_id, len(tools_data))
    return ok({
        "success": True,
        "message": f"连接成功，发现 {len(tools_data)} 个工具",
        "tools": tools_data,
    })
