"""
迁移脚本 - 创建产区管理相关数据表
运行方式：在 HuiYan_Back 目录下执行
    python migrations/production_area_tables.py

因 lifespan 已执行 Base.metadata.create_all，此脚本作为显式补充/重建入口。
DDL 与 DML 隔离于 engine.begin() 单独事务，规避 MySQL 隐式提交破坏事务。
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from core.db.base import engine


async def migrate():
    """创建产区/地块/种植批次表（幂等）"""
    async with engine.begin() as conn:
        # 1. 产区表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_production_area'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_production_area (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '产区ID',
                    farmer_id INT NOT NULL DEFAULT 0 COMMENT '归属农户ID',
                    name VARCHAR(128) DEFAULT '' COMMENT '产区名称',
                    code VARCHAR(64) DEFAULT '' COMMENT '产区编号',
                    province VARCHAR(64) DEFAULT '' COMMENT '省',
                    city VARCHAR(64) DEFAULT '' COMMENT '市',
                    district VARCHAR(64) DEFAULT '' COMMENT '区/县',
                    address VARCHAR(256) DEFAULT '' COMMENT '详细地址',
                    longitude DOUBLE DEFAULT 0 COMMENT '中心点经度',
                    latitude DOUBLE DEFAULT 0 COMMENT '中心点纬度',
                    boundary TEXT COMMENT '边界GeoJSON多边形',
                    area_size DOUBLE DEFAULT 0 COMMENT '面积（亩）',
                    crop_category VARCHAR(64) DEFAULT '' COMMENT '主要作物类别',
                    status INT DEFAULT 1 COMMENT '状态: 0=停用, 1=正常',
                    sort_order INT DEFAULT 0 COMMENT '排序',
                    description VARCHAR(512) DEFAULT '' COMMENT '备注说明',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
                    INDEX idx_area_farmer (farmer_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产区表'
            """))
            print("[迁移] hy_production_area 表已创建")
        else:
            print("[迁移] hy_production_area 表已存在，跳过")

        # 2. 地块表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_plot'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_plot (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '地块ID',
                    area_id INT NOT NULL DEFAULT 0 COMMENT '所属产区ID',
                    farmer_id INT NOT NULL DEFAULT 0 COMMENT '归属农户ID（冗余便于过滤）',
                    name VARCHAR(128) DEFAULT '' COMMENT '地块名称',
                    code VARCHAR(64) DEFAULT '' COMMENT '地块编号',
                    longitude DOUBLE DEFAULT 0 COMMENT '中心点经度',
                    latitude DOUBLE DEFAULT 0 COMMENT '中心点纬度',
                    boundary TEXT COMMENT '边界GeoJSON多边形',
                    area_size DOUBLE DEFAULT 0 COMMENT '面积（亩）',
                    soil_type VARCHAR(64) DEFAULT '' COMMENT '土壤类型',
                    status INT DEFAULT 1 COMMENT '状态: 0=停用, 1=正常',
                    sort_order INT DEFAULT 0 COMMENT '排序',
                    description VARCHAR(512) DEFAULT '' COMMENT '备注说明',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
                    INDEX idx_plot_area (area_id),
                    INDEX idx_plot_farmer (farmer_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='地块表'
            """))
            print("[迁移] hy_plot 表已创建")
        else:
            print("[迁移] hy_plot 表已存在，跳过")

        # 3. 种植批次表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_planting_batch'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_planting_batch (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '批次ID',
                    plot_id INT NOT NULL DEFAULT 0 COMMENT '所属地块ID',
                    area_id INT DEFAULT 0 COMMENT '所属产区ID（冗余）',
                    farmer_id INT DEFAULT 0 COMMENT '归属农户ID（冗余）',
                    batch_no VARCHAR(64) DEFAULT '' COMMENT '批次编号',
                    crop_name VARCHAR(64) DEFAULT '' COMMENT '作物名称',
                    crop_variety VARCHAR(64) DEFAULT '' COMMENT '作物品种',
                    season VARCHAR(32) DEFAULT '' COMMENT '茬口/季别',
                    plant_date DATETIME COMMENT '种植日期',
                    expected_harvest_date DATETIME COMMENT '预计采收日期',
                    actual_harvest_date DATETIME COMMENT '实际采收日期',
                    plant_count INT DEFAULT 0 COMMENT '种植株数',
                    status INT DEFAULT 1 COMMENT '状态: 0=未开始, 1=种植中, 2=已采收, 3=异常',
                    description VARCHAR(512) DEFAULT '' COMMENT '备注说明',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
                    INDEX idx_batch_plot (plot_id),
                    INDEX idx_batch_area (area_id),
                    INDEX idx_batch_farmer (farmer_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='种植批次表'
            """))
            print("[迁移] hy_planting_batch 表已创建")
        else:
            print("[迁移] hy_planting_batch 表已存在，跳过")

    print("[完成] 产区管理数据表迁移完毕")


if __name__ == "__main__":
    asyncio.run(migrate())
