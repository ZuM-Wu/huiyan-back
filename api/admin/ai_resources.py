# -*- coding: utf-8 -*-
"""AgentScope AI 资源管理接口。"""
from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import urlparse

from agentscope.credential import CredentialFactory
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select

from api.admin import ai_resource_mcp as _mcp
from api.admin import ai_resource_connection_actions as _connection_actions
from api.admin import ai_resource_skills as _skills
from api.admin.ai_resource_serializers import (
    card_payload as _card_payload,
    connection_payload as _connection_payload,
    discovery_payload as _discovery_row_payload,
)
from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.ai_resources import (
    AgentScopeConnectionModelDiscoveryModel,
    AgentScopeConnectionPoolModel,
    AgentScopeModelCardModel,
)
from core.db.base import async_session_factory
from core.response import ok
from services.agentscope.connection_presets import (
    PRESETS,
    discover_models,
    preset_payload,
    resolve_preset,
)
from services.agentscope.platform_credentials import PLATFORM_CREDENTIAL_OWNER
from services.agentscope.model_capabilities import reconcile_input_capabilities
from services.agentscope.runtime import agentscope_runtime


MCPInput = _mcp.MCPInput
_mcp_payload = _mcp._mcp_payload
router = APIRouter(prefix="/api/admin/v1/ai-resources", tags=["AgentScope资源"])
router.include_router(_mcp.router)
router.include_router(_connection_actions.router)
router.include_router(_skills.router)
_PERM = Depends(require_permission("ai:setting"))
_MODEL_DISCOVERY_CACHE: dict[int, list[dict[str, Any]]] = {}
_INPUT_MODALITY_OPTIONS = {"text/plain", "image/*"}
_OUTPUT_MODALITY_OPTIONS = {"text/plain", "application/x-thinking"}


class ModelCardInput(BaseModel):
    """ModelCard 请求；来源连接和模型 ID 仅用于创建时选择。"""

    connection_id: int | None = Field(default=None, ge=1)
    provider: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    label: str | None = Field(default=None, max_length=128)
    input_types: list[str] = Field(default_factory=list)
    output_types: list[str] = Field(default_factory=list)
    context_size: int | None = Field(default=None, ge=1, le=10_000_000)
    output_size: int | None = Field(default=None, ge=1, le=1_000_000)
    support_tools: bool | None = None
    support_reasoning: bool | None = None
    support_vision: bool | None = None
    parameter_schema: dict[str, Any] | None = None
    status: int | None = Field(default=None, ge=1, le=2)

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_optional_provider(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("input_types")
    @classmethod
    def validate_input_types(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values if str(value).strip()]
        invalid = sorted(set(normalized) - _INPUT_MODALITY_OPTIONS)
        if invalid:
            raise ValueError(f"不支持的模型模态：{', '.join(invalid)}")
        return normalized

    @field_validator("output_types")
    @classmethod
    def validate_output_types(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values if str(value).strip()]
        invalid = sorted(set(normalized) - _OUTPUT_MODALITY_OPTIONS)
        if invalid:
            raise ValueError(f"不支持的模型模态：{', '.join(invalid)}")
        return normalized


class ConnectionInput(BaseModel):
    """Credential 连接池请求。"""

    preset_key: str | None = Field(default=None, max_length=64)
    provider: str | None = Field(default=None, max_length=64)
    protocol: Literal["openai_chat_completions", "openai_responses"] | None = None
    vendor_name: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=128)
    api_key: str = Field(default="", max_length=512)
    base_url: str = Field(default="", max_length=512)
    default_model: str = Field(default="", max_length=128)
    status: int = Field(default=1, ge=1, le=2)


class ConnectionPatch(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)
    base_url: str | None = Field(default=None, max_length=512)
    default_model: str | None = Field(default=None, max_length=128)
    status: int | None = Field(default=None, ge=1, le=2)


async def _admin_user(request: Request, _: None = Depends(check_admin)) -> str:
    return f"admin:{request.state.user_id}"


def _credential_data(data: ConnectionInput | ConnectionPatch, provider: str, existing: dict | None = None) -> dict:
    payload = dict(existing or {})
    payload["type"] = provider
    for field in ("api_key", "base_url", "name"):
        value = getattr(data, field, None)
        if value is not None and (field not in {"api_key", "base_url"} or value.strip()):
            payload[field] = value.strip() if isinstance(value, str) else value
    return payload


def _validate_provider(provider: str) -> None:
    if CredentialFactory.get_credential_class(provider) is None:
        raise HTTPException(status_code=422, detail=f"未注册的 Credential Provider：{provider}")


def _connection_vendor_name(preset, submitted: str) -> str:
    """固定预设禁止客户端覆盖厂商名，自定义预设必须提供名称。"""
    if preset.key != "custom_openai":
        return preset.vendor
    vendor_name = submitted.strip()
    if not vendor_name:
        raise HTTPException(status_code=422, detail="自定义供应商必须填写供应商名称")
    return vendor_name


