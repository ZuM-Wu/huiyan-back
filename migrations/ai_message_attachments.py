# -*- coding: utf-8 -*-
"""为 AI 消息补充可持久化图片附件字段。"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def probe(db) -> bool:
    """字段存在时视为迁移已应用。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_ai_message' "
        "AND COLUMN_NAME='attachments'"
    ))
    return bool(result.scalar())


async def apply(db):
    """幂等添加附件 JSON 文本字段。"""
    if await probe(db):
        return
    await db.execute(text(
        "ALTER TABLE hy_ai_message ADD COLUMN attachments TEXT NULL "
        "COMMENT '消息图片附件 JSON 数组（仅存永久URL与元数据）' AFTER content"
    ))
    logger.info("[迁移] hy_ai_message.attachments 字段已添加")
