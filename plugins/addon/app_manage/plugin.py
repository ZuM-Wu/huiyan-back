# -*- coding: utf-8 -*-
"""
App管理插件主类

功能概述：
- App版本更新管理：管理员上传APK发版（强制更新标记），App端检查更新并直接下载
- 开屏广告管理：单张广告投放（时间段/展示时长/跳转链接/素材缓存标识）
- App公告管理：独立于核心 notice 模块的App专用公告

生命周期遵循五步 install / uninstall 契约（参照 file_download 插件）：
- install：建表（migrations/install.sql，含广告单行种子）+ 写默认配置（幂等）
- uninstall：删表 + 清配置 + 删菜单 + 清缓存键 + 清空 upload/app_manage/ 物理文件

注意：事务由框架层 PluginManager 统一管理，install/uninstall 内禁止 db.commit()。
- APK/广告图存储在项目公开静态目录 upload/app_manage/（经 /upload 免登录直接下载）
"""
import logging
from pathlib import Path

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)

# 插件常量
PLUGIN_NAME = "app_manage"
# 后台菜单指向的插件页面地址（对应 ViewController 的 /admin/plugin/{name}/{page} 路由）
MENU_PATH = "/admin/plugin/app_manage/app_manage"
# 项目后端根目录（plugins/addon/app_manage/plugin.py 向上三级）
BACKEND_ROOT = Path(__file__).resolve().parents[3]
# 物理文件存储目录（项目公开 upload/ 静态挂载下的插件子目录，App端免登录直接下载）
UPLOAD_DIR = BACKEND_ROOT / "upload" / PLUGIN_NAME


class Plugin(BasePlugin):
    """App管理插件主类"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "App管理"
        self.version = "1.0.1"
        self.description = "农户端App的版本更新、开屏广告与App公告管理，含App端免登录公开接口"
        self.module = "addon"
        self._config_manager = ConfigManager()

    # ------------------------------------------------------------------
    # 安装 — 五步契约
    # ------------------------------------------------------------------
    async def install(self) -> bool:
        """
        安装插件（五步）：
        1. 创建数据表（执行 migrations/install.sql，含广告单行幂等种子）
        2. 注册显式能力 — 本插件当前无 Event/Pipeline/Task 声明
        3. 注册权限节点 — 由 PluginManager 从 get_permissions() 统一注册
        4. 写入默认配置（键前缀 app_manage.，幂等不覆盖）
        5. 后台页面由 get_pages() 声明，安装时框架自动写入 hy_nav，返回 True
        """
        if not self.db:
            return False

        # 1. 创建数据表（含广告 id=1 单行种子）
        await self._run_sql_file("install.sql")

        # 2. 显式能力由 PluginManager 负责注册
        # 3. 权限节点（get_permissions 返回，PluginManager 负责注册）

        # 4. 写入默认配置（幂等：已存在则不覆盖）
        for key, value in (self.config or {}).items():
            existing = await self._config_manager.get(key, self.db)
            if existing is None:
                await self._config_manager.set(
                    key, str(value), self.db, description="App管理插件配置"
                )

        # 5. 页面声明见 get_pages()，框架自动注册导航

        logger.info("[app_manage] 插件安装完成")
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
        4. 删除公开 API 缓存键和清空 upload/app_manage/ 物理文件
        5. 注销运行时能力 — PluginManager 处理，返回 True
        """
        if not self.db:
            return False

        # 1. 删除数据表
        await self._run_sql_file("uninstall.sql")

        # 2. 运行时能力注销由 PluginManager 处理
        # 3. 权限、配置、导航和菜单由 PluginManager 统一清理。
        # 4. 清缓存 + 清空物理文件；运行时能力由 PluginManager 注销。
        # 事务由框架层 PluginManager 统一管理，插件不自行 commit
        await self._clear_public_cache()
        self._cleanup_upload_dir()

        logger.info("[app_manage] 插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def get_routers(self):
        """显式声明管理员端与 App 公开路由。"""
        from plugins.addon.app_manage.router import farmer_router, router
        return [router, farmer_router]

    def get_permissions(self):
        """返回插件权限树"""
        from plugins.addon.app_manage.auth import permission_tree
        return permission_tree

    def get_pages(self):
        """
        声明插件对外提供的页面（供导航管理"插件页面"选择器按归属分组展示）：
        - 后台页面：App管理页，对应 /admin/plugin/app_manage/app_manage
        - 本插件面向农户端App（原生客户端），不提供农户端 Web 页面
        """
        return [
            {
                "key": "plugin_app_manage",
                "title": "App管理",
                "path": MENU_PATH,
                "icon": "mobile",
                "nav_type": "admin",
                "template": "app_manage.html", "audience": "admin",
                "scripts": ["app_manage.js"],
                "permission": "app_manage:list",
                "api_base": "/api/admin/v1/app-manage",
            },
        ]

    def get_config_schema(self):
        """返回配置表单 Schema（供后台「插件配置」弹窗动态渲染，参照 mail_smtp 格式）"""
        return [
            {
                "key": "cache_ttl",
                "label": "公开接口缓存秒数",
                "type": "input",
                "required": True,
                "default": "300",
                "placeholder": "如 300",
                "help": "App端检查更新/广告/公告接口的服务端缓存时长",
            },
        ]

    def get_upload_policy_schema(self):
        """声明由系统上传设置统一管理的 APK 与广告图策略。"""
        from plugins.addon.app_manage.upload_policies import POLICIES
        return POLICIES

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

    async def _clear_public_cache(self) -> None:
        """删除公开API的三个缓存键，防止卸载后返回幽灵数据"""
        from plugins.addon.app_manage.services.cache_util import clear_all_cache
        await clear_all_cache()

    def _cleanup_upload_dir(self) -> None:
        """
        清空 upload/app_manage/ 目录内的物理文件（仅删文件，保留目录）。

        安全防线：resolve 后断言路径确实是项目根 upload/ 下的 app_manage 子目录，
        防止任何路径异常导致误删插件存储目录之外的文件。
        """
        upload_dir = UPLOAD_DIR.resolve()
        expected_parent = (BACKEND_ROOT / "upload").resolve()
        if upload_dir.parent != expected_parent or upload_dir.name != PLUGIN_NAME:
            logger.warning("[app_manage] upload 目录路径异常，跳过清理: %s", upload_dir)
            return
        if not upload_dir.is_dir():
            return
        for item in upload_dir.iterdir():
            if item.is_file():
                try:
                    item.unlink()
                except OSError:
                    logger.warning("[app_manage] 物理文件删除失败: %s", item)
