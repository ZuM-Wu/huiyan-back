# -*- coding: utf-8 -*-
"""
邮件模板去插件绑定迁移脚本 - 删除 hy_email_template.interface 字段

背景：邮件无平台审核要求，模板不应绑定具体插件接口；
发送时由核心自动路由到已启用的邮件插件（短信因需平台审核仍保留绑定）。
幂等：字段不存在时跳过。
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def migrate(db):
    """删除 hy_email_template.interface 字段（幂等）"""
    # 1. 检查字段是否存在
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_email_template' "
        "AND COLUMN_NAME='interface'"
    ))
    exists = result.scalar()

    if exists:
        # 2. 删除字段（邮件模板不再绑定插件接口）
        await db.execute(text(
            "ALTER TABLE hy_email_template DROP COLUMN interface"
        ))
        await db.commit()
        logger.info("[DB 迁移] hy_email_template.interface 字段已删除（邮件模板不绑定插件）")
    else:
        logger.debug("[DB 迁移] hy_email_template.interface 字段不存在，跳过")


async def rollback(db):
    """回滚脚本：恢复 interface 字段（历史绑定数据无法恢复）"""
    await db.execute(text(
        "ALTER TABLE hy_email_template ADD COLUMN interface VARCHAR(64) "
        "NOT NULL DEFAULT '' COMMENT '接口标识：smtp/sendcloud（已废弃）' AFTER id"
    ))
    await db.commit()
    logger.info("[DB 回滚] hy_email_template.interface 字段已恢复")
