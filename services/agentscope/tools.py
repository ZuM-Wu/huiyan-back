# -*- coding: utf-8 -*-
"""慧眼农业工具到 AgentScope ToolBase 的桥接。

系统原有农业能力已经以 MCP 声明方式登记。本模块只负责把声明转换为
AgentScope 工具，并在每次调用前重新执行项目自己的权限策略。工具实现仍
复用原 handler，因此不会复制天气、产区和农户业务规则。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from core.db.base import async_session_factory
from services.mcp.external import build_access_token, call_tool

logger = logging.getLogger(__name__)


def _parse_user(user_id: str) -> tuple[str, int]:
    """解析 AgentScope 的 admin:{id}/farmer:{id} 用户标识。"""
    kind, _, raw_id = str(user_id or "").partition(":")
    if kind not in {"admin", "farmer"} or not raw_id.isdigit():
        raise ValueError("AgentScope 用户标识非法")
    return kind, int(raw_id)


def _policy_allowed(policy: dict[str, Any], token: Any) -> bool:
    """使用新策略表执行 audience 与管理员 RBAC 检查。"""
    claims = getattr(token, "claims", None) or {}
    audience = _normalize_audience(policy.get("audience", "admin"))
    user_type = claims.get("user_type")
    if user_type not in audience:
        return False
    if user_type != "admin":
        return True
    from services.mcp.middleware import _normalized_admin, _permission_granted
    if _normalized_admin(claims):
        return True
    code = str(policy.get("permission_code") or "")
    return _permission_granted(code, getattr(token, "scopes", None) or [])


def _normalize_audience(value: Any) -> set[str]:
    """把历史 ``both`` 值规范化为两个真实用户体系。"""
    audience = {item.strip() for item in str(value or "admin").split(",")}
    if "both" in audience:
        audience.discard("both")
        audience.update(("admin", "farmer"))
    return audience


class HuiyanTool(ToolBase):
    """将一个系统或外部 MCP 声明实现为 AgentScope ToolBase。"""

    def __init__(
        self,
        *,
        source_name: str,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        token: Any,
        policy: dict[str, Any],
        handler: Any = None,
    ) -> None:
        super().__init__()
        self.name = name
        self.description = description
        self.input_schema = input_schema or {
            "type": "object",
            "properties": {},
        }
        self.is_read_only = bool(policy.get("is_read_only", False))
        self.is_concurrency_safe = bool(policy.get("is_concurrency_safe", False))
        self._source_name = source_name
        self._token = token
        self._policy = policy
        self._handler = handler

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        del tool_input, context
        allowed = _policy_allowed(self._policy, self._token)
        return PermissionDecision(
            behavior=(PermissionBehavior.ALLOW if allowed else PermissionBehavior.DENY),
            message=("已通过慧眼平台工具权限策略" if allowed else "当前用户无权调用该工具"),
            decision_reason="huiyan_tool_policy",
        )

    async def call(self, **kwargs: Any) -> ToolChunk:
        """系统工具始终经过 MCP 中间件，旧对象也不能绕过注销、权限和响应预算。"""
        if not _policy_allowed(self._policy, self._token):
            return ToolChunk(
                content=[TextBlock(text=f"没有权限调用工具：{self.name}")],
                state=ToolResultState.DENIED,
            )

        result, failed = await call_tool(self._source_name, kwargs, self._token)
        return ToolChunk(content=[TextBlock(text=result)],
                         state=ToolResultState.ERROR if failed else ToolResultState.SUCCESS)


async def _load_policies(db) -> dict[str, dict[str, Any]]:
    """读取新工具策略表；单个旧库尚未迁移时回落为空映射。"""
    try:
        rows = (await db.execute(text(
            "SELECT tool_name, audience, permission_code, is_read_only, "
            "is_concurrency_safe, status FROM hy_agentscope_tool_policy"
        ))).mappings().all()
    except Exception:
        return {}
    return {
        row["tool_name"]: dict(row)
        for row in rows
        if int(row.get("status") or 0) == 1
    }


async def sync_tool_policies(
    db,
    declarations: list[dict[str, Any]],
) -> None:
    """在调用方事务中为新工具补齐安全默认策略。

    只使用 ``INSERT IGNORE`` 登记缺失项，不覆盖管理员已经调整的策略，
    也不在内部提交事务。启动和插件生命周期负责决定提交或回滚。
    """
    for declaration in declarations:
        name = "<unknown>"
        try:
            name = str(declaration.get("name") or "")
            if not name:
                continue
            policy = _default_policy(declaration, name)
            await db.execute(text(
                "INSERT IGNORE INTO hy_agentscope_tool_policy "
                "(tool_name, audience, permission_code, is_read_only, "
                "is_concurrency_safe, area_scoped, schema_json, status) "
                "VALUES (:name, :audience, :permission, 0, 0, 0, :schema, 1)"
            ), {
                "name": name,
                "audience": policy["audience"],
                "permission": policy["permission_code"],
                "schema": json.dumps(
                    declaration.get("parameters")
                    or {"type": "object", "properties": {}},
                    ensure_ascii=False,
                ),
            })
        except Exception:
            logger.exception("AgentScope 工具策略同步失败: tool=%s", name)
            continue


def _default_policy(declaration: dict[str, Any], name: str) -> dict[str, Any]:
    """从旧 MCP 声明生成迁移期间的安全默认策略。"""
    audience = ",".join(sorted(_normalize_audience(
        declaration.get("audience", "admin"),
    )))
    return {
        "tool_name": name,
        "audience": audience,
        "permission_code": declaration.get("permission_code") or "",
        "is_read_only": False,
        "is_concurrency_safe": False,
        "status": 1,
    }


def list_agent_tool_declarations() -> list[dict[str, Any]]:
    """唯一来源为系统 MCP 已验证的声明，禁止绕过注册表回填核心能力。"""
    from core.config import settings
    from services.mcp.registry import list_registered_tool_declarations
    return list_registered_tool_declarations() if settings.MCP_ENABLED else []


async def _system_mcp_enabled(user_id: str) -> bool:
    """系统工具必须由用户配置并启用系统 MCP 资源后才进入会话。"""
    try:
        from services.agentscope.runtime import agentscope_runtime
        records = await agentscope_runtime.storage.list_mcps(user_id)
    except Exception:
        return False
    return any(
        bool(getattr(record, "enabled", False))
        and getattr(getattr(record, "client", None), "name", "")
        == "huiyan_system_mcp"
        for record in records
    )


async def build_agent_tools(user_id: str, agent_id: str, session_id: str) -> list[ToolBase]:
    """按用户、Agent 和 Session 构建本轮可用的 AgentScope 工具。"""
    del agent_id, session_id
    user_type, numeric_id = _parse_user(user_id)
    async with async_session_factory() as db:
        token = await build_access_token(user_type, numeric_id, db)
        declarations = (
            list_agent_tool_declarations()
            if await _system_mcp_enabled(user_id)
            else []
        )
        policies = await _load_policies(db)

        tools: list[ToolBase] = []
        for declaration in declarations:
            source_name = str(declaration.get("name") or "")
            if not source_name:
                continue
            name = source_name
            policy = policies.get(name) or _default_policy(declaration, name)
            from services.mcp.middleware import _capability_allowed
            from services.mcp.registry import get_tool_meta
            if not _policy_allowed(policy, token) or not _capability_allowed(get_tool_meta(name), token):
                continue
            try:
                tools.append(HuiyanTool(
                    source_name=source_name,
                    name=name,
                    description=declaration["description"],
                    input_schema=declaration["parameters"],
                    token=token,
                    policy=policy,
                    handler=declaration.get("handler"),
                ))
            except Exception:
                logger.exception("跳过无法构造的 AgentScope 工具: tool=%s", name)

        # 外部 MCP 保留现有缓存发现/个人凭据校验，作为新运行时的过渡适配。
        try:
            from services.mcp.external import list_tool_declarations

            external = await list_tool_declarations(
                token,
                db=db,
                system_tools_enabled=False,
            )
        except Exception:
            external = []
        for declaration in external:
            source_name = "<unknown>"
            try:
                function = declaration.get("function") or {}
                source_name = str(function.get("name") or "")
                if not source_name:
                    continue
                policy = _default_policy({"audience": user_type}, source_name)
                tools.append(HuiyanTool(
                    source_name=source_name,
                    name=source_name,
                    description=str(function.get("description") or ""),
                    input_schema=function.get("parameters") or {},
                    token=token,
                    policy=policy,
                ))
            except Exception:
                logger.exception("跳过无法构造的外部 MCP 工具: tool=%s", source_name)
        return tools
