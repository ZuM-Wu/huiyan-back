# -*- coding: utf-8 -*-
"""
统一 SSE 事件信封 — AI 对话流式输出的前端/App 兼容契约

响应头 text/event-stream，每条消息为 "data: {JSON}\n\n"，JSON 信封固定为:
    {"type": <事件类型>, "data": {...}}

事件类型枚举（公共文档 Docs/API/Public/AI对话SSE事件格式.md 同步维护）:
    meta            会话元信息（conversation_id/message_id/model/interface）
    content_delta   正文增量文本 {"text"}
    reasoning_delta 思维链增量文本 {"text"}（推理模型专用）
    tool_call       模型请求调用工具 {"name", "arguments"}（每个调用一条事件）
    tool_result     工具执行结果 {"name", "result", "denied"}（denied=True 表示无权限）
    done            回合结束 {"finish_reason", "usage"}
    error           错误 {"code", "message"}（统一错误码 + 中文描述）
"""
import json
from typing import Any, Dict

# 固定事件类型枚举（新增类型须同步公共接口文档）
EVENT_TYPES = frozenset({
    "meta", "content_delta", "reasoning_delta",
    "tool_call", "tool_result", "done", "error",
})


def build_event(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    构造统一事件信封

    参数:
        event_type: 事件类型（必须在 EVENT_TYPES 枚举内）
        data: 事件负载字典
    返回:
        {"type": ..., "data": {...}} 信封字典
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"未知的 SSE 事件类型: {event_type}")
    return {"type": event_type, "data": data or {}}


def sse_frame(envelope: Dict[str, Any]) -> str:
    """
    将事件信封序列化为 SSE 数据帧

    返回: "data: {JSON}\n\n" 格式字符串（ensure_ascii=False 保留中文）
    """
    return f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n"


def sse_format(event_type: str, data: Dict[str, Any]) -> str:
    """构造并序列化事件帧（build_event + sse_frame 的便捷组合，07 §1.17 登记签名）"""
    return sse_frame(build_event(event_type, data))


# 兼容别名：旧命名 sse_event 指向 sse_format（存量调用无需迁移）
sse_event = sse_format


def error_event(code: str, message: str) -> str:
    """构造统一错误事件帧（统一错误码 + 中文描述）"""
    return sse_event("error", {"code": code, "message": message})
