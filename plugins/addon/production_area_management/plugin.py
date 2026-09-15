# -*- coding: utf-8 -*-
"""产区管理演示插件生命周期与页面声明。"""

import logging
from pathlib import Path

from core.plugin_base import BasePlugin
from plugins.addon.production_area_management.services.demo_service import DemoService

logger = logging.getLogger(__name__)

PLUGIN_NAME = "production_area_management"
MENU_PATH = "/admin/plugin/production_area_management/index"


class Plugin(BasePlugin):
    """产区管理演示插件主类。"""

    def __init__(self, db_session=None, config: dict | None = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "产区管理"
        self.version = "1.0.0"
        self.description = "固定演示流程、真实产区天气与积温、AI 任务生成和任务反馈"
        self.module = "addon"

    async def install(self) -> bool:
        """创建插件自有表并播种历史演示数据。"""
        if not self.db:
            return False
        await self._run_sql_file("install.sql")
        await DemoService().seed_initial(self.db)
        logger.info("[production_area_management] 插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """删除插件自有表，运行时资源由平台统一注销。"""
        if not self.db:
            return False
        await self._run_sql_file("uninstall.sql")
        logger.info("[production_area_management] 插件卸载完成")
        return True

    def get_routers(self):
        """声明管理员端路由。"""
        from plugins.addon.production_area_management.router import router
        return [router]

    def get_permissions(self):
        """返回插件权限树。"""
        from plugins.addon.production_area_management.auth import permission_tree
        return permission_tree

    def get_pages(self):
        """声明产区管理演示页面，由平台自动注册到“应用”菜单。"""
        return [
            {
                "key": "plugin_production_area_management",
                "title": "产区管理",
                "path": MENU_PATH,
                "icon": "location",
                "nav_type": "admin",
                "template": "index.html",
                "audience": "admin",
                "styles": ["index.css", "index-detail.css"],
                "scripts": ["index.js"],
                "permission": "production_area_management:list",
                "api_base": "/api/admin/v1/plugins/production_area_management",
            }
        ]

    def get_config_schema(self):
        """演示插件不暴露可编辑配置。"""
        return []

    async def _run_sql_file(self, filename: str) -> None:
        """读取并执行插件 migrations 下的 SQL 文件。"""
        sql_path = Path(__file__).parent / "migrations" / filename
        if not sql_path.is_file():
            return
        await self._exec_sql(sql_path.read_text(encoding="utf-8"))

    async def repair_database(self, report: dict) -> dict | bool:
        """按幂等 install.sql 补齐同版本表结构漂移。"""
        return await self._repair_from_install_sql()
