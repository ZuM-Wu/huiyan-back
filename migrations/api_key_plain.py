# -*- coding: utf-8 -*-
"""
迁移脚本 - hy_api_key 补齐 key_plain 密钥明文列

背景：
    个人 API 密钥列表可视化需求：密钥明文落库，列表页可随时查看与复制，
    不再仅创建时一次性展示；鉴权热路径仍走 key_hash 哈希精确查找。
    新库由 Base.metadata.create_all 直接建出（ORM 已声明），
    存量库由本迁移补齐；存量密钥无明文可回填，key_plain 为空串，
    前端回退展示前缀占位。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def probe(db) -> bool:
    """hy_api_key.key_plain 列已存在则视为已应用"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_api_key' "
        "AND COLUMN_NAME='key_plain'"
    ))
    return bool(result.scalar())


async def apply(db):
    """hy_api_key 补齐 key_plain 密钥明文列"""
    await db.execute(text(
        "ALTER TABLE hy_api_key ADD COLUMN key_plain VARCHAR(128) "
        "NOT NULL DEFAULT '' COMMENT '密钥明文（列表可视化展示用）'"
    ))
    logger.info("[迁移] hy_api_key.key_plain 列已添加")
