# -*- coding: utf-8 -*-
"""
农业知识库插件主类

功能概述：
- 管理员维护二级分类（大类>子类）与知识条目（发生原因/解决方案/典型图片），
  典型图片复用系统通用图片上传接口
- 农户在农户端浏览知识条目并可对某条内容提交勘误建议，管理员审核采纳/驳回
- 接入 MCP：搜索/详情/分类树（audience=both）+ 新建/批量新建条目（audience=admin），
  供后续 AI 农业分析调用

生命周期遵循五步 install / uninstall 契约（参照 file_download 插件）：
- install：建表（migrations/install.sql，含分类种子）+ 写默认配置（幂等）
- uninstall：删表 + 清配置 + 删菜单

注意：事务由框架层 PluginManager 统一管理，install/uninstall 内禁止 db.commit()。
- MCP handler 为模块级函数（mcp_tools.py），不携带 self
"""
import logging
from pathlib import Path

from sqlalchemy import delete

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin
from plugins.addon.knowledge.mcp_tools import (
    knowledge_batch_create,
    knowledge_categories,
    knowledge_create,
    knowledge_detail,
    knowledge_search,
    knowledge_update,
)

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
        self.version = "2.0.0"
        self.description = "管理员维护农业知识分类与条目，农户浏览与勘误，接入 MCP 供 AI 农业分析"
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
        3. 级联删除权限 — PluginManager 调用 unregister_plugin_permissions() 处理
        4. 清理配置（删除所有 knowledge. 前缀配置）
        5. 删除后台菜单，返回 True
        """
        if not self.db:
            return False

        # 1. 删除数据表
        await self._run_sql_file("uninstall.sql")

        # 2. 运行时能力注销由 PluginManager 处理
        # 3. 级联删除权限（PluginManager 处理）

        # 4. 清理配置
        from core.db.configuration import ConfigurationModel
        await self.db.execute(
            delete(ConfigurationModel).where(ConfigurationModel.key.like(f"{PLUGIN_NAME}.%"))
        )

        # 5. 删除后台菜单（事务由框架层统一管理，插件不自行 commit）
        from core.db.menu import Menu
        await self.db.execute(delete(Menu).where(Menu.plugin == PLUGIN_NAME))

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

    def get_mcp_tools(self):
        """
        声明插件的 MCP 工具（注册名自动加插件名前缀 knowledge_）

        - knowledge_search / knowledge_detail / knowledge_categories：audience=both，
          供管理员与农户端 AI 检索
        - knowledge_create：audience=admin，新建条目（不含典型图片，需后台补传）
        - knowledge_batch_create：audience=admin，批量新建条目（仅录标题，需后台补图）
        - knowledge_update：audience=admin，增量更新条目（仅写非空参数，图片不可改）
        permission_code 复用 RBAC 权限码，与后台接口同权限口径。
        """
        return [
            {
                "name": "search",
                "description": "搜索农业知识条目，支持按关键词/分类ID/适用作物筛选，"
                               "返回条目摘要列表（id/标题/分类名/作物/摘要）。",
                "handler": knowledge_search,
                "audience": "both",
                "permission_code": "knowledge:list",
            },
            {
                "name": "detail",
                "description": "查询农业知识条目详情，返回完整字段：典型图片URL列表、"
                               "发生原因、解决方案、适用作物等。",
                "handler": knowledge_detail,
                "audience": "both",
                "permission_code": "knowledge:list",
            },
            {
                "name": "categories",
                "description": "列出农业知识库的二级分类树（大类>子类，仅启用分类）。",
                "handler": knowledge_categories,
                "audience": "both",
                "permission_code": "knowledge:list",
            },
            {
                "name": "create",
                "description": "新建农业知识条目（不含典型图片，需后续在后台编辑上传补充）。"
                               "参数：title/category_id 必填，crop/summary/cause/solution 选填。",
                "handler": knowledge_create,
                "audience": "admin",
                "permission_code": "knowledge:create",
            },
            {
                "name": "batch_create",
                "description": "批量新建农业知识条目（共享分类/作物，仅录标题）。"
                               "参数：category_id/titles 必填，crop 选填；典型图片需后台补传。",
                "handler": knowledge_batch_create,
                "audience": "admin",
                "permission_code": "knowledge:create",
            },
            {
                "name": "update",
                "description": "更新农业知识条目（增量语义：仅写入非空参数，未传字段保持原值）。"
                               "参数：entry_id 必填，title/category_id/crop/summary/cause/"
                               "solution 选填；典型图片不可经 MCP 修改，需后台上传。",
                "handler": knowledge_update,
                "audience": "admin",
                "permission_code": "knowledge:update",
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
