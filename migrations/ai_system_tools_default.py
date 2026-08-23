# -*- coding: utf-8 -*-
"""迁移 - 关闭未明确登记的系统 MCP 工具。"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def _get_row(db):
    """读取系统 MCP 开关配置。"""
    result = await db.execute(text(
        "SELECT value, create_time, update_time FROM hy_configuration "
        "WHERE `key`='ai.system_tools_enabled' LIMIT 1"
    ))
    return result.first()


async def probe(db) -> bool:
    """开关不存在、已关闭或已被管理员修改时无需迁移。"""
    row = await _get_row(db)
    if not row or str(row[0]) != "1":
        return True
    # 仅回收旧种子写入的默认值，保留管理员明确修改过的配置。
    return row[1] != row[2]


async def apply(db):
    """将未修改的旧默认开启值调整为关闭。"""
    await db.execute(text(
        "UPDATE hy_configuration SET value='0', "
        "description='系统 MCP 工具总开关（0=关闭, 1=开启）' "
        "WHERE `key`='ai.system_tools_enabled' AND value='1' "
        "AND create_time=update_time"
    ))
    logger.info("[迁移] 未登记的系统 MCP 默认开关已关闭")
