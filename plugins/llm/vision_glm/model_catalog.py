# -*- coding: utf-8 -*-
"""智谱大模型 1.2 支持的对话补全模型及能力目录。"""
from typing import Any, Dict, List

DEFAULT_CHAT_MODEL = "glm-5.2"
DEFAULT_VISION_MODEL = "glm-4.6v-flash"


def _model(
    name: str,
    label: str,
    *,
    category: str = "text",
    tools: bool = False,
    reasoning: bool = False,
    vision: bool = False,
    deprecated: bool = False,
) -> Dict[str, Any]:
    """构造统一模型声明，能力字段供设置页与 Agent 共用。"""
    return {
        "name": name,
        "label": label,
        "category": category,
        "support_tools": tools,
        "support_reasoning": reasoning,
        "support_vision": vision,
        "deprecated": deprecated,
    }


# 清单以 2026-08-11 智谱官方模型概览为准。非对话补全模型不在此登记。
MODEL_CATALOG: List[Dict[str, Any]] = [
    _model("glm-5.2", "GLM-5.2", tools=True, reasoning=True),
    _model("glm-5.1", "GLM-5.1", tools=True, reasoning=True),
    _model("glm-5", "GLM-5", tools=True, reasoning=True),
    _model("glm-5-turbo", "GLM-5-Turbo", tools=True, reasoning=True),
    _model("glm-4.7", "GLM-4.7", tools=True, reasoning=True),
    _model("glm-4.7-flashx", "GLM-4.7-FlashX", tools=True, reasoning=True),
    _model("glm-4.6", "GLM-4.6", tools=True, reasoning=True),
    _model("glm-4.5-air", "GLM-4.5-Air", tools=True, reasoning=True),
    _model("glm-4.5-airx", "GLM-4.5-AirX", tools=True, reasoning=True),
    _model("glm-4-long", "GLM-4-Long"),
    _model("glm-4-flashx-250414", "GLM-4-FlashX-250414", tools=True),
    _model("glm-4.7-flash", "GLM-4.7-Flash", tools=True, reasoning=True),
    _model(
        "glm-4.5-flash", "GLM-4.5-Flash", tools=True,
        reasoning=True, deprecated=True,
    ),
    _model("glm-4-flash-250414", "GLM-4-Flash-250414", tools=True),
    _model("charglm-4", "CharGLM-4"),
    _model("emohaa", "Emohaa"),
    _model(
        "glm-5v-turbo", "GLM-5V-Turbo", category="vision",
        tools=True, reasoning=True, vision=True,
    ),
    _model(
        "glm-4.6v", "GLM-4.6V", category="vision",
        tools=True, reasoning=True, vision=True,
    ),
    _model(
        "glm-4.1v-thinking-flashx", "GLM-4.1V-Thinking-FlashX",
        category="vision", reasoning=True, vision=True,
    ),
    _model(
        "glm-4.6v-flash", "GLM-4.6V-Flash", category="vision",
        tools=True, reasoning=True, vision=True,
    ),
    _model(
        "glm-4.1v-thinking-flash", "GLM-4.1V-Thinking-Flash",
        category="vision", reasoning=True, vision=True,
    ),
    _model("glm-4v-flash", "GLM-4V-Flash", category="vision", vision=True),
]

_MODEL_INDEX = {item["name"]: item for item in MODEL_CATALOG}


def list_models() -> List[Dict[str, Any]]:
    """返回副本，避免调用方修改全局模型能力目录。"""
    return [dict(item) for item in MODEL_CATALOG]


def get_model_capabilities(model_name: str) -> Dict[str, Any]:
    """读取模型能力；手工输入的未知模型按最保守的纯文本能力处理。"""
    model = _MODEL_INDEX.get(str(model_name or "").strip().lower())
    if model:
        return dict(model)
    return _model(str(model_name or "").strip(), str(model_name or "").strip() or "未知模型")
