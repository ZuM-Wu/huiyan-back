# -*- coding: utf-8 -*-
"""智谱全系列对话补全模型驱动与视觉 MCP 工具入口。"""
import json
import logging
from typing import Any, AsyncIterator, Dict, List

import httpx

from core.plugins.llm_base import LlmBasePlugin
from plugins.llm.vision_glm.model_catalog import (
    DEFAULT_CHAT_MODEL,
    get_model_capabilities,
    list_models,
)
from plugins.llm.vision_glm.mcp_tools import (
    DEFAULT_BASE_URL,
    VisionInputError,
    _parse_max_bytes,
    _parse_timeout,
    validate_image_data,
    vision_glm_analyze,
)

logger = logging.getLogger(__name__)


class Plugin(LlmBasePlugin):
    """提供智谱文本、推理、图文对话和视觉前置能力。"""

    name = "vision_glm"
    title = "智谱大模型"
    version = "1.2.0"
    description = "智谱官方对话补全模型驱动，支持文本、推理、工具调用与视觉理解"
    module = "llm"
    supports_vision_input = True

    def get_config_schema(self) -> list:
        """声明 AI 设置页使用的配置字段。"""
        return [
            {"key": "api_key", "label": "智谱 API Key", "type": "password", "required": True,
             "placeholder": "请在智谱开放平台创建 API Key"},
            {"key": "base_url", "label": "接口地址", "type": "input", "required": False,
             "default": DEFAULT_BASE_URL},
            {"key": "timeout", "label": "超时时间（秒）", "type": "input", "required": False,
             "default": "60"},
            {"key": "max_image_mb", "label": "单张图片上限（MB）", "type": "input",
             "required": False, "default": "8"},
        ]

    def get_mcp_tools(self) -> list:
        """保留 DeepSeek 等非视觉驱动使用的自动图片分析工具。"""
        return [{
            "name": "analyze",
            "description": "分析一张或多张农业图片，返回作物、病虫害、长势或现场内容的文字描述。",
            "handler": vision_glm_analyze,
            "audience": "both",
            "permission_code": None,
        }]

    async def llm_list_models(self) -> List[Dict[str, Any]]:
        """返回 1.2 版本登记的全部智谱对话补全模型。"""
        return list_models()

    async def llm_get_model_capabilities(self, model_name: str) -> Dict[str, Any]:
        """按模型目录返回精确能力，未知模型使用保守能力。"""
        return get_model_capabilities(model_name)

    async def llm_chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        options: Dict[str, Any],
    ) -> AsyncIterator[Dict[str, Any]]:
        """调用智谱流式接口并归一化为系统 LLM 事件。"""
        config = self.config or {}
        api_key = str(config.get("api_key") or "").strip()
        if not api_key:
            yield _error_event("config_missing", "未配置智谱 API Key，请先在 AI 设置中完成配置")
            return

        try:
            _validate_message_images(messages, _parse_max_bytes(config.get("max_image_mb")))
        except VisionInputError as exc:
            yield _error_event("invalid_image", f"图片输入无效：{exc}")
            return

        payload = build_chat_payload(messages, tools, options)
        base_url = str(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        state = {"tool_calls": {}, "usage": None, "finish_reason": ""}
        try:
            async with httpx.AsyncClient(timeout=_parse_timeout(config.get("timeout"))) as client:
                async with client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        yield _http_error_event(response.status_code)
                        return
                    async for line in response.aiter_lines():
                        chunk = parse_sse_line(line)
                        if chunk is None:
                            continue
                        for event in apply_stream_chunk(chunk, state):
                            yield event
        except httpx.TimeoutException:
            yield _error_event("timeout", "智谱 GLM 接口请求超时，请稍后重试")
            return
        except httpx.HTTPError:
            logger.warning("[vision_glm] 流式请求智谱接口失败")
            yield _error_event("network_error", "无法连接智谱 GLM 接口，请检查服务端网络")
            return

        tool_calls = collect_tool_calls(state)
        if tool_calls:
            yield {"type": "tool_call", "data": {"tool_calls": tool_calls}}
        yield {"type": "done", "data": {
            "finish_reason": state["finish_reason"] or "stop",
            "usage": state["usage"] or {},
        }}

    async def test_connection(self, config: dict = None) -> dict:
        """调用智谱模型列表接口验证网络与 API Key。"""
        cfg = config or self.config or {}
        api_key = str(cfg.get("api_key") or "").strip()
        if not api_key:
            return {"success": False, "message": "未配置智谱 GLM API Key"}
        base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=_parse_timeout(cfg.get("timeout"))) as client:
                response = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
        except httpx.TimeoutException:
            return {"success": False, "message": "智谱 GLM 接口连接超时"}
        except httpx.HTTPError:
            logger.warning("[vision_glm] GLM 连通性测试网络异常")
            return {"success": False, "message": "无法连接智谱 GLM 接口"}
        if response.status_code == 200:
            return {"success": True, "message": "智谱 GLM 接口连接成功"}
        return {"success": False, "message": _http_error_event(response.status_code)["data"]["message"]}


