# -*- coding: utf-8 -*-
"""
DeepSeek 大模型驱动插件
流式调用 DeepSeek 官方 chat/completions 接口（境内可直连），
将平台 SSE chunk 归一化为 LlmBasePlugin 约定的统一事件块

支持模型:
    - deepseek-v4-flash: 快速模型，支持 function 工具调用与思考模式
    - deepseek-v4-pro: 强力模型，支持 function 工具调用与思考模式

思考模式（两模型均支持，通过 options 传入）:
    - thinking: {"type": "enabled"} / {"type": "disabled"}（默认 enabled）
    - reasoning_effort: "low" / "high" / "max"（默认 high）
    - 思考模式开启时 temperature 等采样参数被平台静默忽略
    - 思考模式 + 工具调用时，后续请求须回传 reasoning_content

配置项（hy_configuration，前缀 llm_deepseek.）:
    - api_key: DeepSeek 平台 API Key
    - base_url: 接口基础地址（默认官方 https://api.deepseek.com）
    - timeout: 请求超时秒数（默认 120，流式长回复需较大值）
"""
import json
import logging
from typing import Any, AsyncIterator, Dict, List

import httpx

from core.plugins.llm_base import LlmBasePlugin

logger = logging.getLogger(__name__)

# 官方接口基础地址（配置为空时回落）
DEFAULT_BASE_URL = "https://api.deepseek.com"
# 默认请求超时（秒）
DEFAULT_TIMEOUT = 120


