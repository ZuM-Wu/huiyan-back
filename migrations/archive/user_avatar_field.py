"""
迁移脚本 - 为 hy_farmer 表新增 avatar 字段
运行方式：在 app 目录下执行
    python migrations/user_avatar_field.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from core.db.base import engine


async def migrate():
    """为 hy_farmer 表新增 avatar 字段（幂等）"""
    async with engine.begin() as conn:
        # 检查并添加 avatar 字段
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_farmer' AND COLUMN_NAME='avatar'"
        ))
        if not result.scalar():
            await conn.execute(text(
                "ALTER TABLE hy_farmer ADD COLUMN avatar VARCHAR(256) DEFAULT '' COMMENT '头像URL'"
            ))
            print("[迁移] hy_farmer.avatar 已添加")
        else:
            print("[迁移] hy_farmer.avatar 已存在，跳过")

    print("[完成] 头像字段迁移完毕")


if __name__ == "__main__":
    asyncio.run(migrate())
