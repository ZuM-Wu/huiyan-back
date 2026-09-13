# -*- coding: utf-8 -*-
"""
迁移脚本 - 创建 hy_file_log 文件存储日志表

背景：
    对标 ZJMF file_log 表，记录每次文件上传的存储归属信息，
    用于对象存储切换时的数据追踪与迁移评估。
    新库由 Base.metadata.create_all 直接建出（ORM 已声明），
    存量库由本迁移补齐；通过 migrations/registry.py 注册表统一调度，
    探测函数发现表已存在时仅标记已应用（探测回填）。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def probe(db) -> bool:
    """hy_file_log 表已存在则视为已应用"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_file_log'"
    ))
    return bool(result.scalar())


async def apply(db):
    """创建 hy_file_log 文件存储日志表"""
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_file_log ("
        "  id INT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
        "  uuid VARCHAR(64) NOT NULL COMMENT '文件唯一标识（用于签名URL的fid）',"
        "  save_name VARCHAR(128) NOT NULL COMMENT '存储文件名（UUID重命名后）',"
        "  local_path VARCHAR(512) NULL DEFAULT NULL COMMENT '本地 upload/ 下的相对路径',"
        "  local_path_hash VARCHAR(64) NULL DEFAULT NULL COMMENT '本地相对路径SHA-256摘要',"
        "  object_key VARCHAR(512) NOT NULL DEFAULT '' COMMENT '对象存储中的真实对象键',"
        "  original_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '原始文件名',"
        "  ext VARCHAR(16) NOT NULL DEFAULT '' COMMENT '扩展名（含点号）',"
        "  oss_method VARCHAR(64) NOT NULL DEFAULT 'local_oss' COMMENT '存储插件标识',"
        "  url VARCHAR(512) NOT NULL DEFAULT '' COMMENT '访问URL',"
        "  file_size INT NOT NULL DEFAULT 0 COMMENT '文件字节数',"
        "  admin_id INT NULL COMMENT '上传管理员ID',"
        "  source VARCHAR(32) NOT NULL DEFAULT 'admin' COMMENT '来源：admin/farmer/system',"
        "  create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "  PRIMARY KEY (id),"
        "  KEY idx_file_log_save_name (save_name),"
        "  UNIQUE KEY uk_file_log_local_path (local_path_hash),"
        "  KEY idx_file_log_uuid (uuid),"
        "  KEY idx_file_log_oss_method (oss_method)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件存储日志表'"
    ))
    logger.info("[迁移] hy_file_log 文件存储日志表已创建")
