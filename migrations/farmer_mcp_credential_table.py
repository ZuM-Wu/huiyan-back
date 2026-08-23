# -*- coding: utf-8 -*-
"""迁移脚本 - 创建农户个人外部 MCP 凭据表"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def _table_exists(db) -> bool:
    """检查农户个人 MCP 凭据表是否存在"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_farmer_mcp_credential'"
    ))
    return bool(result.scalar())


async def probe(db) -> bool:
    """表已存在则视为已应用"""
    return await _table_exists(db)


async def apply(db):
    """创建农户个人外部 MCP 凭据表"""
    if await _table_exists(db):
        return
    await db.execute(text(
        "CREATE TABLE hy_farmer_mcp_credential ("
        "  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',"
        "  farmer_id INT NOT NULL COMMENT '农户ID（hy_farmer.id）',"
        "  server_id INT NOT NULL COMMENT '公共MCP服务器ID（hy_ai_farmer_mcp_server.id）',"
        "  api_key VARCHAR(256) NOT NULL DEFAULT '' COMMENT '农户个人 Bearer 鉴权密钥（明文存储）',"
        "  tools_cache TEXT NULL COMMENT '按个人凭据缓存的工具列表 JSON',"
        "  status INT NOT NULL DEFAULT 1 COMMENT '状态: 1=启用, 2=停用',"
        "  last_test_time DATETIME NULL COMMENT '最近测试时间',"
        "  last_test_status INT NOT NULL DEFAULT 0 COMMENT '最近测试状态: 0=未测试, 1=成功, 2=失败',"
        "  last_error VARCHAR(512) NOT NULL DEFAULT '' COMMENT '最近测试错误信息',"
        "  create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "  update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',"
        "  UNIQUE KEY uk_farmer_mcp_credential (farmer_id, server_id),"
        "  KEY idx_farmer_mcp_credential_farmer (farmer_id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='农户外部MCP个人凭据表'"
    ))
    logger.info("[迁移] hy_farmer_mcp_credential 表已创建")
