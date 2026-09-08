# -*- coding: utf-8 -*-
"""AgentScope 新消息按次参数入口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from agentscope.app._manager import ChatRunRegistry
from agentscope.app._service import ChatService
from agentscope.app.deps import (
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_storage,
)
from agentscope.app.storage import StorageBase
from agentscope.message import Msg

from services.agentscope.reasoning import (
    TurnReasoningOptions,
    bind_turn_reasoning,
    normalize_turn_reasoning,
)
from services.agentscope.tool_policy import get_model_capabilities


router = APIRouter(prefix="/chat", tags=["chat"])


class TurnChatRequest(BaseModel):
    """只用于新用户消息的按次模型参数请求。"""

    agent_id: str = Field(min_length=1, description="Agent 标识")
    session_id: str = Field(min_length=1, description="会话标识")
    input: Msg | list[Msg] = Field(description="本轮用户消息")
    thinking_enable: bool = Field(default=False, description="是否启用深度思考")
    reasoning_effort: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="本轮思考等级",
    )

    @field_validator("input")
    @classmethod
    def validate_input(cls, value: Msg | list[Msg]) -> Msg | list[Msg]:
        """空消息数组不能触发无输入的后台运行。"""
        if isinstance(value, list) and not value:
            raise ValueError("本轮消息不能为空")
        return value


class TurnChatResponse(BaseModel):
    """按次聊天任务已进入后台运行。"""

    status: str = "started"
    session_id: str


@router.post(
    "/turn",
    response_model=TurnChatResponse,
    summary="使用按次模型参数触发聊天",
)
async def chat_turn(
    body: TurnChatRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    chat_service: ChatService = Depends(get_chat_service),
    chat_run_registry: ChatRunRegistry = Depends(get_chat_run_registry),
) -> TurnChatResponse:
    """校验当前 ModelCard 后，以任务上下文触发一次新消息回复。"""
    session = await storage.get_session(user_id, body.agent_id, body.session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")

    capabilities = await get_model_capabilities(
        storage,
        user_id,
        body.agent_id,
        body.session_id,
    )
    requested = TurnReasoningOptions(
        enabled=body.thinking_enable,
        effort=body.reasoning_effort,
    )
    try:
        reasoning = normalize_turn_reasoning(requested, capabilities.reasoning)
    except ValueError as exc:
        code = (
            status.HTTP_400_BAD_REQUEST
            if body.thinking_enable and not capabilities.support_reasoning
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    async def run_turn() -> None:
        with bind_turn_reasoning(reasoning):
            await chat_service.run(
                user_id=user_id,
                session_id=body.session_id,
                agent_id=body.agent_id,
                input_msg=body.input,
            )

    run_coro = run_turn()
    try:
        chat_run_registry.spawn(run_coro, session_id=body.session_id)
    except RuntimeError as exc:
        run_coro.close()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return TurnChatResponse(session_id=body.session_id)


__all__ = ["TurnChatRequest", "TurnChatResponse", "chat_turn", "router"]
