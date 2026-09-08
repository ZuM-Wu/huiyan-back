# -*- coding: utf-8 -*-
"""将误设为 Responses 的 GLM 连接恢复为 Chat Completions。"""
from __future__ import annotations

import json

from sqlalchemy import text

from services.agentscope.providers import GLMChatModel


_MARKER = "ai-resource-glm-chat-protocol-v1"
_CHAT_SCHEMA = json.dumps(
    GLMChatModel.Parameters.model_json_schema(),
    ensure_ascii=False,
)


async def _table_exists(db, table: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"
    ), {"table": table})
    return bool(result.scalar())


async def probe(db) -> bool:
    if not await _table_exists(db, "hy_ai_resource_refactor"):
        return False
    result = await db.execute(text(
        "SELECT COUNT(*) FROM hy_ai_resource_refactor WHERE marker=:marker"
    ), {"marker": _MARKER})
    return bool(result.scalar())


async def apply(db) -> None:
    """幂等纠正 GLM 协议与模型参数，不触碰 Credential 和会话记录。"""
    if await probe(db):
        return
    if await _table_exists(db, "hy_agentscope_connection_pool"):
        await db.execute(text(
            "UPDATE hy_agentscope_connection_pool SET "
            "protocol='openai_chat_completions', last_test_status=0, "
            "last_test_error='', last_test_at=NULL "
            "WHERE preset_key='glm'"
        ))
        if await _table_exists(db, "hy_agentscope_model_card"):
            await db.execute(text(
                "UPDATE hy_agentscope_model_card m "
                "INNER JOIN hy_agentscope_connection_pool p ON p.id=m.connection_id "
                "SET m.provider=p.provider, m.parameter_schema=:schema "
                "WHERE p.preset_key='glm'"
            ), {"schema": _CHAT_SCHEMA})
        if await _table_exists(db, "hy_agentscope_connection_model_discovery"):
            await db.execute(text(
                "UPDATE hy_agentscope_connection_model_discovery d "
                "INNER JOIN hy_agentscope_connection_pool p ON p.id=d.connection_id "
                "SET d.parameter_schema=:schema "
                "WHERE p.preset_key='glm'"
            ), {"schema": _CHAT_SCHEMA})
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_ai_resource_refactor ("
        "marker VARCHAR(64) NOT NULL PRIMARY KEY COMMENT '迁移标记',"
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间'"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 资源连接来源迁移记录'"
    ))
    await db.execute(text(
        "INSERT IGNORE INTO hy_ai_resource_refactor(marker) VALUES (:marker)"
    ), {"marker": _MARKER})
