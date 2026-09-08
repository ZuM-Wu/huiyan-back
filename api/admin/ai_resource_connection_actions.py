# -*- coding: utf-8 -*-
"""供应商连接的状态、默认连接和连通性操作。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.ai_resources import AgentScopeConnectionPoolModel
from core.db.base import async_session_factory
from core.response import ok
from services.agentscope.connection_health import test_connection_record


router = APIRouter(tags=["AgentScope供应商连接操作"])
_PERM = Depends(require_permission("ai:setting"))


async def _admin_user(request: Request, _: None = Depends(check_admin)) -> str:
    return f"admin:{request.state.user_id}"


async def _connection_row(db, connection_id: int) -> AgentScopeConnectionPoolModel:
    row = (await db.execute(select(AgentScopeConnectionPoolModel).where(
        AgentScopeConnectionPoolModel.id == connection_id,
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")
    return row


@router.post("/connections/{connection_id}/default", dependencies=[_PERM])
async def set_default_connection(connection_id: int, _: str = Depends(_admin_user)):
    async with async_session_factory() as db:
        row = await _connection_row(db, connection_id)
        if row.status != 1:
            raise HTTPException(status_code=409, detail="停用连接不能设为默认")
        await db.execute(update(AgentScopeConnectionPoolModel).values(is_default=0))
        row.is_default = 1
        await db.commit()
    return ok(msg="默认连接已更新")


@router.put("/connections/{connection_id}/status", dependencies=[_PERM])
async def set_connection_status(connection_id: int, request: Request, _: str = Depends(_admin_user)):
    status = (await request.json()).get("status")
    if status not in (1, 2):
        raise HTTPException(status_code=422, detail="status 必须为 1 或 2")
    async with async_session_factory() as db:
        row = await _connection_row(db, connection_id)
        if status == 2 and row.is_default:
            row.is_default = 0
        row.status = status
        await db.commit()
    return ok(msg="连接状态已更新")


@router.post("/connections/{connection_id}/test", dependencies=[_PERM])
async def test_connection(connection_id: int, _: str = Depends(_admin_user)):
    result = await test_connection_record(
        connection_id, include_disabled=True,
    )
    if result.error_code == "not_found":
        raise HTTPException(status_code=404, detail=result.message)
    if result.error_code == "credential_not_found":
        raise HTTPException(status_code=409, detail=result.message)
    return ok({"success": result.success, "message": result.message})
