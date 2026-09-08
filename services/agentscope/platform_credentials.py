# -*- coding: utf-8 -*-
"""平台共享 AI Credential 的固定 owner 与 AgentScope 访问策略。"""

from __future__ import annotations

import logging

from agentscope.app.access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)
from sqlalchemy import select

from core.db.ai_resources import AgentScopeConnectionPoolModel
from core.db.base import async_session_factory


PLATFORM_CREDENTIAL_OWNER = "system:ai-resources"
logger = logging.getLogger(__name__)


class PlatformCredentialAccessPolicy(ResourceAccessPolicyBase):
    """只向管理员会话共享启用中的平台 Credential，且只授予读取使用权。"""

    async def list_accessible(self, viewer_id, kind, storage) -> list[ResourceRef]:
        del storage
        if kind != ResourceKind.CREDENTIAL or not viewer_id.startswith("admin:"):
            return []
        try:
            async with async_session_factory() as db:
                credential_ids = (await db.execute(select(
                    AgentScopeConnectionPoolModel.credential_id,
                ).where(AgentScopeConnectionPoolModel.status == 1))).scalars().all()
        except Exception:
            logger.exception("读取平台共享 Credential 清单失败，已按拒绝访问处理")
            return []
        return [
            ResourceRef(
                kind=ResourceKind.CREDENTIAL,
                owner_id=PLATFORM_CREDENTIAL_OWNER,
                resource_id=credential_id,
                permission=ResourcePermission.READ,
            )
            for credential_id in credential_ids
        ]


platform_credential_access_policy = PlatformCredentialAccessPolicy()
