# -*- coding: utf-8 -*-
"""创建物联硬件设备本地镜像表。"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def probe(db) -> bool:
    """目标表存在即视为迁移完成。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_hardware_device'"
    ))
    return bool(result.scalar())


async def apply(db):
    """幂等创建物联硬件设备本地镜像表。"""
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_hardware_device ("
        " id INT NOT NULL AUTO_INCREMENT COMMENT '设备本地主键',"
        " external_id INT NOT NULL DEFAULT 0 COMMENT '物联平台设备ID',"
        " device_name VARCHAR(128) NOT NULL COMMENT '物联平台设备编号',"
        " device_type VARCHAR(32) NOT NULL DEFAULT '' COMMENT '设备类型编码',"
        " device_type_label VARCHAR(64) NOT NULL DEFAULT '' COMMENT '设备类型名称',"
        " nickname VARCHAR(128) NOT NULL DEFAULT '' COMMENT '物联平台设备名称',"
        " bot_id INT NOT NULL DEFAULT 0 COMMENT '物联平台机器人ID',"
        " external_latitude VARCHAR(32) NOT NULL DEFAULT '' COMMENT '物联平台纬度快照',"
        " external_longitude VARCHAR(32) NOT NULL DEFAULT '' COMMENT '物联平台经度快照',"
        " external_address VARCHAR(256) NOT NULL DEFAULT '' COMMENT '物联平台地址快照',"
        " image_url VARCHAR(512) NOT NULL DEFAULT '' COMMENT '设备自定义图片URL',"
        " visible_metric_identifiers JSON NULL COMMENT '实时卡片显示标识列表，NULL表示使用有值指标，空数组表示全部隐藏',"
        " area_id INT NULL COMMENT '绑定产区ID',"
        " plot_id INT NULL COMMENT '绑定地块ID',"
        " marker_ratio DECIMAL(10,8) NULL COMMENT '地块外边界周长位置比例0到1',"
        " marker_longitude DECIMAL(11,8) NULL COMMENT '硬件标记在绑定地块内的经度',"
        " marker_latitude DECIMAL(10,8) NULL COMMENT '硬件标记在绑定地块内的纬度',"
        " available INT NOT NULL DEFAULT 1 COMMENT '平台可用状态: 0=不可用, 1=可用',"
        " last_sync_error TEXT NOT NULL COMMENT '最近同步错误',"
        " last_sync_time DATETIME NULL COMMENT '最近同步时间（中国时间）',"
        " create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间（中国时间）',"
        " update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间（中国时间）',"
        " PRIMARY KEY (id),"
        " UNIQUE KEY uk_hardware_device_name (device_name),"
        " KEY idx_hardware_device_type (device_type),"
        " KEY idx_hardware_device_available (available),"
        " KEY idx_hardware_device_area (area_id),"
        " KEY idx_hardware_device_plot (plot_id),"
        " CONSTRAINT fk_hardware_device_area FOREIGN KEY (area_id) REFERENCES hy_production_area(id) ON DELETE SET NULL,"
        " CONSTRAINT fk_hardware_device_plot FOREIGN KEY (plot_id) REFERENCES hy_plot(id) ON DELETE SET NULL,"
        " CONSTRAINT ck_hardware_device_marker_ratio CHECK (marker_ratio IS NULL OR (marker_ratio >= 0 AND marker_ratio <= 1)),"
        " CONSTRAINT ck_hardware_device_marker_coordinates CHECK ((marker_longitude IS NULL AND marker_latitude IS NULL) OR (marker_longitude BETWEEN -180 AND 180 AND marker_latitude BETWEEN -90 AND 90))"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='物联硬件设备本地镜像表'"
    ))
    logger.info("[迁移] 物联硬件设备本地镜像表已创建")
