# -*- coding: utf-8 -*-
"""OpenAI Responses 协议的 AgentScope Credential。"""
from typing import Literal, Type

from pydantic import ConfigDict

from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIResponseModel


class OpenAIResponsesCredential(OpenAICredential):
    """使用 OpenAI Responses API 的凭据类型。"""

    model_config = ConfigDict(title="OpenAI Responses API")
    type: Literal["openai_responses_credential"] = "openai_responses_credential"  # type: ignore[assignment]

    @classmethod
    def get_chat_model_class(cls) -> Type[OpenAIResponseModel]:
        """固定返回 Responses 模型，避免会话配置误选 Chat Completions。"""
        return OpenAIResponseModel
