# -*- coding: utf-8 -*-
"""AgentScope 按次思考能力解析与任务上下文。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


_EFFORT_LABELS = {
    "none": "关闭",
    "minimal": "极低",
    "low": "低",
    "medium": "中",
    "high": "高",
    "xhigh": "极高",
    "max": "最大",
}


@dataclass(frozen=True)
class ReasoningCapability:
    """ModelCard 对外暴露的思考能力。"""

    supported: bool = False
    efforts: tuple[str, ...] = ()
    default_effort: str | None = None


@dataclass(frozen=True)
class TurnReasoningOptions:
    """单次模型调用使用的思考选项，不进入 Session 持久化。"""

    enabled: bool = False
    effort: str | None = None


_TURN_REASONING_OPTIONS: ContextVar[TurnReasoningOptions | None] = ContextVar(
    "huiyan_turn_reasoning_options",
    default=None,
)


def _enum_values(schema: dict[str, Any]) -> tuple[str, ...]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    effort_schema = properties.get("reasoning_effort")
    if not isinstance(effort_schema, dict):
        return ()
    candidates = [effort_schema]
    for key in ("anyOf", "oneOf"):
        values = effort_schema.get(key)
        if isinstance(values, list):
            candidates.extend(item for item in values if isinstance(item, dict))
    result: list[str] = []
    for candidate in candidates:
        values = candidate.get("enum")
        if not isinstance(values, list):
            continue
        for raw in values:
            value = str(raw).strip()
            if value and value not in result:
                result.append(value)
    return tuple(result)


def reasoning_capability(
    support_reasoning: bool,
    parameter_schema: dict[str, Any] | None,
) -> ReasoningCapability:
    """从 ModelCard 参数 Schema 派生可选等级和推荐默认值。"""
    if not support_reasoning:
        return ReasoningCapability()
    schema = parameter_schema if isinstance(parameter_schema, dict) else {}
    efforts = _enum_values(schema)
    effort_schema = (schema.get("properties") or {}).get("reasoning_effort")
    declared_default = (
        effort_schema.get("default")
        if isinstance(effort_schema, dict)
        else None
    )
    default_effort = str(declared_default) if declared_default in efforts else None
    if default_effort is None and "high" in efforts:
        default_effort = "high"
    if default_effort is None:
        default_effort = next((item for item in efforts if item != "none"), None)
    return ReasoningCapability(True, efforts, default_effort)


def reasoning_options_payload(capability: ReasoningCapability) -> dict[str, Any]:
    """生成聊天模型目录可直接使用的思考等级字段。"""
    return {
        "reasoning_efforts": [
            {"value": value, "label": _EFFORT_LABELS.get(value, value)}
            for value in capability.efforts
        ],
        "default_reasoning_effort": capability.default_effort,
    }


def normalize_turn_reasoning(
    options: TurnReasoningOptions,
    capability: ReasoningCapability,
) -> TurnReasoningOptions:
    """按 ModelCard 能力校验并补齐单次思考选项。"""
    if not options.enabled:
        return TurnReasoningOptions()
    if not capability.supported:
        raise ValueError("当前模型不支持深度思考")
    effort = options.effort or capability.default_effort
    if effort and effort not in capability.efforts:
        raise ValueError(f"当前模型不支持思考等级：{effort}")
    return TurnReasoningOptions(True, effort)


@contextmanager
def bind_turn_reasoning(options: TurnReasoningOptions) -> Iterator[None]:
    """把选项限定在当前异步聊天任务，防止并发会话互相污染。"""
    token = _TURN_REASONING_OPTIONS.set(options)
    try:
        yield
    finally:
        _TURN_REASONING_OPTIONS.reset(token)


def current_turn_reasoning() -> TurnReasoningOptions | None:
    """读取当前聊天任务的按次思考选项。"""
    return _TURN_REASONING_OPTIONS.get()


__all__ = [
    "ReasoningCapability",
    "TurnReasoningOptions",
    "bind_turn_reasoning",
    "current_turn_reasoning",
    "normalize_turn_reasoning",
    "reasoning_capability",
    "reasoning_options_payload",
]
