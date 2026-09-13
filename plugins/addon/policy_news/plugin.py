"""农业政策资讯插件生命周期与显式能力声明。"""
from pathlib import Path

from core.config_manager import ConfigManager
from core.plugin_base import BasePlugin
from services.task.definitions import TaskContext, TaskDefinition

PLUGIN_NAME = "policy_news"
MENU_PATH = "/admin/plugin/policy_news/policy_news"


async def refresh_task_handler(context: TaskContext, payload: dict) -> dict:
    """队列任务 handler：执行一次官方政策抓取。"""
    del context, payload
    from .service import refresh_policies
    return await refresh_policies()


class Plugin(BasePlugin):
    """农业政策资讯插件主类。"""

    def __init__(self, db_session=None, config: dict | None = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "农业政策资讯"
        self.version = "1.0.2"
        self.description = "获取农业农村部和广西农业农村厅最新政策"
        self.module = "addon"
        self._config_manager = ConfigManager()

    async def install(self) -> bool:
        """创建插件表并写入 manifest 声明的默认配置。"""
        if not self.db:
            return False
        await self._run_sql_file("install.sql")
        for key, value in self.config.items():
            if await self._config_manager.get(key, self.db) is None:
                await self._config_manager.set(
                    key, str(value), self.db, description="农业政策资讯插件配置"
                )
        return True

    async def uninstall(self) -> bool:
        """删除插件私有表，框架负责配置和运行时能力清理。"""
        if not self.db:
            return False
        await self._run_sql_file("uninstall.sql")
        return True

    def get_routers(self):
        """显式声明管理员端和农户端接口。"""
        from .router import admin_router, farmer_router
        return [admin_router, farmer_router]

    def get_permissions(self):
        """返回管理员端 RBAC 权限树。"""
        return [
            {"title": "农业政策资讯", "code": "policy_news:*",
             "url": MENU_PATH, "children": [
                 {"title": "查看政策", "code": "policy_news:list"},
                 {"title": "刷新政策", "code": "policy_news:refresh"},
                 {"title": "清理政策", "code": "policy_news:clear"},
             ]},
        ]

    def get_pages(self):
        """声明管理端和农户端插件页面。"""
        return [
            {
                "key": "plugin_policy_news",
                "title": "农业政策资讯",
                "path": MENU_PATH,
                "icon": "file-copy",
                "nav_type": "admin",
                "audience": "admin",
                "template": "policy_news.html",
                "scripts": ["policy_news.js"],
                "permission": "policy_news:list",
                "api_base": "/api/admin/v1/plugins/policy_news",
            },
            {
                "key": "plugin_policy_news_front",
                "title": "农业政策资讯",
                "path": "/farmer/plugin/policy_news/policy_news_index",
                "icon": "file-copy",
                "nav_type": "frontend",
                "audience": "farmer",
                "template": "policy_news_index.html",
                "scripts": ["policy_news_farmer.js"],
                "permission": "",
                "api_base": "/api/v1/plugins/policy_news",
            },
        ]

    def get_config_schema(self):
        """返回可调整的周期和保留条数配置，不暴露来源地址。"""
        return [
            {"key": "interval_hours", "label": "自动刷新周期（小时）", "type": "input",
             "required": True, "default": "6", "help": "范围 1-24 小时"},
            {"key": "retention_limit", "label": "历史保留条数", "type": "input",
             "required": True, "default": "100", "help": "范围 20-100 条"},
        ]

    def get_task_definitions(self):
        """声明队列执行任务，周期投递由运行时调度器负责。"""
        return [TaskDefinition(
            "policy_news.refresh", "刷新农业政策资讯", PLUGIN_NAME, "policy_news",
            refresh_task_handler, timeout_seconds=60, max_attempts=3, concurrency=1,
            backoff=True, failure_notifications=True,
        )]

    def get_widgets(self):
        """声明管理端和农户端共用的首页挂件。"""
        from .widget import PolicyNewsWidget
        return [PolicyNewsWidget()]

    async def on_runtime_enable(self) -> None:
        """启用后恢复周期投递任务。"""
        from .scheduler import register_schedule
        interval = int(self.config.get(f"{PLUGIN_NAME}.interval_hours", 6) or 6)
        await register_schedule(interval)

    async def on_runtime_disable(self) -> None:
        """禁用前释放周期投递任务。"""
        from .scheduler import remove_schedule
        remove_schedule()

    async def _run_sql_file(self, filename: str) -> None:
        """读取并执行插件 migrations 下的 SQL 文件。"""
        sql_path = Path(__file__).parent / "migrations" / filename
        if sql_path.exists():
            await self._exec_sql(sql_path.read_text(encoding="utf-8"))
    async def repair_database(self, report: dict) -> dict | bool:
        return await self._repair_from_install_sql()
