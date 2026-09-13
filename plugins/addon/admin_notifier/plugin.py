# -*- coding: utf-8 -*-
"""
邮件通知管理员插件 — 插件入口

功能：
- 五步 install / 五步 uninstall 契约
- 建表（读取 migrations/install.sql 执行）
- 权限树注册（get_permissions）
- 可靠订阅 task.failed，由平台为本插件独立重试
- 默认配置写入
- 路由注册（get_router）

定时清理任务已移至核心 task_manager（clean_task_logs），不在插件职责范围内。
"""
import logging
from pathlib import Path

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)

PLUGIN_NAME = "admin_notifier"
MENU_PATH = "/admin/plugin/admin_notifier/admin_notifier"


class Plugin(BasePlugin):
    """邮件通知管理员插件主类"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "邮件通知管理员"
        self.version = "1.0.2"
        self.description = "任务失败时按配置发送邮件/短信通知管理员"
        self.module = "addon"
        self._config_manager = ConfigManager()

    # ------------------------------------------------------------------
    # 安装 — 五步契约
    # ------------------------------------------------------------------
    async def install(self) -> bool:
        """
        安装插件（五步）：
        1. 创建数据表（执行 migrations/install.sql）
        2. 注册显式能力 — 由 PluginManager 统一处理
        3. 注册权限节点 — 由 PluginManager 从 get_permissions() 统一注册
        4. 写入默认配置
        5. 返回 True
        """
        if not self.db:
            return False

        # 1. 创建数据表
        await self._run_sql_file("install.sql")

        # 2. 显式能力由 PluginManager 负责注册
        # 3. 注册权限节点（get_permissions 返回，PluginManager 负责注册）

        # 4. 写入默认配置（幂等：已存在则不覆盖）
        for key, value in (self.config or {}).items():
            existing = await self._config_manager.get(key, self.db)
            if existing is None:
                await self._config_manager.set(
                    key, str(value), self.db, description="邮件通知管理员插件配置"
                )

        logger.info("[admin_notifier] 插件安装完成")
        return True

    # ------------------------------------------------------------------
    # 卸载 — 五步逆操作
    # ------------------------------------------------------------------
    async def uninstall(self) -> bool:
        """
        卸载插件（五步逆操作）：
        1. 删除数据表（执行 migrations/uninstall.sql）
        2. 按 owner 注销全部运行时能力 — PluginManager 处理
        3. 级联删除权限、配置、导航和菜单 — PluginManager 处理
        4. 注销插件运行时能力 — PluginManager 处理
        5. 返回 True
        """
        if not self.db:
            return False

        # 1. 删除数据表
        await self._run_sql_file("uninstall.sql")

        # 2. 运行时能力注销由 PluginManager 处理
        # 3-5. 权限、配置、导航、菜单和运行时能力由 PluginManager 统一清理。

        logger.info("[admin_notifier] 插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def get_routers(self):
        """返回插件管理员端 APIRouter"""
        from plugins.addon.admin_notifier.router import router
        return [router]

    def get_event_subscriptions(self):
        """可靠订阅任务最终失败事件。"""
        from core.events import EventSubscription
        from plugins.addon.admin_notifier.hooks import on_task_failed
        return [EventSubscription(
            "task.failed", PLUGIN_NAME, on_task_failed,
            title="任务失败管理员通知", max_attempts=3,
            failure_notifications=False,
        )]

    def get_permissions(self):
        """返回插件权限树"""
        from plugins.addon.admin_notifier.auth import permission_tree
        return permission_tree

    def get_pages(self):
        """声明插件对外提供的页面（供导航管理"插件页面"选择器使用）"""
        return [
            {
                "key": "plugin_admin_notifier",
                "title": "邮件通知管理员",
                "path": MENU_PATH,
                "icon": "mail",
                "nav_type": "admin",
                "template": "admin_notifier.html", "audience": "admin",
                "scripts": ["admin_notifier.js"],
                "permission": "admin_notifier:config:view",
                "api_base": "/api/admin/v1/admin_notifier",
            },
        ]

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
