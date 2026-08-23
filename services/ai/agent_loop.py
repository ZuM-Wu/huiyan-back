# -*- coding: utf-8 -*-
"""
Agent 对话主循环 — AI 对话的编排核心

流程:
1. 会话定位/创建 → 用户消息落库
2. 驱动路由（resolve_llm_plugin）→ 工具桥导出当前身份可用工具声明
3. 组装 messages（技能预设系统提示词 + 历史上下文窗口）
4. 驱动流式输出，边执行边产出统一 SSE 事件帧:
   - content_delta/reasoning_delta 直接透传
   - 命中 tool_calls: 产出 tool_call 事件 → 工具桥执行（越权回填「没有权限」
     不中断对话）→ 产出 tool_result 事件 → tool 消息回填 → 再次调用驱动
   - 最多 max_tool_rounds 回合（配置项，默认 5），超限后收敛为无工具终答
5. 结束后落库 assistant 消息与 usage，产出 done 事件

产出物为 SSE 数据帧字符串（core/ai/sse.py 信封格式），API 层直接
StreamingResponse 透出，Web 前端与 App 共用同一套解析。
"""
import json
import logging
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from core.db.base import async_session_factory
from services.ai import service, tool_bridge
from services.ai.driver import resolve_llm_plugin
from services.ai.sse import sse_format, error_event

logger = logging.getLogger(__name__)

# tool_call 事件中 arguments 摘要的最大长度（防止超长参数刷屏）
_ARGS_SUMMARY_LIMIT = 500
# tool_result 事件中结果摘要的最大长度
_RESULT_SUMMARY_LIMIT = 1000
_VISION_TOOL_NAME = "vision_glm_analyze"
_VISION_PROMPT_LIMIT = 500
_IMAGE_DATA_URL_RE = re.compile(
    r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=\r\n]+", re.IGNORECASE)


