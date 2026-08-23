# -*- coding: utf-8 -*-
"""
AI 对话 API（农户端）

与管理员端同构（api/admin/ai_chat.py），差异点:
- 前缀 /api/v1/ai，鉴权走 check_farmer
- 全部端点受 ai.farmer_enabled 总开关控制，关闭时一律 404（对外不暴露功能存在）
- 强制 user_type="farmer"：工具桥按 audience=farmer/both 过滤，技能仅取农户可见预设
- 不提供模型切换（model 固定走全局默认），不透传 skill 之外的管理能力

接口:
- GET    /api/v1/ai/conversations              会话列表（分页）
- POST   /api/v1/ai/conversations              创建会话
- DELETE /api/v1/ai/conversations/{id}         删除会话（软删除）
- GET    /api/v1/ai/conversations/{id}/messages 历史消息分页
- GET    /api/v1/ai/skills                     农户可用技能列表
- POST   /api/v1/ai/chat                       发送消息（SSE 流式响应）
"""
import logging

from typing import List, Union

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.db.base import async_session_factory
from core.auth.middleware_chain import check_farmer
from core.response import ok
from services.ai import service
from services.ai.agent_loop import run_chat
from services.image_upload import save_uploaded_image

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/ai", tags=["农户AI对话"])

# SSE 流式响应头（与管理员端一致，禁用缓存与反向代理缓冲）
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def check_ai_enabled() -> None:
    """农户端 AI 总开关守卫：ai.farmer_enabled 非 "1" 时 404（功能不存在语义）"""
    async with async_session_factory() as db:
        settings = await service.get_ai_settings(db)
    if not settings["farmer_enabled"]:
        raise HTTPException(status_code=404, detail="Not Found")


class ConversationCreate(BaseModel):
    """创建会话请求"""
    title: str = ""
    skill_id: int = 0


class ChatRequest(BaseModel):
    """发送消息请求（支持纯文本或图片多模态内容；农户端不允许指定模型）"""
    conversation_id: int = 0
    content: Union[str, List]
    skill_id: int = 0
    attachments: List[dict] = Field(default_factory=list, max_length=4)


@router.get("/conversations", dependencies=[Depends(check_ai_enabled)])
async def list_conversations(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: None = Depends(check_farmer),
):
    """会话列表（按最后活跃时间倒序分页）"""
    async with async_session_factory() as db:
        data = await service.list_conversations(
            db, "farmer", request.state.user_id, page, page_size)
    return ok(data)


@router.post("/conversations", dependencies=[Depends(check_ai_enabled)])
async def create_conversation(data: ConversationCreate, request: Request,
                              _: None = Depends(check_farmer)):
    """创建新会话（可选携带农户可见技能）"""
    async with async_session_factory() as db:
        conv = await service.create_conversation(
            db, "farmer", request.state.user_id,
            title=data.title, skill_id=data.skill_id)
        await db.commit()
        return ok({"id": conv.id, "title": conv.title, "skill_id": conv.skill_id})


@router.delete("/conversations/{conversation_id}",
               dependencies=[Depends(check_ai_enabled)])
async def delete_conversation(conversation_id: int, request: Request,
                              _: None = Depends(check_farmer)):
    """删除会话（软删除，归属校验）"""
    async with async_session_factory() as db:
        success = await service.delete_conversation(
            db, "farmer", request.state.user_id, conversation_id)
        if not success:
            raise HTTPException(status_code=404, detail="会话不存在或无权访问")
        await db.commit()
    return ok(msg="会话已删除")


@router.get("/conversations/{conversation_id}/messages",
            dependencies=[Depends(check_ai_enabled)])
async def list_messages(
    conversation_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: None = Depends(check_farmer),
):
    """会话历史消息分页（时间正序）"""
    async with async_session_factory() as db:
        conv = await service.get_conversation(
            db, "farmer", request.state.user_id, conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在或无权访问")
        data = await service.list_messages(db, conversation_id, page, page_size)
        data["conversation"] = {"id": conv.id, "title": conv.title,
                                "skill_id": conv.skill_id}
    return ok(data)


@router.get("/skills", dependencies=[Depends(check_ai_enabled)])
async def list_skills(request: Request, _: None = Depends(check_farmer)):
    """农户可用技能列表（从 MCP 工具自动派生，audience=farmer/both）"""
    from services.ai import tool_bridge
    async with async_session_factory() as db:
        token = await tool_bridge.build_access_token("farmer", request.state.user_id, db)
        declarations = await tool_bridge.list_tool_declarations(token, db=db)
    skills = [{
        "id": i + 1,
        "name": d["function"]["name"],
        "description": d["function"]["description"] or "",
        "type": "tool",
    } for i, d in enumerate(declarations)]
    return ok({"list": skills})


@router.post("/upload/image", dependencies=[Depends(check_ai_enabled)])
async def upload_chat_image(
    request: Request,
    file: UploadFile = File(..., description="聊天图片"),
    _: None = Depends(check_farmer),
):
    """上传农户 AI 对话图片，返回可持久化附件元数据。"""
    attachment = await save_uploaded_image(file, source="ai_farmer")
    return ok(attachment, msg="上传成功")


@router.post("/chat", dependencies=[Depends(check_ai_enabled)])
async def chat(data: ChatRequest, request: Request, _: None = Depends(check_farmer)):
    """
    发送消息，SSE 流式返回统一事件信封（与管理员端同一套格式）

    强制 user_type="farmer"：工具桥仅暴露 audience=farmer/both 的系统工具，
    技能按农户可见范围解析；model 留空走全局默认，不接受前端指定。
    """
    generator = run_chat(
        user_type="farmer", user_id=request.state.user_id,
        content=data.content, conversation_id=data.conversation_id,
        skill_id=data.skill_id,
        attachments=data.attachments,
    )
    return StreamingResponse(generator, media_type="text/event-stream",
                             headers=SSE_HEADERS)
