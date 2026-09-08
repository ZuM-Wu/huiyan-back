# -*- coding: utf-8 -*-
"""创建物联硬件实时数据最新快照表。"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def probe(db) -> bool:
    """目标表存在即视为迁移完成。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='hy_hardware_realtime_snapshot'"
    ))
    return bool(result.scalar())


async def apply(db):
    """幂等创建每台设备仅一行的实时数据快照表。"""
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_hardware_realtime_snapshot ("
        " id INT NOT NULL AUTO_INCREMENT COMMENT '快照主键',"
        " device_id INT NOT NULL COMMENT '硬件设备本地主键',"
        " payload JSON NOT NULL COMMENT '最近成功的实时数据JSON',"
        " last_attempt_time DATETIME NULL COMMENT '最近尝试获取时间（中国时间）',"
        " last_success_time DATETIME NULL COMMENT '最近成功获取时间（中国时间）',"
        " last_error TEXT NOT NULL COMMENT '最近获取错误',"
        " create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间（中国时间）',"
        " update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP "
        "ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间（中国时间）',"
        " PRIMARY KEY (id),"
        " UNIQUE KEY uk_hardware_realtime_device (device_id),"
        " CONSTRAINT fk_hardware_realtime_device FOREIGN KEY (device_id) "
        "REFERENCES hy_hardware_device(id) ON DELETE CASCADE"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
        "COMMENT='物联硬件实时数据最新快照表'"
    ))
    logger.info("[迁移] 物联硬件实时数据最新快照表已创建")
