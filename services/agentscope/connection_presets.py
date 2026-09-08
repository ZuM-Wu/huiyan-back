# -*- coding: utf-8 -*-
"""AgentScope AI 连接预设与模型发现辅助函数。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import httpx
from agentscope.model import DeepSeekChatModel, OpenAIChatModel, OpenAIResponseModel

from services.agentscope.model_capabilities import normalize_discovered_model
from services.agentscope.providers import GLMChatModel


OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
OPENAI_RESPONSES = "openai_responses"


@dataclass(frozen=True)
class ProtocolOption:
    """连接协议及其对应的 AgentScope Credential Provider。"""

    value: str
    label: str
    provider: str


@dataclass(frozen=True)
class ConnectionPreset:
    """连接池厂商预设；这里只保存公开配置，不包含任何密钥。"""

    key: str
    vendor: str
    provider: str
    protocol: str
    base_url: str
    input_types: tuple[str, ...] = ("text/plain",)
    output_types: tuple[str, ...] = ("text/plain",)
    context_size: int = 32768
    output_size: int = 4096
    support_tools: bool = True
    support_reasoning: bool = False
    support_vision: bool = False
    supported_protocols: tuple[ProtocolOption, ...] = ()

    def protocol_option(self, protocol: str | None = None) -> ProtocolOption:
        """解析请求协议；命名预设只有一个选项，因此无法被客户端覆盖。"""
        target = protocol or self.protocol
        options = self.supported_protocols or (
            ProtocolOption(self.protocol, self.protocol, self.provider),
        )
        for option in options:
            if option.value == target:
                return option
        raise ValueError("连接预设不支持所选协议")


_CHAT = ProtocolOption(
    OPENAI_CHAT_COMPLETIONS,
    "OpenAI Chat Completions",
    "openai_credential",
)
_RESPONSES = ProtocolOption(
    OPENAI_RESPONSES,
    "OpenAI Responses",
    "openai_responses_credential",
)
_DEEPSEEK_CHAT = replace(_CHAT, provider="deepseek_credential")
_GLM_CHAT = replace(_CHAT, provider="glm_credential")


PRESETS: tuple[ConnectionPreset, ...] = (
    ConnectionPreset("deepseek", "DeepSeek", _DEEPSEEK_CHAT.provider, _DEEPSEEK_CHAT.value, "https://api.deepseek.com", context_size=65536, output_size=8192, support_reasoning=True, supported_protocols=(_DEEPSEEK_CHAT,)),
    ConnectionPreset("glm", "智谱 GLM", _GLM_CHAT.provider, _GLM_CHAT.value, "https://open.bigmodel.cn/api/paas/v4", context_size=128000, output_size=8192, support_reasoning=True, supported_protocols=(_GLM_CHAT,)),
    ConnectionPreset("openai", "OpenAI", _RESPONSES.provider, _RESPONSES.value, "https://api.openai.com/v1", input_types=("text/plain", "image/*"), context_size=128000, output_size=16384, support_reasoning=True, support_vision=True, supported_protocols=(_RESPONSES,)),
    ConnectionPreset("qwen", "通义千问", _CHAT.provider, _CHAT.value, "https://dashscope.aliyuncs.com/compatible-mode/v1", context_size=131072, output_size=8192, support_reasoning=True, supported_protocols=(_CHAT,)),
    ConnectionPreset("kimi", "Kimi", _CHAT.provider, _CHAT.value, "https://api.moonshot.cn/v1", context_size=131072, output_size=8192, support_reasoning=True, supported_protocols=(_CHAT,)),
    ConnectionPreset("volcengine", "火山方舟", _CHAT.provider, _CHAT.value, "https://ark.cn-beijing.volces.com/api/v3", context_size=128000, output_size=8192, support_reasoning=True, supported_protocols=(_CHAT,)),
    ConnectionPreset("minimax", "MiniMax", _CHAT.provider, _CHAT.value, "https://api.minimaxi.com/v1", context_size=32768, output_size=8192, support_reasoning=True, supported_protocols=(_CHAT,)),
    ConnectionPreset("custom_openai", "自定义供应商", _CHAT.provider, _CHAT.value, "", context_size=32768, output_size=4096, supported_protocols=(_CHAT, _RESPONSES)),
)
PRESET_MAP = {item.key: item for item in PRESETS}
_INPUT_MODALITY_OPTIONS = {"text/plain", "image/*"}
_OUTPUT_MODALITY_OPTIONS = {"text/plain", "application/x-thinking"}
_MODEL_FETCH_ATTEMPTS = 2
_MODEL_FETCH_RETRY_DELAY_SECONDS = 0.2


def preset_payload(item: ConnectionPreset) -> dict[str, Any]:
    """生成可直接返回给前端的预设信息。"""
    return {
        "preset_key": item.key,
        "vendor": item.vendor,
        "provider": item.provider,
        "protocol": item.protocol,
        "base_url": item.base_url,
        "supported_protocols": [
            {"value": option.value, "label": option.label, "provider": option.provider}
            for option in item.supported_protocols
        ],
        "protocol_locked": len(item.supported_protocols) == 1,
        "capabilities": {
            "input_types": list(item.input_types),
            "output_types": list(item.output_types),
            "context_size": item.context_size,
            "output_size": item.output_size,
            "support_tools": item.support_tools,
            "support_reasoning": item.support_reasoning,
            "support_vision": "image/*" in item.input_types,
        },
    }


def resolve_preset(
    preset_key: str | None,
    provider: str | None = None,
    protocol: str | None = None,
) -> ConnectionPreset:
    """解析预设、协议和 Provider，并拒绝客户端提交的不一致组合。"""
    if preset_key and preset_key in PRESET_MAP:
        preset = PRESET_MAP[preset_key]
    elif provider == "deepseek_credential":
        preset = PRESET_MAP["deepseek"]
    elif provider == "glm_credential":
        preset = PRESET_MAP["glm"]
    elif provider in {"openai_credential", "openai_responses_credential"}:
        preset = PRESET_MAP["custom_openai"]
        protocol = protocol or (
            OPENAI_RESPONSES
            if provider == "openai_responses_credential"
            else OPENAI_CHAT_COMPLETIONS
        )
    else:
        raise ValueError("未选择有效的连接预设")
    option = preset.protocol_option(protocol)
    if provider and provider != option.provider:
        raise ValueError("Provider 与连接预设及协议不匹配")
    return replace(preset, provider=option.provider, protocol=option.value)


def default_model_payload(preset: ConnectionPreset, name: str, card: Any | None = None) -> dict[str, Any]:
    """把 AgentScope ModelCard 或远端模型 ID 统一成发现结果。"""
    if card is not None:
        return normalize_discovered_model({
            "model_name": card.name,
            "label": card.label,
            "input_types": list(card.input_types),
            "output_types": list(card.output_types),
            "context_size": card.context_size,
            "output_size": card.output_size,
            "support_tools": preset.support_tools,
            "support_reasoning": preset.support_reasoning,
            "support_vision": "image/*" in card.input_types,
            "parameter_schema": card.parameter_schema,
        })
    return normalize_discovered_model({
        "model_name": name,
        "label": name,
        "input_types": list(preset.input_types),
        "output_types": list(preset.output_types),
        "context_size": preset.context_size,
        "output_size": preset.output_size,
        "support_tools": preset.support_tools,
        "support_reasoning": preset.support_reasoning,
        "support_vision": preset.support_vision,
        "parameter_schema": _default_parameter_schema(preset),
    })


def _default_parameter_schema(preset: ConnectionPreset) -> dict[str, Any]:
    """返回供应商内置模型参数契约，避免远端目录缺少 Schema 时丢失表单。"""
    if preset.key == "deepseek":
        return DeepSeekChatModel.Parameters.model_json_schema()
    if preset.key == "glm":
        return GLMChatModel.Parameters.model_json_schema()
    if preset.protocol == OPENAI_RESPONSES:
        return OpenAIResponseModel.Parameters.model_json_schema()
    return OpenAIChatModel.Parameters.model_json_schema()


def parse_model_response(payload: Any, preset: ConnectionPreset) -> list[dict[str, Any]]:
    """解析 OpenAI 风格及常见简化形式的模型列表响应。"""
    raw_items = payload.get("data", []) if isinstance(payload, dict) else payload
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("data", [])
    if not isinstance(raw_items, list):
        return []
    result = []
    for item in raw_items:
        if isinstance(item, str):
            name = item.strip()
            metadata = {}
        elif isinstance(item, dict):
            name = str(item.get("id") or item.get("name") or "").strip()
            metadata = item
        else:
            continue
        if not name:
            continue
        model = default_model_payload(preset, name)
        model["label"] = str(metadata.get("label") or metadata.get("display_name") or name)
        for field in ("context_size", "output_size"):
            if isinstance(metadata.get(field), int) and metadata[field] > 0:
                model[field] = metadata[field]
        for field in ("input_types", "output_types"):
            if isinstance(metadata.get(field), list) and metadata[field]:
                values = [str(value) for value in metadata[field]]
                allowed = _INPUT_MODALITY_OPTIONS if field == "input_types" else _OUTPUT_MODALITY_OPTIONS
                if set(values).issubset(allowed):
                    model[field] = values
        for field in ("support_tools", "support_reasoning", "support_vision"):
            if field in metadata:
                model[field] = bool(metadata[field])
        if isinstance(metadata.get("parameter_schema"), dict) and metadata["parameter_schema"]:
            model["parameter_schema"] = metadata["parameter_schema"]
        result.append(normalize_discovered_model(model))
    return result


async def _fetch_remote_models(api_key: str, address: str) -> Any:
    """请求远端模型目录，并对短暂网络或上游故障做一次快速重试。"""
    if not api_key or not address:
        raise ValueError("API Key 和 Base URL 不能为空")
    response = None
    last_error: httpx.HTTPError | None = None
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(_MODEL_FETCH_ATTEMPTS):
            try:
                response = await client.get(
                    f"{address.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if response.status_code != 429 and response.status_code < 500:
                    break
            except httpx.HTTPError as exc:
                response = None
                last_error = exc
            if attempt + 1 < _MODEL_FETCH_ATTEMPTS:
                await asyncio.sleep(_MODEL_FETCH_RETRY_DELAY_SECONDS)
    if response is None:
        if isinstance(last_error, httpx.TimeoutException):
            raise RuntimeError("模型接口连接超时，请检查服务器网络或 Base URL") from last_error
        if isinstance(last_error, httpx.ConnectError):
            raise RuntimeError("无法连接模型接口，请检查服务器网络、代理或 Base URL") from last_error
        if isinstance(last_error, httpx.RequestError):
            raise RuntimeError("模型接口网络请求失败，请检查服务器网络或 Base URL") from last_error
        raise RuntimeError("模型接口连接失败，请检查服务器网络或 Base URL") from last_error
    if response.status_code in (401, 403):
        raise ValueError("API Key 无效或无权访问该模型接口")
    if response.status_code >= 400:
        raise RuntimeError(f"模型接口请求失败（HTTP {response.status_code}）")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("模型接口返回了无效数据") from exc


async def validate_connection(preset: ConnectionPreset, api_key: str, base_url: str) -> None:
    """只验证供应商接口和凭据，不要求远端目录替换本地能力目录。"""
    address = (base_url or preset.base_url).rstrip("/")
    payload = await _fetch_remote_models(api_key, address)
    if not parse_model_response(payload, preset):
        raise RuntimeError("模型接口连接成功，但未返回可用模型")


async def discover_models(preset: ConnectionPreset, api_key: str, base_url: str) -> list[dict[str, Any]]:
    """通过 GLM 本地目录或 Credential 对应的远端 `/models` 发现模型。"""
    if preset.key == "glm":
        return [default_model_payload(preset, card.name, card) for card in GLMChatModel.list_models()]
    address = (base_url or preset.base_url).rstrip("/")
    payload = await _fetch_remote_models(api_key, address)
    models = parse_model_response(payload, preset)
    if not models:
        raise RuntimeError("模型接口未返回可用模型")
    return models
