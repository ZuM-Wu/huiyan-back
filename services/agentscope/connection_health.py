# -*- coding: utf-8 -*-
"""供应商连接健康检查服务。"""
from __future__ import annotations

from dataclasses import dataclass
from core.time_utils import china_now

from sqlalchemy import select

from core.db.ai_resources import AgentScopeConnectionPoolModel
from core.db.base import async_session_factory
from services.agentscope.connection_presets import resolve_preset, validate_connection
from services.agentscope.platform_credentials import PLATFORM_CREDENTIAL_OWNER
from services.agentscope.runtime import agentscope_runtime


@dataclass(frozen=True)
class ConnectionHealthResult:
    """连接检测结果；错误信息不得包含 Credential 内容。"""

    connection_id: int
    success: bool
    message: str
    skipped: bool = False
    error_code: str = ""


async def _load_target(connection_id: int) -> AgentScopeConnectionPoolModel | None:
    async with async_session_factory() as db:
        return (await db.execute(select(AgentScopeConnectionPoolModel).where(
            AgentScopeConnectionPoolModel.id == connection_id,
        ))).scalar_one_or_none()


async def _credential_owner(credential_id: str) -> str | None:
    """连接池 Credential 固定归属平台 owner。"""
    del credential_id
    return PLATFORM_CREDENTIAL_OWNER


async def _persist_result(connection_id: int, success: bool, message: str) -> None:
    async with async_session_factory() as db:
        row = (await db.execute(select(AgentScopeConnectionPoolModel).where(
            AgentScopeConnectionPoolModel.id == connection_id,
        ))).scalar_one_or_none()
        if row is None:
            return
        row.last_test_status = 1 if success else 2
        row.last_test_error = "" if success else message[:512]
        row.last_test_at = china_now()
        await db.commit()


async def test_connection_record(
    connection_id: int,
    *,
    user_id: str | None = None,
    include_disabled: bool = False,
) -> ConnectionHealthResult:
    """检测并持久化单个连接；定时任务默认跳过已停用连接。"""
    row = await _load_target(connection_id)
    if row is None:
        return ConnectionHealthResult(
            connection_id, False, "供应商连接不存在", error_code="not_found",
        )
    if row.status != 1 and not include_disabled:
        return ConnectionHealthResult(
            connection_id, False, "连接已停用，跳过自动检测", skipped=True,
        )

    del user_id
    owner = await _credential_owner(row.credential_id)
    record = (
        await agentscope_runtime.storage.get_credential(owner, row.credential_id)
        if owner else None
    )
    if record is None:
        message = "底层 Credential 不存在，请删除后重新创建连接"
        await _persist_result(connection_id, False, message)
        return ConnectionHealthResult(
            connection_id, False, message, error_code="credential_not_found",
        )

    success, message = False, "连接测试失败"
    try:
        preset = resolve_preset(
            row.preset_key,
            row.provider,
            getattr(row, "protocol", None),
        )
        await validate_connection(
            preset,
            str(record.data.get("api_key") or ""),
            str(record.data.get("base_url") or ""),
        )
        success, message = True, "连接测试成功"
    except (RuntimeError, ValueError) as exc:
        message = str(exc)
    await _persist_result(connection_id, success, message)
    return ConnectionHealthResult(connection_id, success, message)
