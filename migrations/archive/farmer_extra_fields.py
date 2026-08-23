"""
迁移脚本 - 为 hy_farmer 表新增 address/remark/country/language 字段 + 插入登录记录种子数据
运行方式：在 HuiYan_Back 目录下执行
    python migrations/farmer_extra_fields.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from core.db.base import engine, async_session_factory


async def migrate():
    """新增字段 + 种子数据"""
    async with engine.begin() as conn:
        # 1. 检查并添加 address 字段
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_farmer' AND COLUMN_NAME='address'"
        ))
        if not result.scalar():
            await conn.execute(text(
                "ALTER TABLE hy_farmer ADD COLUMN address VARCHAR(256) DEFAULT '' COMMENT '地址'"
            ))
            print("[迁移] hy_farmer.address 已添加")

        # 2. 检查并添加 remark 字段
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_farmer' AND COLUMN_NAME='remark'"
        ))
        if not result.scalar():
            await conn.execute(text(
                "ALTER TABLE hy_farmer ADD COLUMN remark VARCHAR(2048) DEFAULT '' COMMENT '备注'"
            ))
            print("[迁移] hy_farmer.remark 已添加")

        # 3. 检查并添加 country 字段
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_farmer' AND COLUMN_NAME='country'"
        ))
        if not result.scalar():
            await conn.execute(text(
                "ALTER TABLE hy_farmer ADD COLUMN country VARCHAR(32) DEFAULT '中国' COMMENT '国家'"
            ))
            print("[迁移] hy_farmer.country 已添加")

        # 4. 检查并添加 language 字段
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_farmer' AND COLUMN_NAME='language'"
        ))
        if not result.scalar():
            await conn.execute(text(
                "ALTER TABLE hy_farmer ADD COLUMN language VARCHAR(32) DEFAULT '中文简体' COMMENT '语言'"
            ))
            print("[迁移] hy_farmer.language 已添加")

    # 5. 插入登录记录种子数据
    async with async_session_factory() as db:
        count_result = await db.execute(text("SELECT COUNT(*) FROM hy_farmer_login"))
        count = count_result.scalar()
        if count == 0:
            # 获取所有农户 ID
            farmers_result = await db.execute(text("SELECT id, username FROM hy_farmer"))
            farmers = farmers_result.fetchall()
            for farmer_row in farmers:
                farmer_id = farmer_row[0]
                # 为每个农户插入 3~5 条模拟登录记录
                records = [
                    {"farmer_id": farmer_id, "ip": "117.151.26.44", "time": "2026-07-20 09:15:32"},
                    {"farmer_id": farmer_id, "ip": "117.151.26.44", "time": "2026-07-18 14:22:10"},
                    {"farmer_id": farmer_id, "ip": "220.181.108.96", "time": "2026-07-15 08:33:45"},
                    {"farmer_id": farmer_id, "ip": "36.4.8.54", "time": "2026-07-10 19:07:21"},
                    {"farmer_id": farmer_id, "ip": "106.11.34.12", "time": "2026-07-05 11:48:56"},
                ]
                for rec in records:
                    await db.execute(text(
                        "INSERT INTO hy_farmer_login (farmer_id, last_login_ip, create_time) "
                        "VALUES (:farmer_id, :ip, :time)"
                    ), {"farmer_id": rec["farmer_id"], "ip": rec["ip"], "time": rec["time"]})
            await db.commit()
            print(f"[种子] 已为 {len(farmers)} 个农户插入登录记录")
        else:
            print(f"[种子] hy_farmer_login 已有 {count} 条记录，跳过")

    print("[完成] 迁移 + 种子数据执行完毕")


if __name__ == "__main__":
    asyncio.run(migrate())
