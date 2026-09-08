# -*- coding: utf-8 -*-
"""为物联硬件设备增加实时卡片显示配置。"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMN_COMMENT = "实时卡片显示标识列表，NULL表示使用有值指标，空数组表示全部隐藏"


async def _read_column_comment(db) -> str | None:
    """读取现有列备注；列不存在时返回空值。"""
    result = await db.execute(text(
        "SELECT COLUMN_COMMENT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_hardware_device' "
        "AND COLUMN_NAME='visible_metric_identifiers'"
    ))
    return result.scalar_one_or_none()


async def probe(db) -> bool:
    """目标列存在且备注语义完整时视为迁移完成。"""
    return await _read_column_comment(db) == _COLUMN_COMMENT


async def apply(db):
    """幂等增加设备级实时卡片显示标识列表并修正旧备注。"""
    current_comment = await _read_column_comment(db)
    if current_comment == _COLUMN_COMMENT:
        return
    operation = "ADD COLUMN" if current_comment is None else "MODIFY COLUMN"
    position = " AFTER image_url" if current_comment is None else ""
    await db.execute(text(
        f"ALTER TABLE hy_hardware_device {operation} "
        "visible_metric_identifiers JSON NULL "
        f"COMMENT '{_COLUMN_COMMENT}'{position}"
    ))
    logger.info("[迁移] 物联硬件实时卡片显示配置字段已创建")
