# -*- coding: utf-8 -*-
"""管理员 MCP 资源接口，供 AI 资源总路由挂载。"""
from __future__ import annotations

from urllib.parse import urlparse

from agentscope.app.storage import MCPRecord
from agentscope.mcp import HttpMCPConfig, MCPClient
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok
from services.agentscope.runtime import agentscope_runtime
from services.mcp.system_endpoint import normalize_system_mcp_url


router = APIRouter()
_PERM = Depends(require_permission("ai:setting"))


def _mcp_payload(row: MCPRecord) -> dict:
    """返回不包含密钥的 MCP 资源视图。"""
    config = row.client.mcp_config
    url = normalize_system_mcp_url(
        row.client.name,
        getattr(config, "url", row.url or ""),
    )
    return {
        "id": row.id,
        "name": row.client.name,
        "display_name": row.display_name or row.client.name,
        "description": row.description,
        "url": url,
        "enabled": bool(row.enabled),
        "is_stateful": bool(row.client.is_stateful),
        "tags": row.tags,
    }


class MCPInput(BaseModel):
    """管理员 MCP 资源请求。"""

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    display_name: str = Field(default="", max_length=128)
    description: str = Field(default="", max_length=512)
    url: str = Field(min_length=1, max_length=1024)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP 地址必须是 http/https URL")
        return value.strip()


class MCPPatch(BaseModel):
    """管理员 MCP 资源更新请求。"""

    display_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    url: str | None = Field(default=None, max_length=1024)
    headers: dict[str, str] | None = None
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP 地址必须是 http/https URL")
        return value.strip()


async def _admin_user(request: Request, _: None = Depends(check_admin)) -> str:
    return f"admin:{request.state.user_id}"


@router.get("/mcps", dependencies=[_PERM])
async def list_mcps(user_id: str = Depends(_admin_user)):
    rows = await agentscope_runtime.storage.list_mcps(user_id)
    return ok({"list": [_mcp_payload(row) for row in rows]})


@router.post("/mcps", dependencies=[_PERM])
async def create_mcp(data: MCPInput, user_id: str = Depends(_admin_user)):
    url = normalize_system_mcp_url(data.name, data.url)
    client = MCPClient(
        name=data.name,
        is_stateful=False,
        mcp_config=HttpMCPConfig(url=url, headers=data.headers or None),
    )
    record = MCPRecord(
        user_id=user_id,
        client=client,
        display_name=data.display_name or data.name,
        description=data.description,
        url=url,
        values={"headers": data.headers},
        enabled=data.enabled,
    )
    try:
        await agentscope_runtime.storage.upsert_mcp(user_id, record)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ok(_mcp_payload(record), msg="MCP 已创建")


@router.patch("/mcps/{mcp_id}", dependencies=[_PERM])
async def update_mcp(mcp_id: str, data: MCPPatch, user_id: str = Depends(_admin_user)):
    record = await agentscope_runtime.storage.get_mcp(user_id, mcp_id)
    if record is None:
        raise HTTPException(status_code=404, detail="MCP 不存在")
    updates = data.model_dump(exclude_none=True)
    old_config = record.client.mcp_config
    old_url = getattr(old_config, "url", "")
    url = normalize_system_mcp_url(
        record.client.name,
        updates.get("url", old_url),
    )
    if "url" in updates or "headers" in updates or url != old_url:
        updates.pop("url", None)
        headers = updates.pop("headers", getattr(old_config, "headers", None))
        updates["client"] = record.client.model_copy(
            update={"mcp_config": HttpMCPConfig(url=url, headers=headers or None)},
        )
        updates["url"] = url
        if data.headers is not None:
            values = dict(record.values or {})
            values["headers"] = data.headers
            updates["values"] = values
    if "display_name" in updates and not updates["display_name"]:
        updates["display_name"] = record.client.name
    updated = record.model_copy(update=updates)
    try:
        await agentscope_runtime.storage.upsert_mcp(user_id, updated)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ok(_mcp_payload(updated), msg="MCP 已更新")


@router.delete("/mcps/{mcp_id}", dependencies=[_PERM])
async def delete_mcp(mcp_id: str, user_id: str = Depends(_admin_user)):
    if not await agentscope_runtime.storage.delete_mcp(user_id, mcp_id):
        raise HTTPException(status_code=404, detail="MCP 不存在")
    return ok(msg="MCP 已删除")


@router.put("/mcps/{mcp_id}/status", dependencies=[_PERM])
async def set_mcp_status(mcp_id: str, request: Request, user_id: str = Depends(_admin_user)):
    body = await request.json()
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="enabled 必须为布尔值")
    record = await agentscope_runtime.storage.get_mcp(user_id, mcp_id)
    if record is None:
        raise HTTPException(status_code=404, detail="MCP 不存在")
    updated = record.model_copy(update={"enabled": enabled})
    await agentscope_runtime.storage.upsert_mcp(user_id, updated)
    return ok(_mcp_payload(updated), msg="MCP 状态已更新")
