# -*- coding: utf-8 -*-
"""
迁移脚本 - 创建农户端外部 MCP 服务器表

背景：
    管理员与农户的 MCP 服务器配置物理隔离，农户端使用独立表
    hy_ai_farmer_mcp_server，与管理员表 hy_ai_mcp_server 结构相同、
    ID 各自独立自增，彻底杜绝跨身份访问。
    新库由 ORM Base.metadata.create_all 直接建出，存量库通过本迁移补建。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def _table_exists(db, table: str) -> bool:
    """检查指定表是否已存在"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t"
    ), {"t": table})
    return bool(result.scalar())


async def probe(db) -> bool:
    """hy_ai_farmer_mcp_server 表已存在则视为已应用"""
    return await _table_exists(db, "hy_ai_farmer_mcp_server")


async def apply(db):
    """创建农户端外部 MCP 服务器表"""
    if await _table_exists(db, "hy_ai_farmer_mcp_server"):
        return
    await db.execute(text(
        "CREATE TABLE hy_ai_farmer_mcp_server ("
        "  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',"
        "  name VARCHAR(64) NOT NULL COMMENT '服务器名称',"
        "  url VARCHAR(256) NOT NULL COMMENT 'Streamable HTTP 接入地址',"
        "  api_key VARCHAR(256) NOT NULL DEFAULT '' COMMENT 'Bearer 鉴权密钥（可为空）',"
        "  config_json TEXT NULL COMMENT '完整 JSON 配置（Claude Desktop 风格，粘贴导入）',"
        "  tools_cache TEXT NULL COMMENT '工具列表缓存 JSON（工具发现后缓存）',"
        "  status INT NOT NULL DEFAULT 1 COMMENT '状态: 1=启用, 2=停用',"
        "  create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "  update_time DATETIME DEFAULT CURRENT_TIMESTAMP "
        "    ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='农户端外部MCP服务器表'"
    ))
    logger.info("[迁移] hy_ai_farmer_mcp_server 表已创建")
