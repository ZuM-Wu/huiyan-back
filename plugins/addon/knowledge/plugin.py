# -*- coding: utf-8 -*-
"""
农业知识库插件主类

功能概述：
- 管理员维护二级分类（大类>子类）与知识条目（发生原因/解决方案/典型图片），
  典型图片复用系统通用图片上传接口
- 农户在农户端浏览知识条目并可对某条内容提交勘误建议，管理员审核采纳/驳回
- 当前不声明 MCP 工具，后续统一规划工具边界后再接入

生命周期遵循五步 install / uninstall 契约（参照 file_download 插件）：
- install：建表（migrations/install.sql，含分类种子）+ 写默认配置（幂等）
- uninstall：删表 + 清配置 + 删菜单

注意：事务由框架层 PluginManager 统一管理，install/uninstall 内禁止 db.commit()。
"""
import logging
from pathlib import Path

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)

# 插件常量
PLUGIN_NAME = "knowledge"
# 后台菜单指向的插件页面地址（对应 ViewController 的 /admin/plugin/{name}/{page} 路由）
MENU_PATH = "/admin/plugin/knowledge/knowledge"


class Plugin(BasePlugin):
    """农业知识库插件主类"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "农业知识库"
        self.version = "1.0.2"
        self.description = "管理员维护农业知识分类与条目，农户浏览并提交勘误"
        self.module = "addon"
        self._config_manager = ConfigManager()

    # ------------------------------------------------------------------
    # 安装 — 五步契约
    # ------------------------------------------------------------------
    async def install(self) -> bool:
        """
        安装插件（五步）：
        1. 创建数据表（执行 migrations/install.sql，含分类种子数据）
        2. 注册显式能力 — 本插件当前无 Event/Pipeline/Task 声明
        3. 注册权限节点 — 由 PluginManager 从 get_permissions() 统一注册
        4. 写入默认配置（键前缀 knowledge.，幂等不覆盖）
        5. 菜单不自动注册，由管理员通过「导航管理」手动添加，返回 True
        """
        if not self.db:
            return False

        # 1. 创建数据表（含分类种子数据）
        await self._run_sql_file("install.sql")

        # 2. 显式能力由 PluginManager 负责注册
        # 3. 权限节点（get_permissions 返回，PluginManager 负责注册）

        # 4. 写入默认配置（幂等：已存在则不覆盖）
        for key, value in (self.config or {}).items():
            existing = await self._config_manager.get(key, self.db)
            if existing is None:
                await self._config_manager.set(
                    key, str(value), self.db, description="农业知识库插件配置"
                )

        # 5. 菜单由导航管理手动添加（get_pages() 声明可用页面）

        logger.info("[knowledge] 插件安装完成")
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

        logger.info("[knowledge] 插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def get_routers(self):
        """显式声明管理员端与农户端路由。"""
        from plugins.addon.knowledge.router import farmer_router, router
        return [router, farmer_router]

    def get_permissions(self):
        """返回插件权限树"""
        from plugins.addon.knowledge.auth import permission_tree
        return permission_tree

    def get_mcp_tools(self):
        """声明知识库查询与维护工具，随插件启停自动注册和注销。"""
        from plugins.addon.knowledge import mcp_tools
        return [
            {"name": "search", "description": "搜索农业知识条目。", "handler": mcp_tools.knowledge_search, "audience": "both", "permission_code": "knowledge:list"},
            {"name": "detail", "description": "读取农业知识条目详情。", "handler": mcp_tools.knowledge_detail, "audience": "both", "permission_code": "knowledge:list"},
            {"name": "categories", "description": "读取启用的农业知识分类树。", "handler": mcp_tools.knowledge_categories, "audience": "both", "permission_code": "knowledge:list"},
            {"name": "create", "description": "新建农业知识条目。", "handler": mcp_tools.knowledge_create, "audience": "admin", "permission_code": "knowledge:create"},
            {"name": "update", "description": "更新农业知识条目。", "handler": mcp_tools.knowledge_update, "audience": "admin", "permission_code": "knowledge:update"},
            {"name": "batch_create", "description": "批量新建农业知识条目。", "handler": mcp_tools.knowledge_batch_create, "audience": "admin", "permission_code": "knowledge:create"},
        ]

    def get_pages(self):
        """
        声明插件对外提供的页面（供导航管理"插件页面"选择器按归属分组展示）：
        - 后台页面：知识库管理页，对应 /admin/plugin/knowledge/knowledge
        - 前台页面：农户端知识库浏览页，对应
          /farmer/plugin/knowledge/knowledge_index。模板名唯一化为
          knowledge_index.html，避免与其他插件的农户端模板同名
          （农户模板经 ChoiceLoader 扁平收集，同名会互相劫持）。
        """
        return [
            {
                "key": "plugin_knowledge",
                "title": "农业知识库",
                "path": MENU_PATH,
                "icon": "books",
                "nav_type": "admin",
                "template": "knowledge.html", "audience": "admin",
                "scripts": ["knowledge.js"],
                "permission": "knowledge:list",
                "api_base": "/api/admin/v1/knowledge",
            },
            {
                "key": "plugin_knowledge_corrections",
                "title": "知识库勘误",
                "path": "/admin/plugin/knowledge/knowledge_corrections",
                "icon": "edit",
                "nav_type": "admin",
                "template": "knowledge_corrections.html", "audience": "admin",
                "scripts": ["knowledge_corrections.js"],
                "permission": "knowledge:correction",
                "api_base": "/api/admin/v1/knowledge",
            },
            {
                "key": "plugin_knowledge_front",
                "title": "农业知识库",
                "path": "/farmer/plugin/knowledge/knowledge_index",
                "icon": "books",
                "nav_type": "frontend",
                "template": "knowledge_index.html", "audience": "farmer",
                "permission": "knowledge:list",
                "api_base": "/api/v1/knowledge",
            },
        ]

    def get_config_schema(self):
        """返回配置表单 Schema（供后台「插件配置」弹窗动态渲染）"""
        return [
            {
                "key": "allow_correction",
                "label": "允许农户提交勘误",
                "type": "select",
                "required": True,
                "default": "1",
                "options": [
                    {"label": "允许", "value": "1"},
                    {"label": "关闭", "value": "0"},
                ],
                "help": "关闭后农户端提交勘误接口返回 403",
            },
            {
                "key": "list_page_size",
                "label": "列表默认每页条数",
                "type": "input",
                "required": True,
                "default": "10",
                "placeholder": "如 10",
                "help": "农户端与后台列表默认每页展示条数",
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