def _extract_text_content(content: Union[str, list]) -> str:
    """从消息内容中提取纯文本（兼容 str 和多模态 list 格式）

    多模态 list 格式示例:
    [{"type": "text", "text": "描述图片"}, {"type": "image_url", ...}]
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return " ".join(parts)
    return str(content) if content else ""


def _extract_image_urls(content: Union[str, list]) -> List[str]:
    """提取多模态消息中的图片 Data URL，供视觉 MCP 内部调用。"""
    if not isinstance(content, list):
        return []
    images: List[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image_url":
            continue
        image_data = item.get("image_url") or {}
        url = image_data.get("url") if isinstance(image_data, dict) else ""
        if isinstance(url, str) and url:
            images.append(url)
    return images


def _sanitize_vision_text(value: Any) -> str:
    """移除视觉事件、上下文和历史中可能被异常回显的图片 Data URL。"""
    return _IMAGE_DATA_URL_RE.sub("[图片内容已省略]", str(value or ""))


def _prepare_user_input(content: Union[str, list], attachments: Optional[list]):
    """一次性提取当前输入并校验永久附件，降低主循环分支复杂度。"""
    text_content = _extract_text_content(content).strip()
    image_urls = _extract_image_urls(content)
    if not text_content and not image_urls:
        return "", [], [], ("empty_content", "消息内容不能为空")
    try:
        saved_attachments = service.normalize_image_attachments(attachments)
    except ValueError as exc:
        return text_content, image_urls, [], ("invalid_attachments", str(exc))
    return text_content, image_urls, saved_attachments, None


async def _model_supports_vision(plugin: Any, model_name: str) -> bool:
    """优先读取模型级能力，并兼容尚未实现能力门面的旧驱动。"""
    capability_loader = getattr(plugin, "llm_get_model_capabilities", None)
    if capability_loader:
        capabilities = await capability_loader(model_name)
        return bool(capabilities.get("support_vision"))
    return bool(getattr(plugin, "supports_vision_input", False))


async def _resolve_model_choice(plugin: Any, requested: str,
                                default_model: str) -> tuple[str, str]:
    """校验请求模型，返回 (最终模型名, 错误消息)。"""
    use_model = str(requested or default_model or "").strip()
    if not use_model:
        return "", "尚未配置默认模型，请先在 AI 设置中选择模型"
    list_models = getattr(plugin, "llm_list_models", None)
    if not callable(list_models):
        # 兼容旧的第三方驱动：没有模型目录门面时沿用驱动自身校验。
        return use_model, ""
    try:
        models = await list_models()
    except Exception:
        return "", "模型列表读取失败，请稍后重试"
    names = {
        str(item.get("name") or "") for item in models
        if isinstance(item, dict) and item.get("name")
    }
    if not names:
        return "", "当前模型接口没有可用模型"
    if use_model not in names:
        return "", f"所选模型不可用：{use_model}"
    return use_model, ""


async def _resolve_chat_driver(settings: Dict[str, Any], interface: str):
    """按设置解析已完成配置的驱动，并映射为 SSE 错误码。"""
    plugin, config, err = await resolve_llm_plugin(
        interface or settings["active_interface"], require_configured=True)
    if plugin:
        return plugin, config, ""
    error_code = "model_not_configured" if "配置" in err else "driver_unavailable"
    return None, {}, (error_code, err)


async def _load_chat_tools(token: Any, db: Any, enabled: bool) -> List[Dict[str, Any]]:
    """读取当前会话工具并隐藏仅供视觉预处理使用的工具。"""
    tools = await tool_bridge.list_tool_declarations(
        token, whitelist=None, db=db, system_tools_enabled=enabled)
    return [tool for tool in tools
            if tool.get("function", {}).get("name") != _VISION_TOOL_NAME]


async def run_chat(user_type: str, user_id: int, content: Union[str, list],
                   conversation_id: int = 0, model: str = "",
                   skill_id: int = 0, interface: str = "",
                   attachments: Optional[list] = None) -> AsyncIterator[str]:
    """
    执行一轮完整对话（含工具调用回合），产出统一 SSE 数据帧

    参数:
        user_type: 用户体系 admin/farmer
        user_id: 用户ID
        content: 用户消息内容（str 纯文本或 list 多模态格式）
        conversation_id: 会话ID（0=自动创建新会话）
        model: 指定模型（空=用全局默认模型）
        skill_id: 技能预设ID（0=不使用；新会话时写入会话记录）
        interface: 指定驱动接口（空=用全局激活接口，再空则自动回落）
        attachments: 已上传图片的永久 URL 与展示元数据（不含 Base64）
    """
    # 兼容多模态 list 格式：提取文本用于校验和落库，图片仅在本轮请求中使用
    text_content, image_urls, saved_attachments, input_error = _prepare_user_input(
        content, attachments)
    if input_error:
        yield error_event(*input_error)
        return
    save_content = text_content or "请分析我上传的图片。"

    async with async_session_factory() as db:
        # ---- 1. 配置与模型校验（失败时不创建会话、不落库消息） ----
        settings = await service.get_ai_settings(db)
        plugin, _config, driver_error = await _resolve_chat_driver(settings, interface)
        if driver_error:
            yield error_event(*driver_error)
            return
        use_model, model_error = await _resolve_model_choice(
            plugin, model, settings["default_model"])
        if model_error:
            yield error_event("model_unavailable", model_error)
            return

        # ---- 2. 会话定位/创建 + 用户消息落库 ----
        conv, err = await _locate_conversation(
            db, user_type, user_id, conversation_id, save_content, skill_id)
        if err:
            yield error_event("conversation_not_found", err)
            return
        user_msg = await service.save_message(
            db, conv.id, "user", save_content, attachments=saved_attachments)
        await service.touch_conversation(db, conv.id)
        await db.commit()

        # ---- 3. 驱动已经在落库前完成配置与模型校验 ----
        supports_vision = await _model_supports_vision(plugin, use_model)
        direct_vision = bool(image_urls and supports_vision)

        # ---- 4. 身份令牌 + 图片视觉 MCP 预处理 + 工具声明 ----
        token = await tool_bridge.build_access_token(user_type, user_id, db)

        # DeepSeek 等文本模型无法接收图片，因此先由视觉 MCP 完成一次分析，
        # 将工具结果作为标准 assistant/tool 消息回填，后续模型只接收文本结果。
        if image_urls and not direct_vision:
            async for frame in _preprocess_vision_message(
                    db, conv.id, user_msg.id, image_urls, text_content,
                    settings["system_tools_enabled"], token):
                yield frame

        skill = await service.get_skill(db, conv.skill_id or skill_id, user_type)
        # 工具列表完全由 MCP 的 audience/permission 过滤决定，不再使用技能白名单。
        tools = await _load_chat_tools(
            token, db, settings["system_tools_enabled"])

        # ---- 5. 组装 messages（系统提示词 + 历史上下文窗口） ----
        messages = await service.build_context_messages(
            db, conv.id, settings["context_limit"])
        if direct_vision:
            _replace_latest_user_content(
                messages, _build_direct_multimodal_content(text_content, image_urls))
        # 技能有 prompt 用技能的，否则用全局默认提示词
        sys_prompt = (skill or {}).get("system_prompt") or settings.get("system_prompt", "")
        if sys_prompt:
            messages.insert(0, {"role": "system", "content": sys_prompt})

        yield sse_format("meta", {
            "conversation_id": conv.id, "message_id": user_msg.id,
            "model": use_model, "interface": getattr(plugin, "name", ""),
        })

        # ---- 6. 工具调用回合循环 ----
        options = {
            "model": use_model,
            "thinking": {"type": "enabled"} if settings["thinking_enabled"] else {"type": "disabled"},
            "reasoning_effort": settings["reasoning_effort"],
            "temperature": settings.get("temperature"),
            "max_tokens": settings.get("max_tokens") or None,
        }
        max_rounds = settings["max_tool_rounds"]
        denied_tools: set[str] = set()  # 跨回合累积被拒工具名，后续回合自动移除
        for round_idx in range(max_rounds + 1):
            # 超限回合收敛：不再暴露工具，强制模型给出最终回答
            # 被拒工具从可用列表移除，避免模型反复重试无权工具
            if round_idx < max_rounds:
                round_tools = [t for t in tools
                               if t["function"]["name"] not in denied_tools]
            else:
                round_tools = []
            state = _new_round_state()

            async for frame in _stream_one_round(plugin, messages, round_tools,
                                                 options, state):
                yield frame
            if state["failed"]:
                await _save_partial(db, conv.id, state)
                return

            if state["tool_calls"]:
                # 落库 assistant（含 tool_calls）→ 执行工具 → 回填 tool 消息
                async for frame in _execute_tools(
                        db, conv.id, messages, state, token, denied_tools,
                        settings["system_tools_enabled"]):
                    yield frame
                await db.commit()
                continue

            # ---- 7. 正常终答：落库 assistant 消息与 usage，产出 done ----
            await service.save_message(
                db, conv.id, "assistant", state["content"],
                reasoning=state["reasoning"], token_usage=state["usage"])
            await service.touch_conversation(db, conv.id)
            await db.commit()
            yield sse_format("done", {
                "finish_reason": state["finish_reason"] or "stop",
                "usage": state["usage"] or {},
            })
            return

        # 防御兜底：收敛回合仍未终答（理论上无工具时不会命中）
        yield error_event("round_limit", "已达工具调用回合上限，对话未能收敛，请重试")


async def _preprocess_vision_message(
        db: Any, conversation_id: int, message_id: int, image_urls: List[str],
        prompt: str, system_tools_enabled: bool, token: Any) -> AsyncIterator[str]:
    """调用视觉 MCP 并把脱敏后的工具回合写入当前会话。"""
    vision_args = {"images": image_urls, "prompt": prompt}
    visible_args = json.dumps({
        "prompt": _sanitize_vision_text(prompt)[:_VISION_PROMPT_LIMIT],
    }, ensure_ascii=False)
    yield sse_format("tool_call", {
        "name": _VISION_TOOL_NAME, "arguments": visible_args,
    })
    if system_tools_enabled:
        vision_result, vision_denied = await tool_bridge.call_tool(
            _VISION_TOOL_NAME, vision_args, token, db=db,
            system_tools_enabled=system_tools_enabled)
    else:
        vision_result = "系统 MCP 工具已关闭，无法执行图片视觉分析。"
        vision_denied = True
    vision_result = _sanitize_vision_text(
        vision_result or "视觉 MCP 未返回分析结果")
    yield sse_format("tool_result", {
        "name": _VISION_TOOL_NAME,
        "result": vision_result[:_RESULT_SUMMARY_LIMIT],
        "denied": vision_denied,
    })

    vision_call_id = f"vision_{message_id}"
    vision_call = [{
        "id": vision_call_id,
        "type": "function",
        "function": {"name": _VISION_TOOL_NAME, "arguments": visible_args},
    }]
    await service.save_message(db, conversation_id, "assistant", "", tool_calls=vision_call)
    await service.save_message(
        db, conversation_id, "tool", vision_result, tool_call_id=vision_call_id)
    await db.commit()


def _build_direct_multimodal_content(prompt: str, image_urls: List[str]) -> List[Dict[str, Any]]:
    """构造仅供当前视觉驱动请求使用的受控多模态内容。"""
    content: List[Dict[str, Any]] = []
    if prompt:
        content.append({"type": "text", "text": prompt})
    for image_url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    if not prompt:
        content.append({"type": "text", "text": "请分析我上传的图片。"})
    return content


def _replace_latest_user_content(
        messages: List[Dict[str, Any]], content: List[Dict[str, Any]]) -> None:
    """只替换本轮最后一条用户消息，历史记录仍保持纯文本。"""
    for message in reversed(messages):
        if message.get("role") == "user":
            message["content"] = content
            return


async def _locate_conversation(db, user_type: str, user_id: int,
                               conversation_id: int, content: str, skill_id: int):
    """定位已有会话或创建新会话（新会话标题取首条消息前缀）"""
    if conversation_id:
        conv = await service.get_conversation(db, user_type, user_id, conversation_id)
        if not conv:
            return None, "会话不存在或无权访问"
        return conv, ""
    conv = await service.create_conversation(
        db, user_type, user_id, title=content[:30], skill_id=skill_id)
    return conv, ""


def _new_round_state() -> Dict[str, Any]:
    """单回合累积状态（正文/思维链/工具调用/usage/终止原因）"""
    return {
        "content": "", "reasoning": "", "tool_calls": [],
        "usage": None, "finish_reason": "", "failed": False,
    }


async def _stream_one_round(plugin, messages: List[Dict[str, Any]],
                            tools: List[Dict[str, Any]], options: Dict[str, Any],
                            state: Dict[str, Any]) -> AsyncIterator[str]:
    """
    执行一次驱动流式调用，透传增量事件并聚合回合状态

    驱动统一事件块 → SSE 数据帧的映射:
        content_delta/reasoning_delta 透传；tool_call 聚合进 state（执行由上层编排）；
        done 记录 finish_reason/usage；error 透传并标记回合失败
    """
    try:
        async for chunk in plugin.llm_chat_stream(messages, tools, options):
            chunk_type = chunk.get("type", "")
            data = chunk.get("data", {})
            if chunk_type == "content_delta":
                state["content"] += data.get("text", "")
                yield sse_format("content_delta", {"text": data.get("text", "")})
            elif chunk_type == "reasoning_delta":
                state["reasoning"] += data.get("text", "")
                yield sse_format("reasoning_delta", {"text": data.get("text", "")})
            elif chunk_type == "tool_call":
                state["tool_calls"] = data.get("tool_calls", [])
            elif chunk_type == "done":
                state["finish_reason"] = data.get("finish_reason", "")
                state["usage"] = data.get("usage") or None
            elif chunk_type == "error":
                state["failed"] = True
                yield sse_format("error", data)
                return
    except Exception:
        # 驱动契约要求归一化异常，此处兜底防御驱动实现缺陷
        logger.exception("[Agent循环] 驱动流式调用异常")
        state["failed"] = True
        yield error_event("driver_error", "模型调用异常，请稍后重试")


async def _execute_tools(db, conversation_id: int, messages: List[Dict[str, Any]],
                         state: Dict[str, Any], token: Any,
                         denied_tools: set,
                         system_tools_enabled: bool = True) -> AsyncIterator[str]:
    """
    执行本回合模型请求的全部工具调用

    - 产出 tool_call 事件（name/arguments 摘要）供前端展示调用过程
    - 工具桥执行（越权返回含工具名的拒绝文案，denied=True 标记透出）
    - 越权工具名写入 denied_tools 集合，后续回合自动从可用列表移除
    - 产出 tool_result 事件；assistant 与 tool 消息同步落库并回填 messages
    """
    calls = state["tool_calls"]
    # OpenAI 格式的 assistant.tool_calls（回填上下文 + 落库，换驱动零迁移）
    assistant_calls = [{
        "id": c.get("id") or f"call_{i}", "type": "function",
        "function": {"name": c.get("name", ""), "arguments": c.get("arguments", "")},
    } for i, c in enumerate(calls)]

    await service.save_message(
        db, conversation_id, "assistant", state["content"],
        reasoning=state["reasoning"], tool_calls=assistant_calls,
        token_usage=state["usage"])
    # 思考模式 + 工具调用时，后续请求须回传 reasoning_content
    assistant_msg: Dict[str, Any] = {
        "role": "assistant", "content": state["content"],
        "tool_calls": assistant_calls,
    }
    if state["reasoning"]:
        assistant_msg["reasoning_content"] = state["reasoning"]
    messages.append(assistant_msg)

    for call in assistant_calls:
        name = call["function"]["name"]
        raw_args = call["function"]["arguments"]
        yield sse_format("tool_call", {
            "name": name, "arguments": raw_args[:_ARGS_SUMMARY_LIMIT],
        })

        arguments = _parse_arguments(raw_args)
        result_text, denied = await tool_bridge.call_tool(
            name, arguments, token, db=db,
            system_tools_enabled=system_tools_enabled)
        if denied:
            denied_tools.add(name)  # 记录被拒工具，后续回合自动移除
        yield sse_format("tool_result", {
            "name": name, "result": result_text[:_RESULT_SUMMARY_LIMIT],
            "denied": denied,
        })

        # 结果以 tool 消息回填（越权同样回填「没有权限」文案，模型可自行说明）
        await service.save_message(
            db, conversation_id, "tool", result_text, tool_call_id=call["id"])
        messages.append({
            "role": "tool", "content": result_text, "tool_call_id": call["id"],
        })


def _parse_arguments(raw: str) -> Dict[str, Any]:
    """解析模型产出的工具参数 JSON（解析失败按空参数处理）"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        logger.warning("[Agent循环] 工具参数 JSON 解析失败: %s", raw[:200])
        return {}


async def _save_partial(db, conversation_id: int, state: Dict[str, Any]):
    """回合失败时保存已产出的部分内容（避免用户已看到的文本丢失）"""
    if state["content"] or state["reasoning"]:
        await service.save_message(
            db, conversation_id, "assistant", state["content"],
            reasoning=state["reasoning"], token_usage=state["usage"])
        await db.commit()
