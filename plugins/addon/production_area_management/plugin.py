# -*- coding: utf-8 -*-
"""产区管理插件生命周期、配置和管理端页面声明。"""

import logging
from pathlib import Path

from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from core.plugin_base import BasePlugin

PLUGIN_NAME = "production_area_management"
MENU_PATH = "/admin/plugin/production_area_management/index"

logger = logging.getLogger(__name__)


class Plugin(BasePlugin):
    """产区管理事实台账插件。"""

    def __init__(self, db_session=None, config: dict | None = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "产区管理"
        self.version = "2.1.0"
        self.description = "按日同步真实产区事实、整改建议、任务与现场反馈"
        self.module = "addon"
        self._config_manager = ConfigManager()

    async def install(self) -> bool:
        """只创建插件表并补齐默认配置，不播种演示数据。"""
        if not self.db:
            return False
        await self._run_sql_file("install.sql")
        defaults = {
            "schedule_enabled": "1",
            "schedule_time": "06:00",
        }
        for key, value in defaults.items():
            full_key = f"{PLUGIN_NAME}.{key}"
            if await self._config_manager.get(full_key, self.db) is None:
                await self._config_manager.set(
                    full_key,
                    value,
                    self.db,
                    description="产区管理按日事实同步配置",
                )
        logger.info("[production_area_management] 插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """删除插件自有表，运行时调度由生命周期钩子先行注销。"""
        if not self.db:
            return False
        from plugins.addon.production_area_management.services.scheduler import remove_schedule

        remove_schedule()
        await self._run_sql_file("uninstall.sql")
        logger.info("[production_area_management] 插件卸载完成")
        return True

    def get_routers(self):
        """声明管理员端路由。"""
        from plugins.addon.production_area_management.router import router

        return [router]

    def get_task_definitions(self):
        """声明队列任务定义：周期调度只负责入队，按日事实写入由队列 Worker 执行。"""
        from plugins.addon.production_area_management.services.scheduler import task_definitions

        return task_definitions()

    def get_permissions(self):
        """返回插件权限树。"""
        from plugins.addon.production_area_management.auth import permission_tree

        return permission_tree

    def get_pages(self):
        """声明产区管理页面。"""
        return [{
            "key": "plugin_production_area_management",
            "title": "产区管理",
            "path": MENU_PATH,
            "icon": "location",
            "nav_type": "admin",
            "template": "index.html",
            "audience": "admin",
            "styles": ["index.css", "index-detail.css"],
            "scripts": ["index-demo.js", "index.js"],
            "permission": "production_area_management:list",
            "api_base": "/api/admin/v1/plugins/production_area_management",
        }]

    def get_config_schema(self):
        """暴露每日事实同步的启用开关和执行时间。"""
        return [
            {
                "key": "schedule_enabled",
                "label": "启用每日事实同步",
                "type": "switch",
                "default": "1",
                "help": "关闭后不再自动生成按日事实日志，可手动立即同步",
            },
            {
                "key": "schedule_time",
                "label": "每日同步时间",
                "type": "input",
                "required": True,
                "default": "06:00",
                "help": "使用北京时间，格式 HH:MM",
            },
        ]

    async def on_runtime_enable(self) -> None:
        """启用插件后按数据库配置恢复每日同步任务。"""
        from plugins.addon.production_area_management.services.scheduler import register_schedule

        async with async_session_factory() as db:
            enabled = str(await self._config_manager.get(
                f"{PLUGIN_NAME}.schedule_enabled", db,
            ) or "1") == "1"
            time_value = await self._config_manager.get(
                f"{PLUGIN_NAME}.schedule_time", db,
            ) or "06:00"
        await register_schedule(enabled, time_value)

    async def on_runtime_disable(self) -> None:
        """停用插件后移除每日同步任务。"""
        from plugins.addon.production_area_management.services.scheduler import remove_schedule

        remove_schedule()

    async def _run_sql_file(self, filename: str) -> None:
        """读取并执行插件 migrations 下的 SQL 文件。"""
        sql_path = Path(__file__).parent / "migrations" / filename
        if sql_path.is_file():
            await self._exec_sql(sql_path.read_text(encoding="utf-8"))

    async def repair_database(self, report: dict) -> dict | bool:
        """按幂等建表脚本补齐同版本表结构漂移。"""
        del report
        return await self._repair_from_install_sql()
