# -*- coding: utf-8 -*-
"""ModelCard 输入模态与兼容能力字段归一化。"""
from __future__ import annotations

from collections.abc import Iterable


IMAGE_MODALITY = "image/*"
TEXT_MODALITY = "text/plain"


def _unique(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        value = str(raw).strip()
        if value and value not in result:
            result.append(value)
    return result


def reconcile_input_capabilities(
    submitted_types: Iterable[str] | None,
    submitted_vision: bool | None,
    *,
    input_types_supplied: bool,
    fallback_types: Iterable[str] | None = None,
    fallback_vision: bool = False,
) -> tuple[list[str], bool]:
    """归一输入能力；显式模态优先，旧视觉布尔值仅用于未提交模态时。"""
    if input_types_supplied:
        input_types = _unique(submitted_types)
    else:
        input_types = _unique(fallback_types)
        vision_hint = fallback_vision if submitted_vision is None else bool(submitted_vision)
        if vision_hint and IMAGE_MODALITY not in input_types:
            input_types.append(IMAGE_MODALITY)
        elif submitted_vision is False:
            input_types = [value for value in input_types if value != IMAGE_MODALITY]
    return input_types, IMAGE_MODALITY in input_types


def normalize_discovered_model(model: dict) -> dict:
    """把远端发现的视觉信号写回输入模态，输出始终保持字段一致。"""
    normalized = dict(model)
    input_types = _unique(normalized.get("input_types"))
    if bool(normalized.get("support_vision")) and IMAGE_MODALITY not in input_types:
        input_types.append(IMAGE_MODALITY)
    normalized["input_types"] = input_types
    normalized["support_vision"] = IMAGE_MODALITY in input_types
    return normalized


__all__ = [
    "IMAGE_MODALITY",
    "TEXT_MODALITY",
    "normalize_discovered_model",
    "reconcile_input_capabilities",
]
