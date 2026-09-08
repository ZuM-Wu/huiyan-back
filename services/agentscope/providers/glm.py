# -*- coding: utf-8 -*-
"""智谱 GLM 的 AgentScope Credential/Chat Completions Model。"""
from __future__ import annotations

from typing import Literal, Type

from pydantic import ConfigDict, Field

from agentscope.credential import OpenAICredential
from agentscope.model import ModelCard, OpenAIChatModel


# 仅登记近期仍使用 Chat Completions、支持工具调用与思考的通用及视觉模型。
# 旧专项模型和已弃用型号不进入新建 ModelCard 的候选目录，避免能力误判。
_GLM_MODELS = (
    ("glm-5.2", "GLM-5.2", False),
    ("glm-5.1", "GLM-5.1", False),
    ("glm-5", "GLM-5", False),
    ("glm-5-turbo", "GLM-5-Turbo", False),
    ("glm-4.7", "GLM-4.7", False),
    ("glm-4.7-flashx", "GLM-4.7-FlashX", False),
    ("glm-4.7-flash", "GLM-4.7-Flash", False),
    ("glm-4.6", "GLM-4.6", False),
    ("glm-5v-turbo", "GLM-5V-Turbo", True),
    ("glm-4.6v", "GLM-4.6V", True),
    ("glm-4.6v-flash", "GLM-4.6V-Flash", True),
)


class GLMCredential(OpenAICredential):
    """智谱 OpenAI 兼容接口凭据。"""

    model_config = ConfigDict(title="智谱 GLM API")
    type: Literal["glm_credential"] = "glm_credential"  # type: ignore[assignment]
    base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4")

    @classmethod
    def get_chat_model_class(cls) -> Type[OpenAIChatModel]:
        """使用智谱实际提供的 Chat Completions 兼容接口。"""
        return GLMChatModel


class GLMChatModel(OpenAIChatModel):
    """复用 AgentScope Chat formatter，并解析智谱思考与工具调用增量。"""

    type: Literal["glm_chat"] = "glm_chat"  # type: ignore[assignment]

    @classmethod
    def list_models(cls, custom_yaml_dir: str | None = None) -> list[ModelCard]:
        del custom_yaml_dir
        parameter_schema = cls.Parameters.model_json_schema()
        cards = []
        for name, label, support_vision in _GLM_MODELS:
            inputs = ["text/plain", "image/*"] if support_vision else ["text/plain"]
            cards.append(ModelCard(
                name=name,
                label=label,
                status="active",
                input_types=inputs,
                output_types=["text/plain", "application/x-thinking"],
                context_size=128_000,
                output_size=8_192,
                parameter_schema=parameter_schema,
                parameters_overrides={},
            ))
        return cards
