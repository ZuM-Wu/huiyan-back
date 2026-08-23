"""
迁移脚本 - 清理已废弃的 API规则白名单权限机制

背景：
    API规则白名单机制（hy_api_rule 表 + WhitelistPermissionMiddleware）借鉴 ZJMF 架构，
    但从未实际使用，表始终为空。权限控制已由路由级 require_permission() 独立承担。
    本脚本幂等删除相关表和种子数据，保持数据库整洁。

运行方式：
    随应用启动由 core/lifespan.py 的 _run_field_migrations() 自动调用
    或手动执行: python migrations/drop_api_rule_tables.py
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
    """幂等迁移：删除 hy_api_rule / hy_permission_rule_link 表 + 清理相关种子数据

    通过 DROP TABLE IF EXISTS 保证幂等，可安全重复执行。
    """
    async with engine.begin() as conn:
        # 1. 删除权限节点关联（hy_permission_rule_link）
        await conn.execute(text("DROP TABLE IF EXISTS `hy_permission_rule_link`"))

        # 2. 删除 API规则表（hy_api_rule）
        await conn.execute(text("DROP TABLE IF EXISTS `hy_api_rule`"))

        # 3. 清理 hy_permission 中已废弃的权限码
        await conn.execute(text(
            "DELETE FROM `hy_permission` WHERE `code` IN ("
            "'page:api_rules', 'rule:list', 'rule:create', 'rule:update', "
            "'rule:delete', 'permission:rule'"
            ")"
        ))

        # 4. 清理 hy_menu 中已废弃的菜单记录
        await conn.execute(text(
            "DELETE FROM `hy_menu` WHERE `name` IN ('api_rules', 'permission_rule')"
        ))

        logger.debug("[迁移] 已清理 API规则白名单相关表和种子数据（幂等）")


if __name__ == "__main__":
    asyncio.run(migrate())
