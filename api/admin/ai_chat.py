# -*- coding: utf-8 -*-
"""
AI 对话 API（管理员端）

接口:
- GET    /api/admin/v1/ai/conversations              会话列表（分页）
- POST   /api/admin/v1/ai/conversations              创建会话
- DELETE /api/admin/v1/ai/conversations/{id}         删除会话（软删除）
- GET    /api/admin/v1/ai/conversations/{id}/messages 历史消息分页
- GET    /api/admin/v1/ai/skills                     可用技能列表（"/"选择面板数据源）
- GET    /api/admin/v1/ai/models                     可用模型列表（聊天页模型选择器）
- POST   /api/admin/v1/ai/chat                       发送消息（SSE 流式响应）

统一信封说明: 除 /chat 为 SSE 流式端点（text/event-stream，事件信封见
Docs/API/Public/AI对话SSE事件格式.md）外，其余接口均走 core/response.py 统一信封。
"""
import logging

from typing import List, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok
from services.ai import service
from services.ai.agent_loop import run_chat
from services.ai.driver import resolve_llm_plugin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/ai", tags=["AI对话"])

# SSE 流式响应头（禁用缓存与反向代理缓冲，保证打字机效果）
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ConversationCreate(BaseModel):
    """创建会话请求"""
    title: str = ""
    skill_id: int = 0


class ChatRequest(BaseModel):
    """发送消息请求（conversation_id=0 时自动创建新会话）

    content 兼容纯文本(str)和多模态格式(list)，多模态格式示例:
    [{"type": "text", "text": "描述这张图片"},
     {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]
    """
    conversation_id: int = 0
    content: Union[str, List]
    model: str = ""
    skill_id: int = 0
    attachments: List[dict] = Field(default_factory=list, max_length=4)


@router.get("/conversations", dependencies=[Depends(require_permission("ai:chat"))])
async def list_conversations(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """会话列表（按最后活跃时间倒序分页）"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        data = await service.list_conversations(
            db, "admin", request.state.user_id, page, page_size)
    return ok(data)


@router.post("/conversations", dependencies=[Depends(require_permission("ai:chat"))])
async def create_conversation(data: ConversationCreate, request: Request,
                              _: None = Depends(check_admin)):
    """创建新会话（可选携带技能预设）"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        conv = await service.create_conversation(
            db, "admin", request.state.user_id,
            title=data.title, skill_id=data.skill_id)
        await db.commit()
        return ok({"id": conv.id, "title": conv.title, "skill_id": conv.skill_id})


@router.delete("/conversations/{conversation_id}",
               dependencies=[Depends(require_permission("ai:chat"))])
async def delete_conversation(conversation_id: int, request: Request,
                              _: None = Depends(check_admin)):
    """删除会话（软删除，归属校验）"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        success = await service.delete_conversation(
            db, "admin", request.state.user_id, conversation_id)
        if not success:
            raise HTTPException(status_code=404, detail="会话不存在或无权访问")
        await db.commit()
    return ok(msg="会话已删除")


@router.get("/conversations/{conversation_id}/messages",
            dependencies=[Depends(require_permission("ai:chat"))])
async def list_messages(
    conversation_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: None = Depends(check_admin),
):
    """会话历史消息分页（时间正序）"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        conv = await service.get_conversation(
            db, "admin", request.state.user_id, conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在或无权访问")
        data = await service.list_messages(db, conversation_id, page, page_size)
        data["conversation"] = {"id": conv.id, "title": conv.title,
                                "skill_id": conv.skill_id}
    return ok(data)


@router.get("/skills", dependencies=[Depends(require_permission("ai:chat"))])
async def list_skills(request: Request, _: None = Depends(check_admin)):
    """可用能力列表（仅返回已登记且实际可用的 MCP 工具）。"""
    from services.ai import tool_bridge
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        settings = await service.get_ai_settings(db)
        token = await tool_bridge.build_access_token("admin", request.state.user_id, db)
        declarations = await tool_bridge.list_tool_declarations(
            token, db=db, system_tools_enabled=settings["system_tools_enabled"])
    skills = [{
        "id": i + 1,
        "name": d["function"]["name"],
        "description": d["function"]["description"] or "",
        "type": "tool",
    } for i, d in enumerate(declarations)]
    if skills:
        state = {
            "ready": True,
            "reason_code": "ready",
            "message": "已加载可用 MCP 能力",
        }
    elif not settings["system_tools_enabled"]:
        state = {
            "ready": False,
            "reason_code": "system_tools_disabled",
            "message": "系统 MCP 能力未登记启用",
        }
    else:
        state = {
            "ready": False,
            "reason_code": "no_cached_tools",
            "message": "暂无已测试并缓存的 MCP 工具",
        }
    return ok({"list": skills, **state})


@router.get("/models", dependencies=[Depends(require_permission("ai:chat"))])
async def list_models(_: None = Depends(check_admin)):
    """可用模型列表（聊天页模型选择器数据源，走当前激活/回落驱动接口）"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        settings = await service.get_ai_settings(db)
    plugin, _config, err = await resolve_llm_plugin(
        settings["active_interface"], require_configured=True)
    if not plugin:
        reason_code = "config_missing" if "配置" in err else "driver_unavailable"
        return ok({
            "list": [], "default_model": "", "ready": False,
            "reason_code": reason_code, "message": err,
        })
    try:
        models = await plugin.llm_list_models()
    except Exception as exc:
        logger.warning("[AI对话] 读取模型列表失败: %s", exc)
        return ok({
            "list": [], "default_model": "", "ready": False,
            "reason_code": "model_list_error", "message": "模型列表读取失败",
        })
    names = {str(item.get("name") or "") for item in models if isinstance(item, dict)}
    default_model = settings["default_model"] if settings["default_model"] in names else ""
    if not models:
        return ok({
            "list": [], "default_model": "", "ready": False,
            "reason_code": "no_models", "message": "当前模型接口没有可用模型",
        })
    return ok({
        "list": models, "default_model": default_model, "ready": True,
        "reason_code": "ready", "message": "模型列表已就绪",
    })


@router.post("/chat", dependencies=[Depends(require_permission("ai:chat"))])
async def chat(data: ChatRequest, request: Request, _: None = Depends(check_admin)):
    """
    发送消息，SSE 流式返回统一事件信封

    响应为 text/event-stream，每条 "data:" 为 {"type", "data"} JSON 信封，
    事件枚举: meta/content_delta/reasoning_delta/tool_call/tool_result/done/error
    """
    generator = run_chat(
        user_type="admin", user_id=request.state.user_id,
        content=data.content, conversation_id=data.conversation_id,
        model=data.model, skill_id=data.skill_id,
        attachments=data.attachments,
    )
    return StreamingResponse(generator, media_type="text/event-stream",
                             headers=SSE_HEADERS)