def build_chat_payload(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """构造智谱 OpenAI 兼容流式图文请求体。"""
    model_name = str(options.get("model") or DEFAULT_CHAT_MODEL).strip()
    capabilities = get_model_capabilities(model_name)
    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": True,
    }
    if tools and capabilities["support_tools"]:
        payload["tools"] = tools
    if options.get("thinking") and capabilities["support_reasoning"]:
        payload["thinking"] = options["thinking"]
    if options.get("reasoning_effort") and capabilities["support_reasoning"]:
        payload["reasoning_effort"] = options["reasoning_effort"]
    if options.get("temperature") is not None:
        payload["temperature"] = options["temperature"]
    if options.get("max_tokens") is not None:
        payload["max_tokens"] = options["max_tokens"]
    return payload


def _validate_message_images(messages: List[Dict[str, Any]], max_bytes: int) -> None:
    """复用 MCP 图片约束校验流式消息中的全部图片。"""
    images: List[str] = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "image_url":
                continue
            image_data = item.get("image_url") or {}
            url = image_data.get("url") if isinstance(image_data, dict) else ""
            images.append(url)
    if images:
        validate_image_data(images, max_bytes)


def parse_sse_line(line: str) -> Any:
    """解析智谱 SSE 行；无效数据和结束标记返回 None。"""
    line = (line or "").strip()
    if not line.startswith("data:"):
        return None
    data = line[len("data:"):].strip()
    if not data or data == "[DONE]":
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        logger.warning("[vision_glm] 收到无法解析的 SSE 数据")
        return None


def apply_stream_chunk(chunk: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """归一化正文、推理、工具调用增量和 usage。"""
    events: List[Dict[str, Any]] = []
    if chunk.get("usage"):
        state["usage"] = chunk["usage"]
    choices = chunk.get("choices") or []
    if not choices:
        return events
    choice = choices[0]
    if choice.get("finish_reason"):
        state["finish_reason"] = choice["finish_reason"]
    delta = choice.get("delta") or {}
    if delta.get("reasoning_content"):
        events.append({"type": "reasoning_delta", "data": {
            "text": delta["reasoning_content"],
        }})
    if delta.get("content"):
        events.append({"type": "content_delta", "data": {"text": delta["content"]}})
    for item in delta.get("tool_calls") or []:
        index = item.get("index", 0)
        slot = state["tool_calls"].setdefault(
            index, {"id": "", "name": "", "arguments": ""})
        if item.get("id"):
            slot["id"] = item["id"]
        function = item.get("function") or {}
        if function.get("name"):
            slot["name"] = function["name"]
        if function.get("arguments"):
            slot["arguments"] += function["arguments"]
    return events


def collect_tool_calls(state: Dict[str, Any]) -> List[Dict[str, str]]:
    """按工具索引导出聚合后的 Function Calling 请求。"""
    calls = state.get("tool_calls") or {}
    return [calls[index] for index in sorted(calls) if calls[index].get("name")]


def _error_event(code: str, message: str) -> Dict[str, Any]:
    return {"type": "error", "data": {"code": code, "message": message}}


def _http_error_event(status_code: int) -> Dict[str, Any]:
    mapping = {
        401: "智谱 API Key 无效或已过期",
        402: "智谱服务额度不足",
        429: "智谱接口请求过于频繁，请稍后重试",
    }
    message = mapping.get(status_code, f"智谱 GLM 接口返回错误（HTTP {status_code}）")
    return _error_event(f"http_{status_code}", message)
