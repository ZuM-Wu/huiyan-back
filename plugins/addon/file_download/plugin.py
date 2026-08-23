# -*- coding: utf-8 -*-
"""
文件下载插件主类

功能概述：
- 管理员按文件夹整理并上传文件、控制可见范围（所有农户 / 指定产区）
- 农户在农户端浏览可见文件并下载，系统记录下载次数

生命周期遵循五步 install / uninstall 契约（参照 hello_world 模板插件）：
- install：建表（migrations/install.sql）+ 写默认配置（幂等）
- uninstall：删表 + 清配置 + 删菜单 + 清空插件私有 upload/ 目录内的物理文件

注意：事务由框架层 PluginManager 统一管理，install/uninstall 内禁止 db.commit()。
"""
import logging
from pathlib import Path

from sqlalchemy import delete

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)

# 插件常量
PLUGIN_NAME = "file_download"
# 后台菜单指向的插件页面地址（对应 ViewController 的 /admin/plugin/{name}/{page} 路由）
MENU_PATH = "/admin/plugin/file_download/file_download"
# 物理文件存储目录（插件私有，不在 /upload 无鉴权静态挂载范围内，下载必须走鉴权接口）
UPLOAD_DIR = Path(__file__).parent / "upload"


async def mcp_list_files(keywords: str = "", folder_id: int = 0) -> list[dict]:
    """MCP 工具 handler：查询插件内的文件列表（最多 100 条）

    模块级函数（非 Plugin 方法）：工具 schema 由函数签名生成，
    不能携带 self 参数；按 registry 协定内部自建 DB 会话。
    """
    from sqlalchemy import select
    from core.db.base import async_session_factory
    from plugins.addon.file_download.models import FileDownloadFile, FileDownloadFolder

    async with async_session_factory() as db:
        q = (
            select(FileDownloadFile, FileDownloadFolder.name)
            .join(FileDownloadFolder, FileDownloadFolder.id == FileDownloadFile.folder_id, isouter=True)
            .where(FileDownloadFile.hidden == 0)
        )
        if folder_id:
            q = q.where(FileDownloadFile.folder_id == folder_id)
        if keywords:
            q = q.where(FileDownloadFile.name.contains(keywords))
        rows = (await db.execute(q.order_by(FileDownloadFile.id.desc()).limit(100))).all()

    return [
        {
            "id": f.id, "name": f.name, "folder": folder_name or "",
            "filetype": f.filetype, "filesize": f.filesize,
            "download_count": f.download_count, "description": f.description,
        }
        for f, folder_name in rows
    ]


