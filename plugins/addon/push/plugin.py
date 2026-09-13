# -*- coding: utf-8 -*-
"""
手动推送 addon 插件 — 插件入口

功能：
- 五步 install / 五步 uninstall 契约
- 建表（读取 migrations/install.sql 执行）
- 权限树注册
- 默认配置写入
- 后台菜单声明（get_pages）
- 路由注册（get_router）
- 调度器启停钩子
"""
import logging
from pathlib import Path

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)

PLUGIN_NAME = "push"
MENU_PATH = "/admin/plugin/push/notice-push"


async def _handle_push_execute(context, task_data: dict) -> None:
    """任务队列 push_execute handler — 执行推送任务"""
    from plugins.addon.push.executor import execute_task
    task_id = task_data.get("task_id", 0)
    if task_id:
        await execute_task(task_id)


class Plugin(BasePlugin):
    """手动推送插件主类"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "推送中心"
        self.version = "1.0.2"
        self.description = "统一管理站内信、短信、邮件推送，支持目标筛选、预览、调度和投递日志"
        self.module = "addon"
        self._config_manager = ConfigManager()

    # ------------------------------------------------------------------
    # 安装 — 五步契约
    # ------------------------------------------------------------------
    async def install(self) -> bool:
        """
        安装插件（五步）：
        1. 创建数据表（执行 migrations/install.sql）
        2. 注册钩子 — 无钩子
        3. 注册权限节点 — 由 PluginManager 从 get_permissions() 统一注册
        4. 写入默认配置
        5. 声明页面（get_pages），由管理员通过导航管理手动添加菜单
        """
        if not self.db:
            return False

        # 1. 创建数据表
        await self._run_sql_file("install.sql")

        # 2. 注册钩子（无）
        # 3. 注册权限节点（get_permissions 返回，PluginManager 负责）

        # 4. 写入默认配置（幂等）
        for key, value in (self.config or {}).items():
            existing = await self._config_manager.get(key, self.db)
            if existing is None:
                await self._config_manager.set(
                    key, str(value), self.db, description="手动推送插件配置"
                )

        # 5. 页面声明（get_pages），菜单由管理员手动添加

        logger.info("[push] 插件安装完成")
        return True

    # ------------------------------------------------------------------
    # 卸载 — 五步逆操作
    # ------------------------------------------------------------------
    async def uninstall(self) -> bool:
        """
        卸载插件（五步逆操作）：
        1. 删除数据表
        2. 注销钩子（无）
        3. 级联删除权限、配置、导航和菜单（PluginManager 处理）
        4. 注销运行时能力（PluginManager 处理）
        5. 返回 True
        """
        if not self.db:
            return False

        # 1. 删除数据表
        await self._run_sql_file("uninstall.sql")

        # 2. 注销钩子（无）
        # 3-5. 权限、配置、导航、菜单和运行时能力由 PluginManager 统一清理。

        logger.info("[push] 插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def get_routers(self):
        """返回插件管理员端 APIRouter"""
        from plugins.addon.push.router import router
        return [router]

    def get_event_subscriptions(self):
        from core.events import EventSubscription
        return [
            EventSubscription("system.startup", PLUGIN_NAME, self.on_app_startup),
            EventSubscription("system.shutdown", PLUGIN_NAME, self.on_app_shutdown),
            EventSubscription("plugin.installed", PLUGIN_NAME, self.on_plugin_installed),
        ]

    def get_task_definitions(self):
        from services.task.definitions import TaskDefinition
        return [TaskDefinition(
            "push_execute", "执行推送任务", PLUGIN_NAME, "push",
            _handle_push_execute, timeout_seconds=300, max_attempts=3,
            concurrency=2, failure_notifications=True,
        )]

    def get_permissions(self):
        """返回插件权限树"""
        from plugins.addon.push.auth import permission_tree
        return permission_tree

    def get_pages(self):
        """
        声明插件对外提供的页面：
        - notice-push: 推送任务列表
        - notice-push-add: 创建/编辑推送任务
        单页模式：列表 + 创建弹窗 + 详情弹窗 + 日志弹窗，仅注册主列表页。
        """
        return [
            {
                "key": "plugin_push_list",
                "title": "推送中心",
                "path": MENU_PATH,
                "icon": "send",
                "nav_type": "admin",
                "template": "notice-push.html", "audience": "admin",
                "permission": "push:list",
                "api_base": "/api/admin/v1/push",
            },
            {
                "key": "plugin_push_form", "title": "推送任务表单",
                "path": "/admin/plugin/push/push-task-form",
                "icon": "edit", "nav_type": "admin", "hidden": True,
                "template": "push-task-form.html", "audience": "admin",
                "scripts": ["push-task-form.js"],
                "permission": "push:create", "api_base": "/api/admin/v1/push",
            },
        ]

    # ------------------------------------------------------------------
    # 调度器管理
    # ------------------------------------------------------------------
    async def on_app_startup(self, payload=None) -> None:
        """应用启动时恢复推送调度任务。"""
        await self._activate_runtime()

    async def on_runtime_enable(self) -> None:
        """插件运行时启用后立即恢复队列处理器和调度任务。"""
        await self._activate_runtime()

    async def on_plugin_installed(self, payload: dict) -> None:
        """运行时安装本插件后立即激活，避免错过 system_startup。"""
        if payload.get("plugin_name") == PLUGIN_NAME:
            await self._activate_runtime()

    async def _activate_runtime(self) -> None:
        """幂等注册队列处理器并恢复推送调度。"""
        from plugins.addon.push.scheduler import (
            register_push_scheduler,
            register_waiting_onetime_tasks,
        )
        await register_waiting_onetime_tasks()
        interval = int(self.config.get("push_center.scheduler_interval", "60") or 60)
        await register_push_scheduler(interval)

    async def on_app_shutdown(self, payload=None) -> None:
        """应用关闭时移除推送调度定时任务"""
        from plugins.addon.push.scheduler import remove_push_scheduler
        await remove_push_scheduler()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    async def _run_sql_file(self, filename: str) -> None:
        """读取 migrations/ 下的 SQL 文件并执行"""
        sql_path = Path(__file__).parent / "migrations" / filename
        if not sql_path.exists():
            return
        with open(sql_path, "r", encoding="utf-8") as f:
            sql = f.read()
        if sql.strip():
            await self._exec_sql(sql)
    async def repair_database(self, report: dict) -> dict | bool:
        return await self._repair_from_install_sql()
