# -*- coding: utf-8 -*-
"""小智 AI MCP addon 生命周期入口。"""
import logging
from pathlib import Path

from sqlalchemy import delete

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)

PLUGIN_NAME = "xiaozhi_mcp"
MENU_PATH = "/admin/plugin/xiaozhi_mcp/xiaozhi_mcp"


class Plugin(BasePlugin):
    """维护小智 WebSocket MCP 桥的安装状态和运行时生命周期。"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "小智 AI MCP"
        self.version = "2.0.0"
        self.description = "将小智 AI 接入点桥接到插件专属自定义 MCP 工具体系"
        self.module = "addon"
        self._config_manager = ConfigManager()

    async def install(self) -> bool:
        """创建插件专属表并写入默认配置。"""
        if not self.db:
            return False
        await self._run_sql_file("install.sql")
        for key, value in (self.config or {}).items():
            if await self._config_manager.get(key, self.db) is None:
                await self._config_manager.set(
                    key,
                    str(value),
                    self.db,
                    description="小智 AI MCP 插件配置",
                )
        logger.info("[xiaozhi_mcp] 插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """停止连接并清理插件专属表、配置和后台菜单。"""
        if not self.db:
            return False
        from core.db.configuration import ConfigurationModel
        from core.db.menu import Menu
        from plugins.addon.xiaozhi_mcp.service import xiaozhi_bridge

        await xiaozhi_bridge.stop("插件已卸载")
        await self._run_sql_file("uninstall.sql")
        await self.db.execute(delete(ConfigurationModel).where(
            ConfigurationModel.key.like(f"{PLUGIN_NAME}.%")
        ))
        await self.db.execute(delete(Menu).where(Menu.plugin == PLUGIN_NAME))
        logger.info("[xiaozhi_mcp] 插件卸载完成")
        return True

    def get_routers(self):
        """返回管理员端专用配置路由。"""
        from plugins.addon.xiaozhi_mcp.router import router
        return [router]

    def get_event_subscriptions(self):
        """显式声明生命周期与配置事件订阅。"""
        from core.events import EventSubscription
        return [
            EventSubscription("system.startup", PLUGIN_NAME, self.on_app_startup),
            EventSubscription("system.shutdown", PLUGIN_NAME, self.on_app_shutdown),
            EventSubscription("plugin.installed", PLUGIN_NAME, self.on_plugin_installed),
            EventSubscription("config.changed", PLUGIN_NAME, self.on_config_changed),
        ]

    def get_permissions(self):
        """返回插件权限树。"""
        from plugins.addon.xiaozhi_mcp.auth import permission_tree
        return permission_tree

    def get_pages(self):
        """声明后台配置与运行监控页面。"""
        return [{
            "key": "plugin_xiaozhi_mcp",
            "title": "小智 AI MCP",
            "path": MENU_PATH,
            "icon": "link",
            "nav_type": "admin",
            "template": "xiaozhi_mcp.html", "audience": "admin",
            "permission": "xiaozhi_mcp:list",
            "api_base": "/api/admin/v1/xiaozhi-mcp",
        }]

    def get_config_schema(self):
        """敏感配置仅允许通过脱敏专用接口读写。"""
        return []

    async def on_app_startup(self, payload=None) -> None:
        """系统启动后按配置建立唯一常驻连接。"""
        await self._start_bridge()

    async def on_app_shutdown(self, payload=None) -> None:
        """系统关闭时回收 WebSocket 任务。"""
        from plugins.addon.xiaozhi_mcp.service import xiaozhi_bridge
        await xiaozhi_bridge.stop("系统正在关闭")

    async def on_runtime_enable(self) -> None:
        """运行时启用插件后立即恢复连接。"""
        await self._start_bridge()

    async def on_runtime_disable(self) -> None:
        """运行时禁用插件前立即关闭连接。"""
        from plugins.addon.xiaozhi_mcp.service import xiaozhi_bridge
        await xiaozhi_bridge.stop("插件已禁用")

    async def on_plugin_installed(self, payload: dict) -> None:
        """运行时安装本插件后立即应用连接配置。"""
        if payload.get("plugin_name") == PLUGIN_NAME:
            await self._start_bridge()

    async def on_config_changed(self, payload: dict) -> None:
        """兼容通用配置入口，插件配置变化后幂等重连。"""
        key = payload.get("key", "")
        if key.startswith(f"{PLUGIN_NAME}."):
            from plugins.addon.xiaozhi_mcp.service import xiaozhi_bridge
            await xiaozhi_bridge.restart()

    @staticmethod
    async def _start_bridge() -> None:
        from plugins.addon.xiaozhi_mcp.service import xiaozhi_bridge
        await xiaozhi_bridge.start()

    async def _run_sql_file(self, filename: str) -> None:
        """按 UTF-8 读取并执行插件迁移 SQL。"""
        sql_path = Path(__file__).parent / "migrations" / filename
        if not sql_path.exists():
            return
        with open(sql_path, "r", encoding="utf-8") as sql_file:
            sql = sql_file.read()
        if sql.strip():
            await self._exec_sql(sql)
