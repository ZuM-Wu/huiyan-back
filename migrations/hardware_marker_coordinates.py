# -*- coding: utf-8 -*-
"""为硬件地图标记增加地块内经纬度坐标。"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

_COMMENTS = {
    "marker_longitude": "硬件标记在绑定地块内的经度",
    "marker_latitude": "硬件标记在绑定地块内的纬度",
}
_CONSTRAINT = "ck_hardware_device_marker_coordinates"


async def _read_columns(db) -> dict[str, str]:
    """读取两个坐标列及中文备注，兼容迁移执行到一半的数据库。"""
    result = await db.execute(text(
        "SELECT COLUMN_NAME, COLUMN_COMMENT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_hardware_device' "
        "AND COLUMN_NAME IN ('marker_longitude','marker_latitude')"
    ))
    return {row[0]: row[1] for row in result.all()}


async def _has_constraint(db) -> bool:
    """检查经纬度成对为空或同时合法的数据库约束。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_hardware_device' "
        "AND CONSTRAINT_NAME=:name"
    ), {"name": _CONSTRAINT})
    return bool(result.scalar())


async def probe(db) -> bool:
    """两个坐标列、中文备注和约束均存在时视为迁移完成。"""
    columns = await _read_columns(db)
    return columns == _COMMENTS and await _has_constraint(db)


async def apply(db):
    """幂等补齐地块内坐标列，并保留旧边界比例供兼容转换。"""
    columns = await _read_columns(db)
    if columns.get("marker_longitude") != _COMMENTS["marker_longitude"]:
        operation = "ADD COLUMN" if "marker_longitude" not in columns else "MODIFY COLUMN"
        await db.execute(text(
            f"ALTER TABLE hy_hardware_device {operation} "
            "marker_longitude DECIMAL(11,8) NULL "
            f"COMMENT '{_COMMENTS['marker_longitude']}' AFTER marker_ratio"
        ))
    if columns.get("marker_latitude") != _COMMENTS["marker_latitude"]:
        operation = "ADD COLUMN" if "marker_latitude" not in columns else "MODIFY COLUMN"
        await db.execute(text(
            f"ALTER TABLE hy_hardware_device {operation} "
            "marker_latitude DECIMAL(10,8) NULL "
            f"COMMENT '{_COMMENTS['marker_latitude']}' AFTER marker_longitude"
        ))
    if not await _has_constraint(db):
        await db.execute(text(
            "ALTER TABLE hy_hardware_device ADD CONSTRAINT "
            "ck_hardware_device_marker_coordinates CHECK ("
            "(marker_longitude IS NULL AND marker_latitude IS NULL) OR "
            "(marker_longitude BETWEEN -180 AND 180 AND "
            "marker_latitude BETWEEN -90 AND 90))"
        ))
    logger.info("[迁移] 物联硬件地块内标记坐标字段已创建")
