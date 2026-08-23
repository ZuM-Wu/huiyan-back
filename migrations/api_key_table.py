# -*- coding: utf-8 -*-
"""
迁移脚本 - 创建 hy_api_key 个人API密钥表

背景：
    MCP 服务的 Bearer Key 鉴权数据表。管理员/农户在个人中心创建个人 Key，
    数据库仅存 sha256 哈希，明文仅创建时一次性返回。
    新库由 Base.metadata.create_all 直接建出（ORM 已声明），
    存量库由本迁移补齐；通过 migrations/registry.py 注册表统一调度，
    探测函数发现表已存在时仅标记已应用（探测回填）。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def probe(db) -> bool:
    """hy_api_key 表已存在则视为已应用"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_api_key'"
    ))
    return bool(result.scalar())


async def apply(db):
    """创建 hy_api_key 个人API密钥表"""
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_api_key ("
        "  id INT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
        "  user_type VARCHAR(16) NOT NULL COMMENT '用户体系: admin=管理员, farmer=农户',"
        "  user_id INT NOT NULL COMMENT '所属用户ID（hy_admin.id 或 hy_farmer.id）',"
        "  name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '密钥备注名',"
        "  key_hash VARCHAR(64) NOT NULL COMMENT '密钥 sha256 哈希（不存明文）',"
        "  prefix VARCHAR(16) NOT NULL DEFAULT '' COMMENT '明文前8位（仅列表展示用）',"
        "  status INT NOT NULL DEFAULT 1 COMMENT '状态: 1=启用, 2=已吊销',"
        "  last_used_time DATETIME NULL COMMENT '最后使用时间（鉴权命中时节流回写）',"
        "  create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "  PRIMARY KEY (id),"
        "  UNIQUE KEY uk_api_key_hash (key_hash),"
        "  KEY idx_api_key_user (user_type, user_id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个人API密钥表'"
    ))
    logger.info("[迁移] hy_api_key 个人API密钥表已创建")
