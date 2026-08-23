# -*- coding: utf-8 -*-
"""为管理员 MCP 服务器补充测试状态字段。"""
from sqlalchemy import text


async def _column_exists(db, column: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_ai_mcp_server' AND COLUMN_NAME=:c"
    ), {"c": column})
    return bool(result.scalar())


async def probe(db) -> bool:
    for name in ("last_test_time", "last_test_status", "last_error"):
        if not await _column_exists(db, name):
            return False
    return True


async def apply(db):
    fields = (
        ("last_test_time", "ALTER TABLE hy_ai_mcp_server ADD COLUMN last_test_time DATETIME NULL COMMENT '最近测试时间'"),
        ("last_test_status", "ALTER TABLE hy_ai_mcp_server ADD COLUMN last_test_status INT NOT NULL DEFAULT 0 COMMENT '最近测试状态: 0=未测试, 1=成功, 2=失败'"),
        ("last_error", "ALTER TABLE hy_ai_mcp_server ADD COLUMN last_error VARCHAR(512) NOT NULL DEFAULT '' COMMENT '最近测试错误信息'"),
    )
    for name, ddl in fields:
        if not await _column_exists(db, name):
            await db.execute(text(ddl))
