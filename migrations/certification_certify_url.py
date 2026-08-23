"""
迁移脚本 - 为 hy_certification_record 表新增 certify_url 字段

背景：
    第三方认证（如芝麻信用）返回的认证链接此前只随 /submit 响应返回一次，
    用户刷新页面或退出重进后即永久丢失认证入口。
    将链接持久化到认证记录表，/status 接口在待审核期间回放给前端，
    供用户重新扫码继续认证。

运行方式：
    随应用启动由 core/lifespan.py 的 _run_field_migrations() 自动调用
    或手动执行: python migrations/certification_certify_url.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text

from core.db.base import engine

logger = logging.getLogger(__name__)


async def migrate(db=None):
    """幂等迁移：为 hy_certification_record 添加 certify_url 列

    通过 information_schema.COLUMNS 探测列是否存在，缺失时才执行 ALTER，
    可随应用启动重复执行而无副作用。
    """
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_certification_record' "
            "AND COLUMN_NAME='certify_url'"
        ))
        if not result.scalar():
            await conn.execute(text(
                "ALTER TABLE hy_certification_record "
                "ADD COLUMN certify_url VARCHAR(512) DEFAULT '' "
                "COMMENT '第三方认证链接（待认证期间供用户重新获取）' AFTER cert_no"
            ))
            logger.info("[迁移] hy_certification_record.certify_url 字段已添加")


if __name__ == "__main__":
    asyncio.run(migrate())
