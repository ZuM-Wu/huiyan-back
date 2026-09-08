# -*- coding: utf-8 -*-
"""AI 资源响应序列化，集中处理公开元数据与安全字段。"""
from __future__ import annotations

import json

from core.db.ai_resources import (
    AgentScopeConnectionModelDiscoveryModel,
    AgentScopeConnectionPoolModel,
    AgentScopeModelCardModel,
)
from services.agentscope.connection_presets import (
    OPENAI_CHAT_COMPLETIONS,
    OPENAI_RESPONSES,
    PRESET_MAP,
)
from services.agentscope.model_capabilities import reconcile_input_capabilities
from services.agentscope.reasoning import (
    reasoning_capability,
    reasoning_options_payload,
)


def _json_list(value: str | list | None) -> list:
    if isinstance(value, list):
        return value
    try:
        result = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return result if isinstance(result, list) else []


def _json_object(value: str | dict | None) -> dict:
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def card_payload(row: AgentScopeModelCardModel) -> dict:
    input_types, support_vision = reconcile_input_capabilities(
        None,
        None,
        input_types_supplied=False,
        fallback_types=_json_list(row.input_types),
        fallback_vision=bool(row.support_vision),
    )
    parameter_schema = _json_object(row.parameter_schema)
    reasoning = reasoning_capability(bool(row.support_reasoning), parameter_schema)
    return {
        "id": row.id, "connection_id": row.connection_id, "provider": row.provider,
        "model_name": row.model_name, "label": row.label,
        "input_types": input_types, "output_types": _json_list(row.output_types),
        "context_size": row.context_size, "output_size": row.output_size,
        "support_tools": bool(row.support_tools), "support_reasoning": reasoning.supported,
        "support_vision": support_vision, "parameter_schema": parameter_schema,
        **reasoning_options_payload(reasoning),
        "status": row.status,
    }


def connection_payload(row: AgentScopeConnectionPoolModel, credential_data: dict | None = None) -> dict:
    preset = PRESET_MAP.get(getattr(row, "preset_key", ""))
    vendor_name = getattr(row, "vendor_name", "") or (preset.vendor if preset else row.provider)
    protocol = getattr(row, "protocol", "")
    if protocol not in {OPENAI_CHAT_COMPLETIONS, OPENAI_RESPONSES}:
        protocol = preset.protocol if preset else OPENAI_CHAT_COMPLETIONS
    return {
        "id": row.id, "preset_key": getattr(row, "preset_key", "custom_openai"),
        "vendor": vendor_name, "vendor_name": vendor_name, "protocol": protocol,
        "provider": row.provider, "credential_id": row.credential_id, "name": row.name,
        "base_url": (credential_data or {}).get("base_url") or (preset.base_url if preset else ""),
        "default_model": row.default_model, "status": row.status, "is_default": bool(row.is_default),
        "last_test_status": row.last_test_status, "last_test_error": row.last_test_error,
        "last_test_at": row.last_test_at.isoformat() if row.last_test_at else None,
    }


def discovery_payload(row: AgentScopeConnectionModelDiscoveryModel) -> dict:
    input_types, support_vision = reconcile_input_capabilities(
        None,
        None,
        input_types_supplied=False,
        fallback_types=_json_list(row.input_types),
        fallback_vision=bool(row.support_vision),
    )
    parameter_schema = _json_object(row.parameter_schema)
    reasoning = reasoning_capability(bool(row.support_reasoning), parameter_schema)
    return {
        "model_name": row.model_name, "label": row.label,
        "input_types": input_types, "output_types": _json_list(row.output_types),
        "context_size": row.context_size, "output_size": row.output_size,
        "support_tools": bool(row.support_tools), "support_reasoning": reasoning.supported,
        "support_vision": support_vision, "parameter_schema": parameter_schema,
        **reasoning_options_payload(reasoning),
    }
