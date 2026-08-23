"""
管理员挂件配置表迁移脚本 — 创建 hy_admin_widget 表
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def migrate(db):
    """
    幂等建表：检查 hy_admin_widget 表是否存在，不存在则创建
    """
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_admin_widget'"
    ))
    exists = result.scalar()

    if not exists:
        await db.execute(text(
            """
            CREATE TABLE `hy_admin_widget` (
                `id` INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
                `admin_id` INT NOT NULL COMMENT '管理员ID',
                `widgets` TEXT DEFAULT '[]' COMMENT '已启用挂件标识的有序JSON数组',
                `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                `update_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                PRIMARY KEY (`id`),
                UNIQUE KEY `uk_admin_id` (`admin_id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员挂件配置表'
            """
        ))
        await db.commit()
        logger.info("[DB 迁移] hy_admin_widget 表已创建")
    else:
        logger.debug("[DB 迁移] hy_admin_widget 表已存在，跳过")


async def rollback(db):
    """回滚脚本"""
    await db.execute(text("DROP TABLE IF EXISTS hy_admin_widget"))
    logger.info("[DB 回滚] hy_admin_widget 表已删除")
