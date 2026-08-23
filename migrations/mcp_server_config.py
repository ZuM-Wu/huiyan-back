# -*- coding: utf-8 -*-
"""
迁移脚本 - hy_ai_mcp_server 表新增 config_json 和 tools_cache 列

背景：
    MCP 服务器管理改为 JSON 配置粘贴模式，需要存储完整 JSON 配置和工具列表缓存。
    存量库通过本迁移补齐列；新库由 ORM Base.metadata.create_all 直接建出。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def _column_exists(db, table: str, column: str) -> bool:
    """检查指定表的列是否已存在"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {"t": table, "c": column})
    return bool(result.scalar())


async def probe(db) -> bool:
    """config_json 列已存在则视为已应用"""
    return await _column_exists(db, "hy_ai_mcp_server", "config_json")


async def apply(db):
    """为 hy_ai_mcp_server 表添加 config_json 和 tools_cache 列（逐列幂等）"""
    if not await _column_exists(db, "hy_ai_mcp_server", "config_json"):
        await db.execute(text(
            "ALTER TABLE hy_ai_mcp_server ADD COLUMN config_json TEXT NULL "
            "COMMENT '完整 JSON 配置（Claude Desktop 风格，粘贴导入）'"
        ))
        logger.info("[迁移] hy_ai_mcp_server.config_json 列已添加")
    if not await _column_exists(db, "hy_ai_mcp_server", "tools_cache"):
        await db.execute(text(
            "ALTER TABLE hy_ai_mcp_server ADD COLUMN tools_cache TEXT NULL "
            "COMMENT '工具列表缓存 JSON（工具发现后缓存）'"
        ))
        logger.info("[迁移] hy_ai_mcp_server.tools_cache 列已添加")
