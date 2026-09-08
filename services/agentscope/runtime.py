# -*- coding: utf-8 -*-
"""AgentScope Service 嵌入配置。

该模块只负责组装 AgentScope 公开服务对象，不在导入阶段连接数据库。真正
的连接、工作区和消息总线生命周期由 FastAPI lifespan 进入 AgentScope 子应用
时管理。
"""
from __future__ import annotations

import logging
import traceback
from pathlib import Path
from types import MethodType
from typing import Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from agentscope.app import create_app
from agentscope.app.deps import get_current_user_id
from agentscope.app.storage import AsyncSQLAlchemyStorage

from core.auth.middleware_chain import check_admin, check_farmer, security
from core.config import BASE_DIR, settings
from core.db.base import engine

from .agent import HuiyanAgent
from .adapters import HuiyanWorkspaceManager
from .message_bus import MySQLMessageBus
from .platform_credentials import platform_credential_access_policy
from .providers import GLMCredential, OpenAIResponsesCredential
from .shutdown import ShutdownDrainMiddleware
from .team_router import team_router
from .tool_policy import build_agent_middlewares
from .turn_router import router as turn_router
from .upload_router import router as upload_router

logger = logging.getLogger(__name__)


def _exception_diagnostics(error: BaseException) -> tuple[str, str]:
    """提取脱敏异常链和 HTTP 状态，不读取异常正文或请求数据。"""
    names: list[str] = []
    status = "none"
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        names.append(type(current).__name__)
        for value in (
            getattr(current, "status_code", None),
            getattr(current, "status", None),
            getattr(getattr(current, "response", None), "status_code", None),
        ):
            if isinstance(value, int):
                status = str(value)
                break
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        wrapped = current.__cause__ or current.__context__
        if wrapped is not None:
            pending.append(wrapped)
    return " > ".join(names), status


def _log_chat_failure(
    label: str,
    error: Exception,
    session_id: str,
    agent_id: str | None = None,
) -> None:
    """记录模型失败的类型和栈帧，同时避免泄露正文、密钥和请求参数。"""
    chain, status = _exception_diagnostics(error)
    stack = "".join(traceback.format_tb(error.__traceback__)) or "<no traceback>"
    logger.error(
        "%s: exception_chain=%s http_status=%s agent_id=%r session_id=%r\n%s",
        label,
        chain,
        status,
        agent_id,
        session_id,
        stack,
    )


class AgentScopeRuntime:
    """保存 AgentScope 共享后端资源，供健康检查和生命周期使用。"""

    def __init__(self) -> None:
        workdir = Path(BASE_DIR) / "runtime" / "agentscope-workspaces"
        self.storage = AsyncSQLAlchemyStorage(
            settings.DATABASE_URL,
            create_tables=False,
            auto_migrate=False,
            engine=engine,
        )
        self.message_bus = MySQLMessageBus()
        self.workspace_manager = HuiyanWorkspaceManager(
            str(workdir),
        )
        self.service_version = "2.0.7"


agentscope_runtime = AgentScopeRuntime()


async def _runtime_middlewares(user_id: str, agent_id: str, session_id: str):
    """通过运行时共享 Storage 构建当前会话的工具门禁。"""
    return await build_agent_middlewares(
        agentscope_runtime.storage,
        user_id,
        agent_id,
        session_id,
    )


def install_chat_failure_logging() -> None:
    """为 AgentScope ChatService 安装幂等且不记录敏感正文的诊断钩子。"""
    service = getattr(agentscope_app.state, "chat_service", None)
    if service is None or getattr(service, "_huiyan_failure_logging", False):
        return

    original_report = service._report_failure
    original_close = service._close_failed_reply

    async def report_with_logging(
        _service,
        user_id: str,
        session_id: str,
        agent_id: str,
        error: Exception,
        team_ctx=None,
        worker_name=None,
    ) -> None:
        del _service
        _log_chat_failure(
            "AgentScope 会话初始化失败",
            error,
            session_id,
            agent_id,
        )
        await original_report(
            user_id,
            session_id,
            agent_id,
            error,
            team_ctx,
            worker_name,
        )

    async def close_with_logging(
        _service,
        session_id: str,
        reply_msg,
        error: Exception,
    ) -> None:
        del _service
        _log_chat_failure("AgentScope 模型回复失败", error, session_id)
        await original_close(session_id, reply_msg, error)

    service._report_failure = MethodType(report_with_logging, service)
    service._close_failed_reply = MethodType(close_with_logging, service)
    service._huiyan_failure_logging = True


async def _jwt_user_id(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """把现有管理员/农户 JWT 映射为 AgentScope 用户标识。"""
    try:
        await check_admin(request, credentials)
        return f"admin:{request.state.user_id}"
    except Exception as admin_error:
        # 管理员校验失败后尝试农户校验；农户令牌不会绕过管理员 RBAC，
        # 资源仍按 AgentScope owner-isolation 分离。
        try:
            await check_farmer(request, credentials)
            return f"farmer:{request.state.user_id}"
        except Exception:
            raise admin_error


agentscope_app = create_app(
    storage=agentscope_runtime.storage,
    message_bus=agentscope_runtime.message_bus,
    workspace_manager=agentscope_runtime.workspace_manager,
    enable_index_worker=False,
    extra_credentials=[GLMCredential, OpenAIResponsesCredential],
    extra_agent_middlewares=_runtime_middlewares,
    resource_access_policy=platform_credential_access_policy,
    custom_agent_cls=HuiyanAgent,
    title="慧眼护农 AgentScope Service",
    version=agentscope_runtime.service_version,
)
agentscope_app.add_middleware(ShutdownDrainMiddleware)
agentscope_app.dependency_overrides[get_current_user_id] = _jwt_user_id
agentscope_app.include_router(team_router)
agentscope_app.include_router(upload_router)
agentscope_app.include_router(turn_router)

logger.info("AgentScope Service 已组装，版本=%s，默认消息总线=MySQL", agentscope_runtime.service_version)
