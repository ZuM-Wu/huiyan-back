# -*- coding: utf-8 -*-
"""
AI 对话服务 — 会话/消息管理与全局对话参数

职责:
- 全局对话参数读取（hy_configuration 的 ai.* 键，带默认值）
- 会话 CRUD（软删除，按 user_type+user_id 严格隔离归属）
- 消息落库与历史分页
- 上下文窗口截断（按条数上限，避免超长上下文撑爆模型输入）
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, func

from core.db.ai import AiConversationModel, AiMessageModel, AiSkillModel
from core.config_manager import ConfigManager

logger = logging.getLogger(__name__)

_VISION_TOOL_NAME = "vision_glm_analyze"
_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# 全局对话参数默认值（hy_configuration 未配置时回落）
AI_SETTING_DEFAULTS = {
    "ai.active_interface": ("", "AI 当前激活的模型驱动接口（空=自动选择第一个启用插件）"),
    "ai.default_model": ("deepseek-v4-flash", "AI 默认对话模型"),
    "ai.vision_model": ("glm-4.6v-flash", "AI 识图 MCP 默认视觉模型"),
    "ai.max_tool_rounds": ("5", "AI 工具调用最大回合数"),
    "ai.context_limit": ("20", "AI 上下文窗口消息条数上限"),
    "ai.farmer_enabled": ("0", "农户端 AI 对话开关（0=关闭, 1=开启）"),
    "ai.system_tools_enabled": ("0", "系统 MCP 工具总开关（0=关闭, 1=开启）"),
    "ai.thinking_enabled": ("1", "AI 思考模式开关（0=关闭, 1=开启）"),
    "ai.reasoning_effort": ("high", "AI 思考强度（low/high/max）"),
    "ai.system_prompt": (
        "你是慧眼护农智慧农业系统的AI助手。你可以帮助用户查询天气信息、"
        "管理生产区域和地块、查看农户信息、处理任务和管理知识库。"
        "请用简洁专业的中文回答用户问题，合理使用可用工具获取实时数据。",
        "AI 默认系统提示词（未选择技能时使用）",
    ),
    "ai.temperature": ("0.7", "AI 模型温度（0-2，思考模式开启时被平台忽略）"),
    "ai.max_tokens": ("0", "AI 最大输出 token 数（0=不限制）"),
}


async def get_ai_settings(db) -> Dict[str, Any]:
    """
    读取全局对话参数（未配置的键回落默认值）

    返回: {"active_interface", "default_model", "vision_model", "max_tool_rounds",
           "context_limit", "farmer_enabled", "system_tools_enabled",
           "thinking_enabled", "reasoning_effort", "system_prompt",
           "temperature", "max_tokens"}
    """
    cm = ConfigManager()
    settings: Dict[str, Any] = {}
    for full_key, (default, _desc) in AI_SETTING_DEFAULTS.items():
        value = await cm.get(full_key, db)
        settings[full_key[len("ai."):]] = value if value is not None else default
    # 数值型参数转 int（非法值回落默认）
    for int_key, fallback in (("max_tool_rounds", 5), ("context_limit", 20)):
        try:
            settings[int_key] = max(1, int(settings[int_key]))
        except (TypeError, ValueError):
            settings[int_key] = fallback
    # 浮点型参数转 float（非法值回落默认）
    try:
        settings["temperature"] = float(settings["temperature"])
    except (TypeError, ValueError):
        settings["temperature"] = 0.7
    # max_tokens 转 int（0=不限制）
    try:
        settings["max_tokens"] = int(settings["max_tokens"])
    except (TypeError, ValueError):
        settings["max_tokens"] = 0
    settings["farmer_enabled"] = str(settings["farmer_enabled"]) == "1"
    settings["system_tools_enabled"] = str(settings["system_tools_enabled"]) == "1"
    settings["thinking_enabled"] = str(settings["thinking_enabled"]) == "1"
    settings["reasoning_effort"] = str(settings["reasoning_effort"] or "high")
    settings["system_prompt"] = str(settings.get("system_prompt") or "")
    return settings


# ------------------------------------------------------------------
# 会话管理（按 user_type + user_id 严格隔离归属）
# ------------------------------------------------------------------

async def create_conversation(db, user_type: str, user_id: int,
                              title: str = "", skill_id: int = 0) -> AiConversationModel:
    """创建新会话"""
    conv = AiConversationModel(
        user_type=user_type, user_id=user_id,
        title=(title or "新对话")[:120], skill_id=skill_id, status=1,
    )
    db.add(conv)
    await db.flush()
    return conv


async def get_conversation(db, user_type: str, user_id: int,
                           conversation_id: int) -> Optional[AiConversationModel]:
    """按归属查询会话（不属于当前用户或已删除时返回 None）"""
    return (await db.execute(
        select(AiConversationModel).where(
            AiConversationModel.id == conversation_id,
            AiConversationModel.user_type == user_type,
            AiConversationModel.user_id == user_id,
            AiConversationModel.status == 1,
        )
    )).scalar_one_or_none()


async def list_conversations(db, user_type: str, user_id: int,
                             page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    """分页查询会话列表（按最后活跃时间倒序）"""
    base = select(AiConversationModel).where(
        AiConversationModel.user_type == user_type,
        AiConversationModel.user_id == user_id,
        AiConversationModel.status == 1,
    )
    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0
    rows = (await db.execute(
        base.order_by(AiConversationModel.update_time.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {
        "total": total,
        "list": [{
            "id": c.id, "title": c.title, "skill_id": c.skill_id,
            "create_time": str(c.create_time), "update_time": str(c.update_time),
        } for c in rows],
    }


async def delete_conversation(db, user_type: str, user_id: int, conversation_id: int) -> bool:
    """软删除会话（归属校验不通过返回 False）"""
    conv = await get_conversation(db, user_type, user_id, conversation_id)
    if not conv:
        return False
    conv.status = 2
    return True


async def touch_conversation(db, conversation_id: int, title: str = ""):
    """刷新会话最后活跃时间；title 非空时同步更新标题（取首条用户消息前缀）"""
    values: Dict[str, Any] = {"update_time": datetime.now()}
    if title:
        values["title"] = title[:120]
    await db.execute(
        update(AiConversationModel)
        .where(AiConversationModel.id == conversation_id)
        .values(**values)
    )


# ------------------------------------------------------------------
# 消息管理
# ------------------------------------------------------------------

async def save_message(db, conversation_id: int, role: str, content: str,
                       reasoning: str = "", tool_calls: Optional[list] = None,
                       tool_call_id: str = "", token_usage: Optional[dict] = None,
                       *, attachments: Optional[list] = None) -> AiMessageModel:
    """落库一条消息（结构化字段序列化为 JSON 字符串）"""
    msg = AiMessageModel(
        conversation_id=conversation_id, role=role, content=content or "",
        attachments=(json.dumps(attachments, ensure_ascii=False)
                     if attachments else None),
        reasoning=reasoning or None,
        tool_calls=json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
        tool_call_id=tool_call_id or "",
        token_usage=json.dumps(token_usage, ensure_ascii=False) if token_usage else None,
    )
    db.add(msg)
    await db.flush()
    return msg


async def list_messages(db, conversation_id: int,
                        page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    """分页查询会话历史消息（时间正序，供前端渲染消息流）"""
    base = select(AiMessageModel).where(
        AiMessageModel.conversation_id == conversation_id
    )
    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0
    rows = (await db.execute(
        base.order_by(AiMessageModel.id.asc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {"total": total, "list": [_message_to_dict(m) for m in rows]}


def _message_to_dict(m: AiMessageModel) -> Dict[str, Any]:
    """消息 ORM → 前端展示字典（JSON 字段反序列化）"""
    content = _unwrap_singleton_result(m.content) if m.role == "tool" else m.content
    return {
        "id": m.id, "role": m.role, "content": content,
        "attachments": _load_json_list(getattr(m, "attachments", None)),
        "reasoning": m.reasoning or "",
        "tool_calls": json.loads(m.tool_calls) if m.tool_calls else [],
        "tool_call_id": m.tool_call_id,
        "usage": json.loads(m.token_usage) if m.token_usage else {},
        "create_time": str(m.create_time),
    }


# ------------------------------------------------------------------
# 上下文窗口
# ------------------------------------------------------------------

async def build_context_messages(db, conversation_id: int, limit: int) -> List[Dict[str, Any]]:
    """
    组装发给模型的历史上下文（OpenAI 风格 messages，不含 system）

    - 按条数上限取最近 limit 条（时间正序返回）
    - 截断修复：窗口开头的 tool 消息缺失前置 assistant.tool_calls 会被平台拒绝，
      逐条剔除窗口头部的 tool 消息直到首条非 tool 消息
    """
    rows = (await db.execute(
        select(AiMessageModel).where(
            AiMessageModel.conversation_id == conversation_id
        ).order_by(AiMessageModel.id.desc()).limit(limit)
    )).scalars().all()
    rows = list(reversed(rows))

    # 剔除窗口头部孤立的 tool 消息（其配对的 assistant 已被截断）
    while rows and rows[0].role == "tool":
        rows.pop(0)

    messages: List[Dict[str, Any]] = []
    for m in rows:
        content = _unwrap_singleton_result(m.content) if m.role == "tool" else m.content
        item: Dict[str, Any] = {"role": m.role, "content": content}
        if m.role == "assistant" and m.tool_calls:
            item["tool_calls"] = json.loads(m.tool_calls)
            # 思考模式 + 工具调用时，后续请求须回传 reasoning_content
            if m.reasoning:
                item["reasoning_content"] = m.reasoning
        if m.role == "tool" and m.tool_call_id:
            item["tool_call_id"] = m.tool_call_id
        messages.append(item)
    return _normalize_vision_context(messages)


def normalize_image_attachments(attachments: Optional[list]) -> List[Dict[str, Any]]:
    """校验并清洗前端提交的永久图片附件元数据。"""
    values = attachments or []
    if not isinstance(values, list) or len(values) > 4:
        raise ValueError("每条消息最多上传 4 张图片")
    normalized: List[Dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, dict):
            raise ValueError("图片附件格式无效")
        url = str(raw.get("url") or "").strip()
        mime_type = str(raw.get("mime_type") or "").lower().strip()
        if not url or url.lower().startswith("data:"):
            raise ValueError("图片附件必须使用上传后的永久 URL")
        if not (url.startswith("/upload/") or _HTTP_URL_RE.match(url)):
            raise ValueError("图片附件 URL 无效")
        if mime_type not in _IMAGE_MIME_TYPES:
            raise ValueError("图片附件类型无效")
        try:
            size = max(0, int(raw.get("size") or 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("图片附件大小无效") from exc
        name = re.sub(r"[\x00-\x1f\x7f]", "", str(raw.get("name") or "图片"))[:255]
        normalized.append({
            "type": "image", "url": url[:2048], "name": name or "图片",
            "mime_type": mime_type, "size": size,
        })
    return normalized


def _load_json_list(raw: Any) -> list:
    """读取历史 JSON 数组，损坏数据按空数组处理。"""
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _unwrap_singleton_result(value: Any) -> str:
    """兼容 FastMCP 历史上把字符串包装成 {result: text} 的结果。"""
    text_value = str(value or "")
    try:
        parsed = json.loads(text_value)
    except (TypeError, json.JSONDecodeError):
        return text_value
    if (isinstance(parsed, dict) and set(parsed) == {"result"}
            and isinstance(parsed["result"], str)):
        return parsed["result"]
    return text_value


def _normalize_vision_context(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把 Agent 主动识图记录折叠为用户文本，避免伪工具回合进入思考模型。"""
    normalized: List[Dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        calls = message.get("tool_calls") or []
        is_vision_round = (
            message.get("role") == "assistant" and calls
            and all(call.get("function", {}).get("name") == _VISION_TOOL_NAME
                    for call in calls)
        )
        if not is_vision_round:
            if (message.get("role") == "tool"
                    and str(message.get("tool_call_id") or "").startswith("vision_")):
                logger.warning("[AI服务] 丢弃孤立的识图工具结果: %s", message.get("tool_call_id"))
            else:
                normalized.append(message)
            index += 1
            continue

        call_ids = {str(call.get("id") or "") for call in calls}
        results: List[str] = []
        next_index = index + 1
        while next_index < len(messages):
            candidate = messages[next_index]
            if (candidate.get("role") != "tool"
                    or str(candidate.get("tool_call_id") or "") not in call_ids):
                break
            results.append(_unwrap_singleton_result(candidate.get("content")))
            next_index += 1
        if results and normalized and normalized[-1].get("role") == "user":
            original = str(normalized[-1].get("content") or "")
            normalized[-1]["content"] = (
                f"{original}\n\n[识图结果]\n" + "\n".join(results)
            ).strip()
        else:
            logger.warning("[AI服务] 丢弃不完整的识图合成回合")
        index = next_index
    return normalized


# ------------------------------------------------------------------
# 技能预设
# ------------------------------------------------------------------

async def get_skill(db, skill_id: int, audience: str) -> Optional[Dict[str, Any]]:
    """
    查询启用中的技能预设（按身份过滤可见范围）

    参数:
        audience: 当前用户体系 admin/farmer（技能 audience=both 双端可见）
    返回:
        {"id", "name", "system_prompt", "tools"}；不可见/不存在返回 None
    """
    if not skill_id:
        return None
    skill = (await db.execute(
        select(AiSkillModel).where(
            AiSkillModel.id == skill_id, AiSkillModel.status == 1
        )
    )).scalar_one_or_none()
    if not skill:
        return None
    if skill.audience not in (audience, "both"):
        return None
    tools: List[str] = []
    if skill.tools:
        try:
            tools = json.loads(skill.tools) or []
        except json.JSONDecodeError:
            logger.warning("[AI服务] 技能 %s 工具白名单 JSON 解析失败", skill_id)
    return {
        "id": skill.id, "name": skill.name,
        "system_prompt": skill.system_prompt, "tools": tools,
    }



