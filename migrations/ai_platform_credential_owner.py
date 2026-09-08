# -*- coding: utf-8 -*-
"""把连接池引用的 Credential 归一到固定平台 owner。"""

from sqlalchemy import text

from services.agentscope.platform_credentials import PLATFORM_CREDENTIAL_OWNER


async def probe(db) -> bool:
    """所有连接池 Credential 已归一时视为完成。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM credentials c "
        "INNER JOIN hy_agentscope_connection_pool p ON p.credential_id=c.id "
        "WHERE c.user_id<>:owner"
    ), {"owner": PLATFORM_CREDENTIAL_OWNER})
    return not bool(result.scalar())


async def apply(db) -> None:
    """只迁移连接池已引用凭据，保留未关联的个人 Credential。"""
    await db.execute(text(
        "UPDATE credentials c "
        "INNER JOIN hy_agentscope_connection_pool p ON p.credential_id=c.id "
        "SET c.user_id=:owner WHERE c.user_id<>:owner"
    ), {"owner": PLATFORM_CREDENTIAL_OWNER})
