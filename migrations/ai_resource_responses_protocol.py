# -*- coding: utf-8 -*-
"""将命名 OpenAI 连接迁移到 OpenAI Responses 协议。"""
from __future__ import annotations

import json

from agentscope.model import OpenAIResponseModel
from sqlalchemy import text


_MARKER = "ai-resource-responses-protocol-v1"
_RESPONSE_SCHEMA = json.dumps(
    OpenAIResponseModel.Parameters.model_json_schema(),
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
    """幂等迁移协议、Credential 类型及连接关联的模型元数据。"""
    if await probe(db):
        return
    if await _table_exists(db, "hy_agentscope_connection_pool"):
        await db.execute(text(
            "UPDATE hy_agentscope_connection_pool SET "
            "protocol='openai_responses', last_test_status=0, "
            "last_test_error='', last_test_at=NULL "
            "WHERE preset_key='openai'"
        ))
        await db.execute(text(
            "UPDATE hy_agentscope_connection_pool SET "
            "provider='openai_responses_credential' "
            "WHERE preset_key='openai'"
        ))
        if await _table_exists(db, "credentials"):
            # 仅替换命名 OpenAI 连接的判别类型，payload 内密钥和地址原样保留。
            await db.execute(text(
                "UPDATE credentials c "
                "INNER JOIN hy_agentscope_connection_pool p ON p.credential_id=c.id "
                "SET c.payload=JSON_SET(c.payload, '$.data.type', "
                "'openai_responses_credential') "
                "WHERE p.preset_key='openai'"
            ))
        if await _table_exists(db, "hy_agentscope_model_card"):
            await db.execute(text(
                "UPDATE hy_agentscope_model_card m "
                "INNER JOIN hy_agentscope_connection_pool p ON p.id=m.connection_id "
                "SET m.provider=p.provider, m.parameter_schema=:schema "
                "WHERE p.preset_key='openai'"
            ), {"schema": _RESPONSE_SCHEMA})
        if await _table_exists(db, "hy_agentscope_connection_model_discovery"):
            await db.execute(text(
                "UPDATE hy_agentscope_connection_model_discovery d "
                "INNER JOIN hy_agentscope_connection_pool p ON p.id=d.connection_id "
                "SET d.parameter_schema=:schema "
                "WHERE p.preset_key='openai'"
            ), {"schema": _RESPONSE_SCHEMA})
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_ai_resource_refactor ("
        "marker VARCHAR(64) NOT NULL PRIMARY KEY COMMENT '迁移标记',"
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间'"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 资源连接来源迁移记录'"
    ))
    await db.execute(text(
        "INSERT IGNORE INTO hy_ai_resource_refactor(marker) VALUES (:marker)"
    ), {"marker": _MARKER})
