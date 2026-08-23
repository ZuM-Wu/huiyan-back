"""
迁移脚本 - 创建实名认证相关数据表
运行方式：在 HuiYan_Back 目录下执行
    python migrations/certification_tables.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from core.db.base import engine


async def migrate():
    """创建实名认证记录表和认证渠道表（幂等）"""
    async with engine.begin() as conn:
        # 1. 检查并创建实名认证记录表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_certification_record'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_certification_record (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '记录ID',
                    farmer_id INT NOT NULL COMMENT '农户ID',
                    real_name VARCHAR(64) DEFAULT '' COMMENT '实名名称',
                    id_card VARCHAR(18) DEFAULT '' COMMENT '身份证号',
                    cert_type VARCHAR(20) DEFAULT 'personal' COMMENT '认证类型: personal=个人',
                    cert_no VARCHAR(128) DEFAULT '' COMMENT '认证ID（第三方返回或系统生成）',
                    certify_url VARCHAR(512) DEFAULT '' COMMENT '第三方认证链接（待认证期间供用户重新获取）',
                    status INT DEFAULT 0 COMMENT '状态: 0=待审核, 1=已认证, 2=未通过',
                    phone VARCHAR(32) DEFAULT '' COMMENT '提交的手机号',
                    front_image VARCHAR(256) DEFAULT '' COMMENT '身份证正面照URL',
                    back_image VARCHAR(256) DEFAULT '' COMMENT '身份证背面照URL',
                    channel VARCHAR(64) DEFAULT 'manual' COMMENT '认证渠道: manual=人工, 插件名=第三方',
                    submit_time DATETIME COMMENT '提交时间',
                    review_time DATETIME COMMENT '审核时间',
                    reviewer_id INT DEFAULT 0 COMMENT '审核管理员ID',
                    reviewer_name VARCHAR(64) DEFAULT '' COMMENT '审核管理员名称',
                    review_remark VARCHAR(512) DEFAULT '' COMMENT '审核备注',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实名认证记录表'
            """))
            print("[迁移] hy_certification_record 表已创建")
        else:
            print("[迁移] hy_certification_record 表已存在，跳过")

        # 2. 检查并创建认证渠道表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_certification_channel'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_certification_channel (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '渠道ID',
                    plugin_name VARCHAR(64) DEFAULT '' COMMENT '插件标识',
                    channel_name VARCHAR(64) DEFAULT '' COMMENT '渠道名称',
                    channel_type VARCHAR(20) DEFAULT 'personal' COMMENT '类型: personal=个人, company=企业',
                    status INT DEFAULT 0 COMMENT '状态: 0=禁用, 1=启用',
                    config TEXT COMMENT '配置参数JSON（AppID/AppSecret等）',
                    sort_order INT DEFAULT 0 COMMENT '排序',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='认证渠道表'
            """))
            print("[迁移] hy_certification_channel 表已创建")
        else:
            print("[迁移] hy_certification_channel 表已存在，跳过")

    print("[完成] 实名认证数据表迁移完毕")


if __name__ == "__main__":
    asyncio.run(migrate())