def _validate_base_url(base_url: str) -> str:
    """校验并规范连接地址，避免保存不可请求或非 HTTP 协议的地址。"""
    normalized = base_url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Base URL 必须是 http/https URL")
    return normalized.rstrip("/")


async def _save_native_credential(user_id: str, payload: dict, credential_id: str | None = None) -> str:
    credential = CredentialFactory.from_dict(payload)
    if credential_id:
        credential.id = credential_id
    return await agentscope_runtime.storage.upsert_credential(user_id, credential)


async def _connection_row(db, connection_id: int) -> AgentScopeConnectionPoolModel:
    query = select(AgentScopeConnectionPoolModel).where(AgentScopeConnectionPoolModel.id == connection_id)
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="连接池连接不存在")
    return row


async def _discovered_models(db, connection_id: int) -> list[dict[str, Any]]:
    """读取最近一次成功发现结果；保留内存缓存兼容旧测试和热路径。"""
    cached = _MODEL_DISCOVERY_CACHE.get(connection_id)
    if cached is not None:
        return cached
    rows = (await db.execute(select(AgentScopeConnectionModelDiscoveryModel).where(
        AgentScopeConnectionModelDiscoveryModel.connection_id == connection_id,
    ).order_by(AgentScopeConnectionModelDiscoveryModel.model_name))).scalars().all()
    models = [_discovery_row_payload(row) for row in rows]
    _MODEL_DISCOVERY_CACHE[connection_id] = models
    return models


async def _replace_discovered_models(db, connection_id: int, models: list[dict[str, Any]]) -> None:
    await db.execute(delete(AgentScopeConnectionModelDiscoveryModel).where(AgentScopeConnectionModelDiscoveryModel.connection_id == connection_id))
    for model in models:
        db.add(AgentScopeConnectionModelDiscoveryModel(
            connection_id=connection_id,
            model_name=model["model_name"],
            label=model["label"],
            input_types=json.dumps(model["input_types"], ensure_ascii=False),
            output_types=json.dumps(model["output_types"], ensure_ascii=False),
            context_size=model["context_size"], output_size=model["output_size"],
            support_tools=int(model["support_tools"]), support_reasoning=int(model["support_reasoning"]),
            support_vision=int(model["support_vision"]),
            parameter_schema=json.dumps(model["parameter_schema"], ensure_ascii=False),
        ))


@router.get("/connection-presets", dependencies=[_PERM])
async def list_connection_presets(_: str = Depends(_admin_user)):
    return ok({"list": [preset_payload(item) for item in PRESETS]})


@router.get("/model-cards", dependencies=[_PERM])
async def list_model_cards(_: str = Depends(_admin_user)):
    async with async_session_factory() as db:
        rows = (await db.execute(select(AgentScopeModelCardModel).order_by(
            AgentScopeModelCardModel.connection_id, AgentScopeModelCardModel.model_name,
        ))).scalars().all()
    return ok({"list": [_card_payload(row) for row in rows]})


