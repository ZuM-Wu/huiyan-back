"""
迁移脚本 - 产区多农户绑定（hy_area_farmer 关联表回填）

将旧的「产区单农户」绑定（hy_production_area.farmer_id > 0）幂等回填到关联表 hy_area_farmer，
使多对多绑定生效。表本身由 SQLAlchemy Base.metadata.create_all 自动创建，本脚本只负责数据回填。

运行方式：
    - 随应用启动由 core/lifespan.py 的 _run_field_migrations() 自动调用 migrate(db)
    - 或在 HuiYan_Back 目录下手动执行：python migrations/area_farmer_binding.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text

from core.db.base import async_session_factory

logger = logging.getLogger(__name__)


async def migrate(db) -> None:
    """幂等回填：产区旧单农户绑定 → hy_area_farmer 关联表"""
    # 表可能尚未创建（极早期调用），create_all 之后必然存在；此处防御性检查
    exists = (await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_area_farmer'"
    ))).scalar()
    if not exists:
        return

    # 取所有仍有旧单农户绑定的产区
    rows = (await db.execute(text(
        "SELECT id, farmer_id FROM hy_production_area WHERE farmer_id > 0"
    ))).fetchall()

    inserted = 0
    for area_id, farmer_id in rows:
        # 关联表已有该 (area_id, farmer_id) 则跳过，保证幂等
        dup = (await db.execute(text(
            "SELECT COUNT(*) FROM hy_area_farmer "
            "WHERE area_id=:area_id AND farmer_id=:farmer_id"
        ), {"area_id": area_id, "farmer_id": farmer_id})).scalar()
        if not dup:
            await db.execute(text(
                "INSERT INTO hy_area_farmer (area_id, farmer_id) "
                "VALUES (:area_id, :farmer_id)"
            ), {"area_id": area_id, "farmer_id": farmer_id})
            inserted += 1

    if inserted:
        await db.commit()
        logger.info("[迁移] hy_area_farmer 回填旧绑定 %d 条", inserted)


async def _main():
    """独立运行入口"""
    async with async_session_factory() as db:
        await migrate(db)
    logger.info("[完成] 产区多农户绑定迁移执行完毕")


if __name__ == "__main__":
    asyncio.run(_main())
