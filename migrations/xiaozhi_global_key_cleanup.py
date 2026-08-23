# -*- coding: utf-8 -*-
"""删除小智插件已废弃的全局系统 MCP API Key。"""
from sqlalchemy import text

CONFIG_KEY = "xiaozhi_mcp.huiyan_api_key"


async def probe(db) -> bool:
    """精确配置键不存在时视为迁移目标已满足。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM hy_configuration WHERE `key`=:key"
    ), {"key": CONFIG_KEY})
    return result.scalar() == 0


async def apply(db):
    """只清理废弃全局 Key，不影响小智接入点和自定义 MCP 凭据。"""
    await db.execute(text(
        "DELETE FROM hy_configuration WHERE `key`=:key"
    ), {"key": CONFIG_KEY})
