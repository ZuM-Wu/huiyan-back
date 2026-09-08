# -*- coding: utf-8 -*-
"""AgentScope 会话工具能力门禁与资源授权中间件。"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass

from agentscope.agent import Agent
from agentscope.app.storage import StorageBase
from agentscope.event import AgentEvent
from agentscope.formatter import (
    DeepSeekChatFormatter,
    FormatterBase,
    OpenAIChatFormatter,
    OpenAIResponseFormatter,
)
from agentscope.message import Msg
from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionBehavior, PermissionDecision
from sqlalchemy import select

from core.db.ai_resources import (
    AgentScopeConnectionPoolModel,
    AgentScopeModelCardModel,
)
from core.db.base import async_session_factory
from services.agentscope.deepseek_formatter import HuiyanDeepSeekVisionFormatter
from services.agentscope.image_inputs import prepare_model_messages, validate_incoming_images
from services.agentscope.reasoning import (
    ReasoningCapability,
    TurnReasoningOptions,
    current_turn_reasoning,
    normalize_turn_reasoning,
    reasoning_capability,
)


@dataclass(frozen=True)
class ModelCapabilities:
    """当前会话实际启用 ModelCard 的运行时能力。"""

    support_tools: bool = False
    input_types: tuple[str, ...] = ("text/plain",)
    provider: str = ""
    protocol: str = ""
    support_reasoning: bool = False
    reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str | None = None

    @property
    def support_vision(self) -> bool:
        return "image/*" in self.input_types

    @property
    def reasoning(self) -> ReasoningCapability:
        """返回可供按次聊天校验的思考能力。"""
        return ReasoningCapability(
            supported=self.support_reasoning,
            efforts=self.reasoning_efforts,
            default_effort=self.default_reasoning_effort,
        )


class BoundResourceMiddleware(MiddlewareBase):
    """仅在模型支持工具时保留 Toolkit，并授权其中的受控资源。"""

    def __init__(
        self,
        support_tools: bool,
        *,
        input_types: tuple[str, ...] = ("text/plain",),
        provider: str = "",
        protocol: str = "",
        support_reasoning: bool = False,
        reasoning_efforts: tuple[str, ...] = (),
        default_reasoning_effort: str | None = None,
        turn_reasoning: TurnReasoningOptions | None = None,
    ) -> None:
        self.capabilities = ModelCapabilities(
            support_tools=support_tools,
            input_types=input_types,
            provider=provider,
            protocol=protocol,
            support_reasoning=support_reasoning,
            reasoning_efforts=reasoning_efforts,
            default_reasoning_effort=default_reasoning_effort,
        )
        self.support_tools = support_tools
        self.turn_reasoning = turn_reasoning

    def _configure_formatter(self, agent: Agent) -> None:
        """在 Agent 处理新消息前同步格式化器能力，防止图片先被降级为链接文本。"""
        if getattr(agent, "model", None) is None:
            return
        formatter: FormatterBase
        if self.capabilities.provider == "deepseek_credential":
            formatter = (
                HuiyanDeepSeekVisionFormatter()
                if self.capabilities.support_vision
                else DeepSeekChatFormatter()
            )
        elif self.capabilities.protocol == "openai_responses":
            formatter = OpenAIResponseFormatter(
                input_types=list(self.capabilities.input_types),
            )
        else:
            formatter = OpenAIChatFormatter(
                input_types=list(self.capabilities.input_types),
            )
        setattr(agent.model, "formatter", formatter)

    def _configure_reasoning(self, agent: Agent) -> None:
        """仅替换本轮模型实例参数，避免思考选项写入 Session。"""
        if self.turn_reasoning is None or getattr(agent, "model", None) is None:
            return
        options = normalize_turn_reasoning(
            self.turn_reasoning,
            self.capabilities.reasoning,
        )
        parameters = getattr(agent.model, "parameters", None)
        if parameters is None:
            if options.enabled:
                raise ValueError("当前模型未提供深度思考参数")
            return
        values = parameters.model_dump()
        if "thinking_enable" not in values:
            if options.enabled:
                raise ValueError("当前模型未提供深度思考参数")
            return
        values["thinking_enable"] = options.enabled
        if "reasoning_effort" in values:
            values["reasoning_effort"] = options.effort
        elif options.effort:
            raise ValueError("当前模型未提供思考等级参数")
        agent.model.parameters = type(parameters).model_validate(values)

    async def on_reply(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        self._configure_formatter(agent)
        self._configure_reasoning(agent)
        await validate_incoming_images(
            input_kwargs.get("inputs"),
            self.capabilities.support_vision,
        )
        if not self.support_tools:
            agent.toolkit.clear()
        async for item in next_handler(**input_kwargs):
            yield item

    async def on_model_call(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable],
    ):
        """仅替换传给供应商的消息副本，Agent 上下文仍保留稳定图片引用。"""
        del agent
        call_kwargs = dict(input_kwargs)
        call_kwargs["messages"] = await prepare_model_messages(input_kwargs["messages"])
        return await next_handler(**call_kwargs)

    async def on_check_permission(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[PermissionDecision]],
    ) -> PermissionDecision:
        del agent, next_handler
        tool = input_kwargs.get("tool")
        tool_name = getattr(tool, "name", "未知工具")
        if not self.support_tools:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="当前模型未启用工具能力。",
                decision_reason="model_card_tools_disabled",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"工具 {tool_name} 已由当前会话资源策略授权。",
            decision_reason="workspace_resource_bound",
        )


async def _model_supports_tools(
    storage: StorageBase,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> bool:
    """按会话当前模型查询启用中的 ModelCard，缺失时采用关闭策略。"""
    session = await storage.get_session(user_id, agent_id, session_id)
    model_config = session.config.chat_model_config if session else None
    credential_id = getattr(model_config, "credential_id", "")
    model_name = getattr(model_config, "model", "")
    if not credential_id or not model_name:
        return False

    query = (
        select(AgentScopeModelCardModel.support_tools)
        .join(
            AgentScopeConnectionPoolModel,
            AgentScopeConnectionPoolModel.id == AgentScopeModelCardModel.connection_id,
        )
        .where(
            AgentScopeConnectionPoolModel.credential_id == credential_id,
            AgentScopeConnectionPoolModel.status == 1,
            AgentScopeModelCardModel.model_name == model_name,
            AgentScopeModelCardModel.status == 1,
        )
    )
    async with async_session_factory() as db:
        value = (await db.execute(query)).scalar_one_or_none()
    return bool(value)


async def get_model_capabilities(
    storage: StorageBase,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> ModelCapabilities:
    """按 Credential 和模型名读取启用中的 ModelCard，缺失时全部关闭。"""
    session = await storage.get_session(user_id, agent_id, session_id)
    model_config = session.config.chat_model_config if session else None
    credential_id = getattr(model_config, "credential_id", "")
    model_name = getattr(model_config, "model", "")
    if not credential_id or not model_name:
        return ModelCapabilities()
    query = (
        select(
            AgentScopeModelCardModel.support_tools,
            AgentScopeModelCardModel.input_types,
            AgentScopeModelCardModel.support_vision,
            AgentScopeModelCardModel.support_reasoning,
            AgentScopeModelCardModel.parameter_schema,
            AgentScopeConnectionPoolModel.provider,
            AgentScopeConnectionPoolModel.protocol,
        )
        .join(
            AgentScopeConnectionPoolModel,
            AgentScopeConnectionPoolModel.id == AgentScopeModelCardModel.connection_id,
        )
        .where(
            AgentScopeConnectionPoolModel.credential_id == credential_id,
            AgentScopeConnectionPoolModel.status == 1,
            AgentScopeModelCardModel.model_name == model_name,
            AgentScopeModelCardModel.status == 1,
        )
    )
    async with async_session_factory() as db:
        row = (await db.execute(query)).one_or_none()
    if row is None:
        return ModelCapabilities()
    try:
        input_types = [str(value) for value in json.loads(row.input_types or "[]")]
    except (TypeError, json.JSONDecodeError):
        input_types = ["text/plain"]
    # 迁移前的旧记录可能只设置 support_vision；运行时兼容保证重启前也能正确处理。
    if bool(row.support_vision) and "image/*" not in input_types:
        input_types.append("image/*")
    try:
        parameter_schema = json.loads(row.parameter_schema or "{}")
    except (TypeError, json.JSONDecodeError):
        parameter_schema = {}
    reasoning = reasoning_capability(
        bool(row.support_reasoning),
        parameter_schema,
    )
    return ModelCapabilities(
        support_tools=bool(row.support_tools),
        input_types=tuple(input_types),
        provider=row.provider,
        protocol=row.protocol,
        support_reasoning=reasoning.supported,
        reasoning_efforts=reasoning.efforts,
        default_reasoning_effort=reasoning.default_effort,
    )


async def build_agent_middlewares(
    storage: StorageBase,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[MiddlewareBase]:
    """构建当前会话的工具与图片能力中间件。"""
    capabilities = await get_model_capabilities(
        storage,
        user_id,
        agent_id,
        session_id,
    )
    turn_reasoning = current_turn_reasoning()
    if turn_reasoning is not None:
        turn_reasoning = normalize_turn_reasoning(
            turn_reasoning,
            capabilities.reasoning,
        )
    return [BoundResourceMiddleware(
        capabilities.support_tools,
        input_types=capabilities.input_types,
        provider=capabilities.provider,
        protocol=capabilities.protocol,
        support_reasoning=capabilities.support_reasoning,
        reasoning_efforts=capabilities.reasoning_efforts,
        default_reasoning_effort=capabilities.default_reasoning_effort,
        turn_reasoning=turn_reasoning,
    )]


__all__ = [
    "BoundResourceMiddleware",
    "ModelCapabilities",
    "build_agent_middlewares",
    "get_model_capabilities",
]
