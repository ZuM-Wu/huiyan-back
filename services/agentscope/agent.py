# -*- coding: utf-8 -*-
"""慧眼运行时 Agent，只接收自动装配的 MCP 与显式安装的 Skill。"""
from __future__ import annotations

from typing import Any

from agentscope.agent import Agent
from agentscope.tool import Toolkit


def bound_workspace_toolkit(toolkit: Toolkit | None) -> Toolkit:
    """丢弃 AgentScope 默认工具，仅复制工作区 basic 组的绑定资源。"""
    if toolkit is None:
        return Toolkit()
    for group in toolkit.tool_groups:
        if group.name == "basic":
            return Toolkit(
                skills_or_loaders=list(group.skills_or_loaders),
                mcps=list(group.mcps),
            )
    return Toolkit()


class HuiyanAgent(Agent):
    """在 Agent 初始化边界过滤平台工具并保留会话资源。"""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        model: Any,
        *,
        toolkit: Toolkit | None = None,
        middlewares: list[Any] | None = None,
        state: Any = None,
        offloader: Any = None,
        model_config: Any = None,
        context_config: Any = None,
        react_config: Any = None,
        injection_config: Any = None,
    ) -> None:
        super().__init__(
            name=name,
            system_prompt=system_prompt,
            model=model,
            toolkit=bound_workspace_toolkit(toolkit),
            middlewares=middlewares,
            state=state,
            offloader=offloader,
            model_config=model_config,
            context_config=context_config,
            react_config=react_config,
            injection_config=injection_config,
        )


__all__ = ["HuiyanAgent", "bound_workspace_toolkit"]
