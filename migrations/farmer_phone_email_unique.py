"""
迁移脚本 - 为 hy_farmer 表的 phone/email 添加条件唯一索引

背景：
    验证码登录和密码找回依赖 phone/email 唯一性（一个手机号/邮箱只能对应一个农户）。
    使用条件唯一索引（仅对非空值生效），避免多个空值记录冲突。

    MySQL 8.0+ 支持函数索引，但此处用更简单的方案：
    - 唯一索引建在 phone/email 字段上
    - 空字符串 '' 视为非绑定状态，通过虚拟列实现"仅非空唯一"
    - 实际操作：先清理重复数据，再添加唯一索引

运行方式：
    随应用启动由 core/lifespan.py 的 _run_field_migrations() 自动调用
    或手动执行: python migrations/farmer_phone_email_unique.py
"""
import asyncio
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from core.db.base import engine

logger = logging.getLogger(__name__)


async def migrate(db=None):
    """
    幂等迁移：为 hy_farmer.phone 和 hy_farmer.email 添加唯一索引
    策略：跳过空值（空字符串），仅对有值的记录施加唯一约束
    MySQL 8.0.13+ 支持在唯一索引中通过函数表达式实现条件唯一
    此处使用更兼容的方案：先清理重复，再添加普通 UNIQUE INDEX（空字符串自然唯一）
    """
    async with engine.begin() as conn:
        # --- phone 唯一索引 ---
        # 检查索引是否已存在
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_farmer' "
            "AND INDEX_NAME='uq_farmer_phone'"
        ))
        if not result.scalar():
            # 清理重复手机号（保留 id 最小的）
            await conn.execute(text(
                "UPDATE hy_farmer SET phone = '' "
                "WHERE phone != '' AND id NOT IN ("
                "  SELECT min_id FROM ("
                "    SELECT MIN(id) as min_id FROM hy_farmer "
                "    WHERE phone != '' GROUP BY phone"
                "  ) AS t"
                ")"
            ))
            # 创建唯一索引（空字符串在 MySQL 中被视为一个值，
            # 但由于我们清理了重复，空字符串记录可能有多个，
            # 所以使用条件唯一：通过前缀索引+虚拟列不可行时，
            # 改用部分索引替代方案 — 仅当 phone != '' 时唯一）
            # MySQL 8.0 不支持 partial index，改用 generated column 方案
            # 简化方案：直接建普通唯一索引，把所有空 phone 置为 NULL
            await conn.execute(text(
                "UPDATE hy_farmer SET phone = NULL WHERE phone = ''"
            ))
            await conn.execute(text(
                "ALTER TABLE hy_farmer MODIFY COLUMN phone VARCHAR(32) NULL DEFAULT NULL COMMENT '手机号'"
            ))
            await conn.execute(text(
                "CREATE UNIQUE INDEX uq_farmer_phone ON hy_farmer(phone)"
            ))
            logger.info("[迁移] hy_farmer.phone 唯一索引已添加（NULL 不参与唯一约束）")

        # --- email 唯一索引 ---
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_farmer' "
            "AND INDEX_NAME='uq_farmer_email'"
        ))
        if not result.scalar():
            # 清理重复邮箱（保留 id 最小的）
            await conn.execute(text(
                "UPDATE hy_farmer SET email = '' "
                "WHERE email != '' AND id NOT IN ("
                "  SELECT min_id FROM ("
                "    SELECT MIN(id) as min_id FROM hy_farmer "
                "    WHERE email != '' GROUP BY email"
                "  ) AS t"
                ")"
            ))
            # 空值置 NULL（MySQL UNIQUE INDEX 允许多个 NULL）
            await conn.execute(text(
                "UPDATE hy_farmer SET email = NULL WHERE email = ''"
            ))
            await conn.execute(text(
                "ALTER TABLE hy_farmer MODIFY COLUMN email VARCHAR(128) NULL DEFAULT NULL COMMENT '邮箱'"
            ))
            await conn.execute(text(
                "CREATE UNIQUE INDEX uq_farmer_email ON hy_farmer(email)"
            ))
            logger.info("[迁移] hy_farmer.email 唯一索引已添加（NULL 不参与唯一约束）")

        # --- 存量空串归一（无条件幂等执行）---
        # 索引建成后历史代码仍可能写入 ''，占用唯一索引名额导致后续插入 Duplicate entry；
        # 每次启动将空串归一为 NULL（等值条件命中唯一索引为点查，0 行时开销可忽略）
        await conn.execute(text(
            "UPDATE hy_farmer SET phone = NULL WHERE phone = ''"
        ))
        await conn.execute(text(
            "UPDATE hy_farmer SET email = NULL WHERE email = ''"
        ))


if __name__ == "__main__":
    asyncio.run(migrate())
