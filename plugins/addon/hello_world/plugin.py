# -*- coding: utf-8 -*-
"""
Hello World 全量示例插件 — 插件开发参考模板

演示插件系统的完整能力：
- 五步 install / 五步 uninstall 契约
- 建表（读取 migrations/install.sql 执行）
- 权限树注册（get_permissions，由 PluginManager 统一写入）
- Event、Pipeline、Task 等能力通过显式声明注册
- 默认配置写入（ConfigManager，键前缀 hello_world.）
- 后台菜单注册（写入 hy_menu 表）
- 双路由注册：管理员端 router + 农户端 farmer_router
- API 路由（router.py，含 require_permission 细粒度鉴权 + active_log 操作日志）
- 前端页面（templates/admin/hello_world.html + templates/farmer/default/index.html）
"""
import logging
from pathlib import Path

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)

# 插件常量
PLUGIN_NAME = "hello_world"
# 后台菜单指向的插件页面地址（对应 ViewController 的 /admin/plugin/{name}/{page} 路由）
MENU_PATH = "/admin/plugin/hello_world/hello_world"


class Plugin(BasePlugin):
    """Hello World 示例插件主类"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "Hello World 示例插件"
        self.version = "1.0.2"
        self.description = "全量演示插件，供后续插件开发参考"
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
        4. 写入默认配置（键前缀 hello_world.）
        5. 注册后台菜单，返回 True
        """
        if not self.db:
            return False

        # 1. 创建数据表
        await self._run_sql_file("install.sql")

        # 2. 显式能力由 PluginManager 负责注册
        # 3. 注册权限节点（get_permissions 返回，PluginManager 负责注册，此处无需操作）

        # 4. 写入默认配置（幂等：已存在则不覆盖）
        for key, value in (self.config or {}).items():
            existing = await self._config_manager.get(key, self.db)
            if existing is None:
                await self._config_manager.set(
                    key, str(value), self.db, description="Hello World 插件配置"
                )

        # 5. 菜单不自动注册，由管理员通过「导航管理」手动添加（get_pages() 声明可用页面）

        logger.info("[hello_world] 插件安装完成")
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
        4. 注销运行时能力 — PluginManager 处理
        5. 返回 True
        """
        if not self.db:
            return False

        # 1. 删除数据表
        await self._run_sql_file("uninstall.sql")

        # 2. 运行时能力注销由 PluginManager 处理
        # 3-5. 权限、配置、导航、菜单和运行时能力由 PluginManager 统一清理。
        # 事务由框架层 PluginManager 统一管理，插件不自行 commit。

        logger.info("[hello_world] 插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def get_routers(self):
        """显式声明管理员端和农户端路由。"""
        from plugins.addon.hello_world.router import farmer_router, legacy_router, router
        return [router, farmer_router, legacy_router]

    def get_event_definitions(self):
        from pydantic import BaseModel, ConfigDict
        from core.events import EventDefinition

        class MessageCreatedPayload(BaseModel):
            model_config = ConfigDict(extra="forbid")
            message_id: int
            title: str

        return [EventDefinition(
            "hello_world.message_created", "示例留言创建", "示例", 1,
            PLUGIN_NAME, MessageCreatedPayload, "transient",
            export_fields=frozenset({"message_id", "title"}),
        )]

    def get_event_subscriptions(self):
        from core.events import EventSubscription
        from plugins.addon.hello_world.hooks import on_message_created, on_system_startup
        return [
            EventSubscription("system.startup", PLUGIN_NAME, on_system_startup),
            EventSubscription("hello_world.message_created", PLUGIN_NAME, on_message_created),
        ]

    def get_permissions(self):
        """返回插件权限树"""
        from plugins.addon.hello_world.auth import permission_tree
        return permission_tree

    def get_pages(self):
        """
        声明插件对外提供的页面（供导航管理"插件页面"选择器按归属分组展示）：
        - 后台页面：管理端示例页，对应 ViewController 的
          /admin/plugin/hello_world/hello_world 路由（真实可访问）。
        - 前台页面：农户端示例页，对应 FarmerViewController 的
          /farmer/plugin/{name}/{page} 路由，模板为
          templates/farmer/{主题}/index.html（回退 default）。
          path 必须写到 page 段（此处 index），方能匹配路由。
        """
        return [
            {
                "key": "plugin_hello_world",
                "title": "示例插件",
                "path": MENU_PATH,
                "icon": "chat",
                "nav_type": "admin",
                "template": "hello_world.html", "audience": "admin",
                "permission": "hello_world:list",
                "api_base": "/api/admin/v1/plugins/hello_world",
            },
            {
                "key": "plugin_hello_world_front",
                "title": "示例插件(前台)",
                "path": "/farmer/plugin/hello_world/index",
                "icon": "chat",
                "nav_type": "frontend",
                "template": "index.html", "audience": "farmer",
                "permission": "",
                "api_base": "/api/v1/plugins/hello_world",
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

    # _register_menu 已移除：插件不自动创建菜单，由管理员通过导航管理页面手动添加
    async def repair_database(self, report: dict) -> dict | bool:
        return await self._repair_from_install_sql()