@router.post("/model-cards", dependencies=[_PERM])
async def create_model_card(data: ModelCardInput, _: str = Depends(_admin_user)):
    if data.connection_id is None or not data.model_name:
        raise HTTPException(status_code=422, detail="ModelCard 必须绑定连接并选择模型")
    async with async_session_factory() as db:
        connection = await _connection_row(db, data.connection_id)
        discovered = await _discovered_models(db, connection.id)
        model = next((item for item in discovered if item["model_name"] == data.model_name), None)
        if model is None:
            raise HTTPException(status_code=409, detail="模型不在该连接最近一次刷新结果中，请先刷新")
        if data.provider and data.provider != connection.provider:
            raise HTTPException(status_code=422, detail="Provider 必须由连接派生")
        exists = (await db.execute(select(AgentScopeModelCardModel.id).where(
            AgentScopeModelCardModel.connection_id == connection.id,
            AgentScopeModelCardModel.model_name == data.model_name,
        ))).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=409, detail="该连接下的模型 ID 已存在")
        input_types, support_vision = reconcile_input_capabilities(
            data.input_types,
            data.support_vision,
            input_types_supplied="input_types" in data.model_fields_set,
            fallback_types=model["input_types"],
            fallback_vision=bool(model["support_vision"]),
        )
        row = AgentScopeModelCardModel(
            connection_id=connection.id,
            provider=connection.provider,
            model_name=data.model_name,
            label=data.label or model["label"],
            input_types=json.dumps(input_types, ensure_ascii=False),
            output_types=json.dumps(data.output_types or model["output_types"], ensure_ascii=False),
            context_size=data.context_size or model["context_size"], output_size=data.output_size or model["output_size"],
            support_tools=int(data.support_tools if data.support_tools is not None else model["support_tools"]),
            support_reasoning=int(data.support_reasoning if data.support_reasoning is not None else model["support_reasoning"]),
            support_vision=int(support_vision),
            parameter_schema=json.dumps(data.parameter_schema if data.parameter_schema is not None else model["parameter_schema"], ensure_ascii=False),
            status=data.status or 1,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return ok(_card_payload(row), msg="ModelCard 已创建")


@router.patch("/model-cards/{card_id}", dependencies=[_PERM])
async def update_model_card(card_id: int, data: ModelCardInput, _: str = Depends(_admin_user)):
    async with async_session_factory() as db:
        row = (await db.execute(select(AgentScopeModelCardModel).where(
            AgentScopeModelCardModel.id == card_id,
        ))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="ModelCard 不存在")
        if data.connection_id is not None and data.connection_id != row.connection_id:
            raise HTTPException(status_code=409, detail="ModelCard 来源连接不可修改")
        if (data.provider and data.provider != row.provider) or (data.model_name and data.model_name != row.model_name):
            raise HTTPException(status_code=409, detail="ModelCard 来源模型不可修改")
        if data.label is not None:
            row.label = data.label
        input_types, support_vision = reconcile_input_capabilities(
            data.input_types,
            data.support_vision,
            input_types_supplied="input_types" in data.model_fields_set,
            fallback_types=json.loads(row.input_types or "[]"),
            fallback_vision=bool(row.support_vision),
        )
        if "input_types" in data.model_fields_set or data.support_vision is not None:
            row.input_types = json.dumps(input_types, ensure_ascii=False)
            row.support_vision = int(support_vision)
        if "output_types" in data.model_fields_set:
            row.output_types = json.dumps(data.output_types, ensure_ascii=False)
        if data.context_size is not None:
            row.context_size = data.context_size
        if data.output_size is not None:
            row.output_size = data.output_size
        if data.support_tools is not None:
            row.support_tools = int(data.support_tools)
        if data.support_reasoning is not None:
            row.support_reasoning = int(data.support_reasoning)
        if data.parameter_schema is not None:
            row.parameter_schema = json.dumps(data.parameter_schema, ensure_ascii=False)
        if data.status is not None:
            row.status = data.status
        await db.commit()
        await db.refresh(row)
    return ok(_card_payload(row), msg="ModelCard 已更新")


@router.delete("/model-cards/{card_id}", dependencies=[_PERM])
async def delete_model_card(card_id: int, _: str = Depends(_admin_user)):
    async with async_session_factory() as db:
        result = await db.execute(delete(AgentScopeModelCardModel).where(AgentScopeModelCardModel.id == card_id))
        if not getattr(result, "rowcount", 0):
            raise HTTPException(status_code=404, detail="ModelCard 不存在")
        await db.commit()
    return ok(msg="ModelCard 已删除")


@router.get("/connections", dependencies=[_PERM])
async def list_connections(_: str = Depends(_admin_user)):
    credentials = {
        item.id: item.data
        for item in await agentscope_runtime.storage.list_credentials(PLATFORM_CREDENTIAL_OWNER)
    }
    async with async_session_factory() as db:
        rows = (await db.execute(select(AgentScopeConnectionPoolModel).order_by(
            AgentScopeConnectionPoolModel.id.desc(),
        ))).scalars().all()
    return ok({"list": [_connection_payload(row, credentials.get(row.credential_id)) for row in rows]})


@router.post("/connections", dependencies=[_PERM])
async def create_connection(data: ConnectionInput, _: str = Depends(_admin_user)):
    try:
        preset = resolve_preset(data.preset_key, data.provider, data.protocol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_provider(preset.provider)
    vendor_name = _connection_vendor_name(preset, data.vendor_name)
    if not data.api_key.strip():
        raise HTTPException(status_code=422, detail="API Key 不能为空")
    base_url = data.base_url.strip() or preset.base_url
    if preset.key == "custom_openai" and not base_url:
        raise HTTPException(status_code=422, detail="自定义供应商连接必须填写 Base URL")
    base_url = _validate_base_url(base_url)
    payload = _credential_data(data, preset.provider)
    payload["base_url"] = base_url
    try:
        credential_id = await _save_native_credential(PLATFORM_CREDENTIAL_OWNER, payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Credential 配置无效，请检查 Provider 和 API Key") from exc
    try:
        async with async_session_factory() as db:
            row = AgentScopeConnectionPoolModel(
                preset_key=preset.key, protocol=preset.protocol, vendor_name=vendor_name, provider=preset.provider,
                credential_id=credential_id, name=data.name, default_model=data.default_model, status=data.status,
            )
            db.add(row)
            await db.commit()
    except Exception:
        await agentscope_runtime.storage.delete_credential(PLATFORM_CREDENTIAL_OWNER, credential_id)
        raise
    return ok(_connection_payload(row, payload), msg="连接池连接已创建")


@router.patch("/connections/{connection_id}", dependencies=[_PERM])
async def update_connection(connection_id: int, data: ConnectionPatch, _: str = Depends(_admin_user)):
    async with async_session_factory() as db:
        row = await _connection_row(db, connection_id)
    try:
        preset = resolve_preset(row.preset_key, row.provider, row.protocol)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record = await agentscope_runtime.storage.get_credential(PLATFORM_CREDENTIAL_OWNER, row.credential_id)
    if not record:
        raise HTTPException(status_code=409, detail="底层 Credential 不存在，请删除后重新创建")
    if data.api_key is not None or data.base_url is not None or data.name is not None:
        payload = _credential_data(data, row.provider, record.data)
        if data.base_url is not None:
            payload["base_url"] = data.base_url.strip() or preset.base_url
            if preset.key == "custom_openai" and not payload["base_url"]:
                raise HTTPException(status_code=422, detail="自定义供应商连接必须填写 Base URL")
            payload["base_url"] = _validate_base_url(payload["base_url"])
        try:
            await _save_native_credential(PLATFORM_CREDENTIAL_OWNER, payload, row.credential_id)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Credential 配置无效，请检查 Provider 和 API Key") from exc
    try:
        async with async_session_factory() as db:
            row = await _connection_row(db, connection_id)
            if data.name is not None:
                row.name = data.name
            if data.default_model is not None:
                row.default_model = data.default_model
            if data.status is not None:
                row.status = data.status
            await db.commit()
    except Exception:
        if 'payload' in locals():
            await _save_native_credential(PLATFORM_CREDENTIAL_OWNER, record.data, record.id)
        raise
    record = await agentscope_runtime.storage.get_credential(PLATFORM_CREDENTIAL_OWNER, row.credential_id)
    return ok(_connection_payload(row, record.data if record else None), msg="连接池连接已更新")


@router.delete("/connections/{connection_id}", dependencies=[_PERM])
async def delete_connection(connection_id: int, _: str = Depends(_admin_user)):
    async with async_session_factory() as db:
        row = await _connection_row(db, connection_id)
        linked = (await db.execute(select(AgentScopeModelCardModel.id).where(
            AgentScopeModelCardModel.connection_id == connection_id,
        ).limit(1))).scalar_one_or_none()
        if linked is not None:
            raise HTTPException(status_code=409, detail="该连接存在关联 ModelCard，请先删除关联模型")
        credential_id = row.credential_id
    record = await agentscope_runtime.storage.get_credential(PLATFORM_CREDENTIAL_OWNER, credential_id)
    if record is None:
        raise HTTPException(status_code=409, detail="底层 Credential 不存在，请删除异常连接数据")
    if not await agentscope_runtime.storage.delete_credential(PLATFORM_CREDENTIAL_OWNER, credential_id):
        raise HTTPException(status_code=503, detail="Credential 删除失败")
    try:
        async with async_session_factory() as db:
            row = await _connection_row(db, connection_id)
            await db.execute(delete(AgentScopeConnectionModelDiscoveryModel).where(
                AgentScopeConnectionModelDiscoveryModel.connection_id == connection_id,
            ))
            await db.delete(row)
            await db.commit()
    except Exception:
        await _save_native_credential(PLATFORM_CREDENTIAL_OWNER, record.data, record.id)
        raise
    _MODEL_DISCOVERY_CACHE.pop(connection_id, None)
    return ok(msg="连接池连接已删除")


set_default_connection = _connection_actions.set_default_connection
set_connection_status = _connection_actions.set_connection_status
test_connection = _connection_actions.test_connection


@router.post("/connections/{connection_id}/models/refresh", dependencies=[_PERM])
async def refresh_connection_models(connection_id: int, _: str = Depends(_admin_user)):
    async with async_session_factory() as db:
        row = await _connection_row(db, connection_id)
    record = await agentscope_runtime.storage.get_credential(PLATFORM_CREDENTIAL_OWNER, row.credential_id)
    if not record:
        raise HTTPException(status_code=409, detail="底层 Credential 不存在")
    try:
        preset = resolve_preset(row.preset_key, row.provider, row.protocol)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        models = await discover_models(
            preset, str(record.data.get("api_key") or ""), str(record.data.get("base_url") or ""),
        )
    except (RuntimeError, ValueError) as exc:
        # 上游短暂故障不应删除最近一次成功目录，否则已有 ModelCard 也无法继续配置。
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    async with async_session_factory() as db:
        await _replace_discovered_models(db, connection_id, models)
        await db.commit()
    _MODEL_DISCOVERY_CACHE[connection_id] = models
    return ok({"connection_id": connection_id, "models": models}, msg="模型列表已刷新")
