"""管理员个人偏好表迁移。"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def probe(db) -> bool:
    """目标表已存在时跳过建表。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_admin_preference'"
    ))
    return bool(result.scalar())


async def apply(db) -> None:
    """幂等创建管理员个人偏好表。"""
    if await probe(db):
        return
    await db.execute(text(
        "CREATE TABLE hy_admin_preference ("
        "id INT NOT NULL AUTO_INCREMENT COMMENT '偏好ID',"
        "admin_id INT NOT NULL COMMENT '管理员ID',"
        "preference_key VARCHAR(128) NOT NULL COMMENT '偏好键',"
        "preference_value TEXT NOT NULL COMMENT '偏好值JSON',"
        "create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',"
        "PRIMARY KEY (id), UNIQUE KEY uk_admin_preference_key (admin_id, preference_key),"
        "CONSTRAINT fk_admin_preference_admin FOREIGN KEY (admin_id) REFERENCES hy_admin(id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员个人偏好表'"
    ))
    logger.info("[迁移] hy_admin_preference 表已创建")