class Plugin(BasePlugin):
    """文件下载插件主类"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "文件下载"
        self.version = "2.0.0"
        self.description = "管理员分文件夹上传文件并控制可见范围，农户在农户端浏览下载"
        self.module = "addon"
        self._config_manager = ConfigManager()

    # ------------------------------------------------------------------
    # 安装 — 五步契约
    # ------------------------------------------------------------------
    async def install(self) -> bool:
        """
        安装插件（五步）：
        1. 创建数据表（执行 migrations/install.sql，含默认文件夹幂等种子）
        2. 注册显式能力 — 声明文件下载事件
        3. 注册权限节点 — 由 PluginManager 从 get_permissions() 统一注册
        4. 写入默认配置（键前缀 file_download.，幂等不覆盖）
        5. 菜单不自动注册，由管理员通过「导航管理」手动添加，返回 True
        """
        if not self.db:
            return False

        # 1. 创建数据表（含默认文件夹种子数据）
        await self._run_sql_file("install.sql")

        # 2. 显式能力由 PluginManager 负责注册
        # 3. 权限节点（get_permissions 返回，PluginManager 负责注册）

        # 4. 写入默认配置（幂等：已存在则不覆盖）
        for key, value in (self.config or {}).items():
            existing = await self._config_manager.get(key, self.db)
            if existing is None:
                await self._config_manager.set(
                    key, str(value), self.db, description="文件下载插件配置"
                )

        # 5. 菜单由导航管理手动添加（get_pages() 声明可用页面）

        logger.info("[file_download] 插件安装完成")
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
        4. 清理配置（删除所有 file_download. 前缀配置）
        5. 删除后台菜单 + 清空插件私有 upload/ 目录物理文件，返回 True
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

        # 5. 删除后台菜单 + 清空物理文件
        from core.db.menu import Menu
        await self.db.execute(delete(Menu).where(Menu.plugin == PLUGIN_NAME))
        # 事务由框架层 PluginManager 统一管理，插件不自行 commit
        self._cleanup_upload_dir()

        logger.info("[file_download] 插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def get_routers(self):
        """显式声明管理员端与农户端路由。"""
        from plugins.addon.file_download.router import farmer_router, router
        return [router, farmer_router]

    def get_event_definitions(self):
        """声明文件下载完成瞬时事件，供审计或统计插件订阅。"""
        from pydantic import BaseModel, ConfigDict
        from core.events import EventDefinition

        class FileDownloadedPayload(BaseModel):
            model_config = ConfigDict(extra="forbid")
            file_id: int
            farmer_id: int

        return [EventDefinition(
            "file_download.downloaded", "农户下载文件", "文件", 1,
            PLUGIN_NAME, FileDownloadedPayload, "transient",
            export_fields=frozenset({"file_id", "farmer_id"}),
        )]

    def get_permissions(self):
        """返回插件权限树"""
        from plugins.addon.file_download.auth import permission_tree
        return permission_tree

    def get_pages(self):
        """
        声明插件对外提供的页面（供导航管理"插件页面"选择器按归属分组展示）：
        - 后台页面：文件下载管理页，对应 /admin/plugin/file_download/file_download
        - 前台页面：农户端资料下载页，对应 /farmer/plugin/file_download/file_download_index。
          模板名唯一化为 file_download_index.html，避免与其他插件的农户端
          模板同名（农户模板经 ChoiceLoader 扁平收集，同名会互相劫持）。
        """
        return [
            {
                "key": "plugin_file_download",
                "title": "文件下载",
                "path": MENU_PATH,
                "icon": "folder",
                "nav_type": "admin",
                "template": "file_download.html", "audience": "admin",
                "permission": "file_download:list",
                "api_base": "/api/admin/v1/file-download",
            },
            {
                "key": "plugin_file_download_front",
                "title": "资料下载",
                "path": "/farmer/plugin/file_download/file_download_index",
                "icon": "folder",
                "nav_type": "frontend",
                "template": "file_download_index.html", "audience": "farmer",
                "permission": "file_download:list",
                "api_base": "/api/v1/file-download",
            },
        ]

    def get_config_schema(self):
        """上传规则已迁移到系统上传设置，本插件无其他可编辑配置。"""
        return []

    def get_upload_policy_schema(self):
        """声明由系统上传设置统一管理的资料上传策略。"""
        from plugins.addon.file_download.upload_policies import POLICIES
        return POLICIES

    def get_mcp_tools(self):
        """
        声明插件的 MCP 工具（插件侧参考实现）

        工具实际注册名为 file_download_list_files（registry 自动加插件名前缀）；
        permission_code 复用现有 RBAC 权限码，与后台接口同权限口径。
        """
        return [
            {
                "name": "list_files",
                "description": "查询文件下载插件中的文件列表，支持按关键词和文件夹ID筛选，"
                               "返回文件名/所属文件夹/类型/大小/下载次数。",
                "handler": mcp_list_files,
                "audience": "admin",
                "permission_code": "file_download:list",
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

    def _cleanup_upload_dir(self) -> None:
        """
        清空插件私有 upload/ 目录内的物理文件（仅删文件，保留目录）。

        安全防线：resolve 后断言路径确实位于本插件目录下的 upload 子目录，
        防止任何路径异常导致误删插件目录之外的文件。
        """
        upload_dir = UPLOAD_DIR.resolve()
        plugin_dir = Path(__file__).parent.resolve()
        if upload_dir.parent != plugin_dir or upload_dir.name != "upload":
            logger.warning("[file_download] upload 目录路径异常，跳过清理: %s", upload_dir)
            return
        if not upload_dir.is_dir():
            return
        for item in upload_dir.iterdir():
            if item.is_file():
                try:
                    item.unlink()
                except OSError:
                    logger.warning("[file_download] 物理文件删除失败: %s", item)