class Plugin(LlmBasePlugin):
    """DeepSeek LLM 驱动插件"""

    name = "llm_deepseek"
    module = "llm"

    def get_config_schema(self) -> list:
        """接口管理页面动态渲染的配置表单定义"""
        return [
            {"key": "api_key", "label": "API Key", "type": "input", "required": True,
             "placeholder": "DeepSeek 开放平台 API Key（sk- 开头）"},
            {"key": "base_url", "label": "接口地址", "type": "input", "required": False,
             "placeholder": f"默认官方地址 {DEFAULT_BASE_URL}"},
            {"key": "timeout", "label": "超时时间（秒）", "type": "input", "required": False,
             "placeholder": f"流式请求超时，默认 {DEFAULT_TIMEOUT} 秒"},
        ]

    # ------------------------------------------------------------------
    # 契约方法：模型列表
    # ------------------------------------------------------------------
    async def llm_list_models(self) -> List[Dict[str, Any]]:
        """返回本驱动支持的模型清单（静态声明，与官方能力对齐）"""
        return [
            {"name": "deepseek-v4-flash", "label": "DeepSeek-V4-Flash",
             "support_tools": True, "support_reasoning": True, "support_vision": False},
            {"name": "deepseek-v4-pro", "label": "DeepSeek-V4-Pro",
             "support_tools": True, "support_reasoning": True, "support_vision": False},
        ]

    # ------------------------------------------------------------------
    # 契约方法：流式对话
    # ------------------------------------------------------------------
    async def llm_chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        options: Dict[str, Any],
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        流式调用 DeepSeek chat/completions 并归一化产出统一事件块

        平台异常在此捕获并归一化为 error 事件，不向上抛裸异常。
        """
        config = self.config or {}
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            yield {"type": "error", "data": {
                "code": "config_missing", "message": "未配置 DeepSeek API Key，请先在 AI 设置中完成配置"}}
            return

        url = self._chat_url(config)
        payload = self.build_payload(messages, tools, options)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            timeout = float(config.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT

        # 归一化状态：跨 chunk 聚合 tool_calls 增量与 usage
        state = {"tool_calls": {}, "usage": None, "finish_reason": ""}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        yield self._http_error_event(resp.status_code, body)
                        return
                    async for line in resp.aiter_lines():
                        chunk = self.parse_sse_line(line)
                        if chunk is None:
                            continue
                        for event in self.apply_chunk(chunk, state):
                            yield event
        except httpx.TimeoutException:
            yield {"type": "error", "data": {
                "code": "timeout", "message": "DeepSeek 接口请求超时，请稍后重试"}}
            return
        except httpx.HTTPError as exc:
            logger.warning(f"[llm_deepseek] 网络异常: {exc}")
            yield {"type": "error", "data": {
                "code": "network_error", "message": "无法连接 DeepSeek 接口，请检查网络或接口地址"}}
            return

        # 流结束：先产出聚合完成的工具调用（若有），再产出 done
        final_calls = self.collect_tool_calls(state)
        if final_calls:
            yield {"type": "tool_call", "data": {"tool_calls": final_calls}}
        yield {"type": "done", "data": {
            "finish_reason": state["finish_reason"] or "stop",
            "usage": state["usage"] or {}}}

    # ------------------------------------------------------------------
    # 请求体构造与 chunk 归一化（纯函数，便于单测）
    # ------------------------------------------------------------------
    @staticmethod
    def build_payload(
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        options: Dict[str, Any],
    ) -> Dict[str, Any]:
        """构造 DeepSeek chat/completions 请求体（OpenAI 兼容格式）"""
        payload: Dict[str, Any] = {
            "model": options.get("model") or "deepseek-v4-flash",
            "messages": messages,
            "stream": True,
            # 请求平台在最后一个 chunk 中携带 usage 统计
            "stream_options": {"include_usage": True},
        }
        # 两模型均支持工具调用，无条件携带 tools 声明
        if tools:
            payload["tools"] = tools
        # 思考模式参数（默认 enabled，由核心层 options 传入）
        thinking = options.get("thinking")
        if thinking:
            payload["thinking"] = thinking
        if options.get("reasoning_effort"):
            payload["reasoning_effort"] = options["reasoning_effort"]
        # 思考模式开启时 temperature 等采样参数被平台静默忽略，不写入保持请求体干净
        is_thinking = not (thinking and thinking.get("type") == "disabled")
        if is_thinking and options.get("temperature") is not None:
            pass  # 思考模式忽略 temperature
        elif not is_thinking and options.get("temperature") is not None:
            payload["temperature"] = options["temperature"]
        if options.get("max_tokens") is not None:
            payload["max_tokens"] = options["max_tokens"]
        return payload

    @staticmethod
    def parse_sse_line(line: str) -> Any:
        """
        解析一行 SSE 数据

        返回: chunk 字典；空行/注释/[DONE]/解析失败返回 None
        """
        line = (line or "").strip()
        if not line.startswith("data:"):
            return None
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            logger.warning(f"[llm_deepseek] SSE chunk 解析失败: {data[:200]}")
            return None

    @staticmethod
    def apply_chunk(chunk: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        将单个平台 chunk 归一化为统一事件列表，并更新聚合状态

        - delta.content → content_delta 事件
        - delta.reasoning_content → reasoning_delta 事件（思考模式开启时）
        - delta.tool_calls → 按 index 聚合进 state（流结束后统一产出 tool_call 事件）
        - finish_reason / usage → 记入 state（done 事件在流结束时产出）
        """
        events: List[Dict[str, Any]] = []
        # usage 仅出现在最后一个 chunk（choices 为空数组）
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
            events.append({"type": "reasoning_delta", "data": {"text": delta["reasoning_content"]}})
        if delta.get("content"):
            events.append({"type": "content_delta", "data": {"text": delta["content"]}})
        for item in delta.get("tool_calls") or []:
            Plugin._merge_tool_call_delta(item, state["tool_calls"])
        return events

    @staticmethod
    def _merge_tool_call_delta(item: Dict[str, Any], acc: Dict[int, Dict[str, str]]) -> None:
        """按 index 聚合 tool_calls 增量（id/name 首个 chunk 携带，arguments 分片拼接）"""
        index = item.get("index", 0)
        slot = acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if item.get("id"):
            slot["id"] = item["id"]
        func = item.get("function") or {}
        if func.get("name"):
            slot["name"] = func["name"]
        if func.get("arguments"):
            slot["arguments"] += func["arguments"]

    @staticmethod
    def collect_tool_calls(state: Dict[str, Any]) -> List[Dict[str, str]]:
        """按 index 顺序导出聚合完成的工具调用列表"""
        acc = state.get("tool_calls") or {}
        return [acc[i] for i in sorted(acc.keys()) if acc[i].get("name")]

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _chat_url(config: Dict[str, Any]) -> str:
        """拼接 chat/completions 完整地址（兼容自定义 base_url 末尾斜杠）"""
        base = str(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        return f"{base}/chat/completions"

    @staticmethod
    def _http_error_event(status_code: int, body: str) -> Dict[str, Any]:
        """将平台 HTTP 错误响应归一化为统一 error 事件"""
        # 尝试提取平台返回的错误描述
        detail = ""
        try:
            detail = (json.loads(body).get("error") or {}).get("message", "")
        except (json.JSONDecodeError, AttributeError):
            detail = body[:200]
        mapping = {
            401: "API Key 无效或已过期",
            402: "账户余额不足",
            429: "请求过于频繁，已触发平台限流",
        }
        message = mapping.get(status_code, f"DeepSeek 接口返回错误（HTTP {status_code}）")
        if detail:
            message = f"{message}：{detail}"
        return {"type": "error", "data": {"code": f"http_{status_code}", "message": message}}

    # ------------------------------------------------------------------
    # 连通性测试
    # ------------------------------------------------------------------
    async def test_connection(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        """调用平台 models 接口验证 API Key 与网络连通性"""
        cfg = config or self.config or {}
        api_key = str(cfg.get("api_key", "")).strip()
        if not api_key:
            return {"success": False, "message": "未配置 API Key"}
        base = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{base}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code == 200:
                return {"success": True, "message": "DeepSeek 接口连接成功"}
            if resp.status_code == 401:
                return {"success": False, "message": "API Key 无效或已过期"}
            return {"success": False, "message": f"接口返回异常状态码: {resp.status_code}"}
        except httpx.HTTPError as exc:
            logger.warning(f"[llm_deepseek] 连通性测试失败: {exc}")
            return {"success": False, "message": "无法连接 DeepSeek 接口，请检查网络或接口地址"}
