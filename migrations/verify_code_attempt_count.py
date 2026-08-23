# -*- coding: utf-8 -*-
"""
迁移脚本 - hy_verify_code 表新增 attempt_count 失败尝试计数列

背景：
    验证码防爆破需要持久化累计失败次数（累计 5 次错误自动作废）。
    新库由 Base.metadata.create_all 直接建出该列（ORM 已声明），
    存量库由本迁移补齐；通过 migrations/registry.py 注册表统一调度，
    探测函数发现列已存在时仅标记已应用（探测回填）。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def probe(db) -> bool:
    """hy_verify_code.attempt_count 列已存在则视为已应用"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_verify_code' "
        "AND COLUMN_NAME='attempt_count'"
    ))
    return bool(result.scalar())


async def apply(db):
    """hy_verify_code 补齐 attempt_count 失败尝试计数列"""
    await db.execute(text(
        "ALTER TABLE hy_verify_code ADD COLUMN attempt_count INT DEFAULT 0 "
        "COMMENT '失败尝试计数（累计5次错误自动作废，防爆破）' AFTER used"
    ))
    logger.info("[迁移] hy_verify_code.attempt_count 列已添加")
