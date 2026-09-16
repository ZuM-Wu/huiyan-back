# -*- coding: utf-8 -*-
"""
迁移脚本 - 创建天气模块相关数据表
运行方式：在 app 目录下执行
    python migrations/weather_tables.py

因 lifespan 已执行 Base.metadata.create_all，此脚本作为显式补充/重建入口。
DDL 隔离于 engine.begin() 单独事务，规避 MySQL 隐式提交破坏事务。
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from core.db.base import engine


async def migrate():
    """创建天气快照/逐日历史/数据源绑定/灾害预警表（幂等）"""
    async with engine.begin() as conn:
        # 1. 产区天气快照表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_weather_data'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_weather_data (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '快照ID',
                    area_id INT NOT NULL DEFAULT 0 COMMENT '产区ID（唯一，每产区一行）',
                    source VARCHAR(64) DEFAULT '' COMMENT '数据来源插件名（weather_qweather/weather_amap）',
                    adcode VARCHAR(16) DEFAULT '' COMMENT '高德行政区划码缓存（避免重复逆地理编码）',
                    realtime TEXT COMMENT '实况天气JSON',
                    hourly TEXT COMMENT '24小时逐时预报JSON（高德源为空）',
                    forecast TEXT COMMENT '逐日预报JSON（3~7天）',
                    fetch_time DATETIME COMMENT '最近一次成功拉取时间',
                    error_msg VARCHAR(256) DEFAULT '' COMMENT '最近一次失败原因（成功时置空，失败保留旧快照）',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
                    UNIQUE KEY uk_weather_area (area_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产区天气快照表'
            """))
            print("[迁移] hy_weather_data 表已创建")
        else:
            print("[迁移] hy_weather_data 表已存在，跳过")

        # 2. 逐日天气历史表（积温数据基础）
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_weather_daily'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_weather_daily (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '记录ID',
                    area_id INT NOT NULL DEFAULT 0 COMMENT '产区ID',
                    date DATE NOT NULL COMMENT '日期（与产区联合唯一）',
                    temp_max DOUBLE COMMENT '当日最高温（℃，实况滚动取极值，自上线起积累）',
                    temp_min DOUBLE COMMENT '当日最低温（℃，同上滚动更新）',
                    temp_avg DOUBLE COMMENT '日均温（℃，=(max+min)/2，日终定格）',
                    humidity DOUBLE COMMENT '相对湿度（%）',
                    precip DOUBLE COMMENT '降水量（mm）',
                    wind_scale VARCHAR(16) DEFAULT '' COMMENT '风力等级',
                    text_day VARCHAR(64) DEFAULT '' COMMENT '天气现象文字',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
                    UNIQUE KEY uk_weather_daily_area_date (area_id, date),
                    INDEX idx_weather_daily_area (area_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='逐日天气历史表'
            """))
            print("[迁移] hy_weather_daily 表已创建")
        else:
            print("[迁移] hy_weather_daily 表已存在，跳过")

        # 3. 产区数据源绑定表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_weather_area_binding'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_weather_area_binding (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '绑定ID',
                    area_id INT NOT NULL DEFAULT 0 COMMENT '产区ID（唯一）',
                    source VARCHAR(64) DEFAULT '' COMMENT '指定数据源插件名（weather_qweather/weather_amap）',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
                    UNIQUE KEY uk_weather_binding_area (area_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产区天气数据源绑定表'
            """))
            print("[迁移] hy_weather_area_binding 表已创建")
        else:
            print("[迁移] hy_weather_area_binding 表已存在，跳过")

        # 4. 气象灾害预警表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_weather_alert'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_weather_alert (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '预警记录ID',
                    area_id INT NOT NULL DEFAULT 0 COMMENT '产区ID',
                    alert_id VARCHAR(128) NOT NULL COMMENT '第三方预警唯一ID（去重键）',
                    source VARCHAR(64) DEFAULT '' COMMENT '来源插件名',
                    alert_type VARCHAR(64) DEFAULT '' COMMENT '预警类型（台风/暴雨/霜冻等）',
                    level VARCHAR(32) DEFAULT '' COMMENT '预警等级（蓝色/黄色/橙色/红色）',
                    title VARCHAR(256) DEFAULT '' COMMENT '预警标题',
                    text TEXT COMMENT '预警详情文本',
                    start_time DATETIME COMMENT '预警生效时间',
                    end_time DATETIME COMMENT '预警结束时间',
                    notified INT DEFAULT 0 COMMENT '通知状态: 0=未通知, 1=已通知（二期联动通知模块）',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    UNIQUE KEY uk_weather_alert_id (alert_id),
                    INDEX idx_weather_alert_area (area_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='气象灾害预警表'
            """))
            print("[迁移] hy_weather_alert 表已创建")
        else:
            print("[迁移] hy_weather_alert 表已存在，跳过")

    print("[完成] 天气模块数据表迁移完毕")


if __name__ == "__main__":
    asyncio.run(migrate())
