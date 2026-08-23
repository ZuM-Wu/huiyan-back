# -*- coding: utf-8 -*-
"""
企业微信通知插件主类

功能概述:
- 对接企业微信群机器人 Webhook API，统一发送 text_notice 模板卡片
- 全局配置管理（Webhook、默认消息类型、超时、重试、全局开关）
 - 管理员通知动作管理（事件目录名称、分类及可外发变量）
- 发送日志记录与查询
- 业务事件与任务失败钩子联动（每个源事件只向管理员群投递一次）

生命周期遵循五步 install / uninstall 契约（参照 admin_notifier 模板插件）:
- install：建表（migrations/install.sql）+ 写默认配置（幂等）
- uninstall：删表 + 清配置 + 删菜单

注意：事务由框架层 PluginManager 统一管理，install/uninstall 内禁止 db.commit()。
"""
import logging
from pathlib import Path

from sqlalchemy import delete

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)

PLUGIN_NAME = "wecom_webhook"
# 后台菜单指向的插件页面地址
MENU_PATH = "/admin/plugin/wecom_webhook/wecom_webhook"


async def _handle_wecom_notice(context, task_data: dict) -> None:
    """任务队列处理器：执行企业微信通知并把失败交回队列重试。"""
    from plugins.addon.wecom_webhook.services.notice_service import wecom_notice_service
    result = await wecom_notice_service.send_queued_notice(task_data)
    if result.get("status") == "error":
        raise RuntimeError(result.get("msg", "企业微信通知发送失败"))


class Plugin(BasePlugin):
    """企业微信通知插件主类"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "企业微信通知"
        self.version = "5.0.0"
        self.description = "企业微信群机器人管理员通知渠道，使用数据驱动模板卡片和动作级路由"
        self.module = "addon"
        self._config_manager = ConfigManager()

    # ------------------------------------------------------------------
    # 安装 — 五步契约
    # ------------------------------------------------------------------
    async def install(self) -> bool:
        """
        安装插件（五步）:
        1. 创建数据表（执行 migrations/install.sql）
        2. 注册事件、任务等显式能力 — 由 PluginManager 统一处理
        3. 注册权限节点 — 由 PluginManager 从 get_permissions() 统一注册
        4. 写入默认配置（幂等：已存在则不覆盖）
        5. 菜单由导航管理手动添加（get_pages() 声明可用页面）
        """
        if not self.db:
            return False

        # 1. 创建数据表
        await self._run_sql_file("install.sql")
        await self._seed_actions()

        # 2. 显式能力由 PluginManager 负责注册
        # 3. 权限节点（get_permissions 返回，PluginManager 负责注册）

        # 4. 写入默认配置（幂等：已存在则不覆盖）
        for key, value in (self.config or {}).items():
            existing = await self._config_manager.get(key, self.db)
            if existing is None:
                await self._config_manager.set(
                    key, str(value), self.db, description="企业微信通知插件配置"
                )

        # 5. 菜单由导航管理手动添加（get_pages() 声明可用页面）

        logger.info("[wecom_webhook] 插件安装完成")
        return True

    # ------------------------------------------------------------------
    # 卸载 — 五步逆操作
    # ------------------------------------------------------------------
    async def uninstall(self) -> bool:
        """
        卸载插件（五步逆操作）:
        1. 删除数据表（执行 migrations/uninstall.sql）
        2. 按 owner 注销全部运行时能力 — PluginManager 处理
        3. 级联删除权限 — PluginManager 调用 unregister_plugin_permissions() 处理
        4. 清理配置（删除所有 wecom_webhook. 前缀配置）
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

        # 5. 删除后台菜单
        from core.db.menu import Menu
        await self.db.execute(delete(Menu).where(Menu.plugin == PLUGIN_NAME))

        logger.info("[wecom_webhook] 插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def get_routers(self):
        """返回插件管理员端 APIRouter"""
        from plugins.addon.wecom_webhook.router import router
        return [router]

    def get_event_subscriptions(self):
        """从统一事件目录显式订阅可靠业务事件。"""
        from core.events import EventSubscription
        from plugins.addon.wecom_webhook import hooks
        mapping = {
            "farmer.registered": hooks.on_farmer_registered,
            "certification.reviewed": hooks.on_cert_reviewed,
            "area.farmer_bound": hooks.on_area_farmer_bound,
            "plot.created": hooks.on_plot_created,
            "batch.status_changed": hooks.on_batch_status_changed,
            "config.changed": hooks.on_config_changed,
            "weather.alert": hooks.on_weather_alert,
            "task.failed": hooks.on_task_failed,
            "notice.sending": hooks.on_notice_sending,
        }
        return [EventSubscription(
            name, PLUGIN_NAME, handler, title=f"企业微信: {name}",
            failure_notifications=name != "task.failed",
        ) for name, handler in mapping.items()]

    def get_task_definitions(self):
        from services.task.definitions import TaskDefinition
        retry_times = int(self.config.get("wecom_webhook.retry_times", 3) or 3)
        return [TaskDefinition(
            "wecom_notice", "企业微信通知", PLUGIN_NAME, "wecom",
            _handle_wecom_notice, timeout_seconds=60,
            max_attempts=max(1, retry_times + 1), concurrency=2,
            failure_notifications=False,
        )]

    async def upgrade(self, old_version: str) -> bool:
        """升级后补齐插件动作目录，并清理废弃的默认消息类型配置。"""
        if not self.db:
            return False
        await self._seed_actions()
        from core.db.configuration import ConfigurationModel
        await self.db.execute(delete(ConfigurationModel).where(
            ConfigurationModel.key == f"{PLUGIN_NAME}.default_msgtype"
        ))
        logger.info("[wecom_webhook] 已从 %s 升级至 5.0.0", old_version)
        return True

    def get_permissions(self):
        """返回插件权限树"""
        from plugins.addon.wecom_webhook.auth import permission_tree
        return permission_tree

    def get_pages(self):
        """声明插件对外提供的页面（供导航管理"插件页面"选择器使用）"""
        return [
            {
                "key": "plugin_wecom_webhook",
                "title": "企业微信通知",
                "path": MENU_PATH,
                "icon": "notification",
                "nav_type": "admin",
                "template": "wecom_webhook.html", "audience": "admin",
                "permission": "wecom_webhook:config:view",
                "api_base": "/api/admin/v1/wecom-webhook",
            },
        ]

    def get_config_schema(self):
        """返回配置表单 Schema（供后台「插件配置」弹窗动态渲染）"""
        return [
            {
                "key": "webhook_url",
                "label": "Webhook 地址",
                "type": "input",
                "required": False,
                "placeholder": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
                "help": "管理员群机器人 Webhook 完整地址；动作可单独覆盖",
            },
            {
                "key": "enabled",
                "label": "全局启用",
                "type": "switch",
                "default": "0",
                "help": "关闭后所有管理员通知动作均不触发企业微信推送",
            },
            {
                "key": "timeout_seconds",
                "label": "请求超时",
                "type": "number",
                "default": "10",
                "help": "企业微信请求超时秒数，范围 1-60",
            },
            {
                "key": "retry_times",
                "label": "失败重试",
                "type": "number",
                "default": "3",
                "help": "持久化任务最大重试次数，范围 0-5",
            },
        ]

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    async def _seed_actions(self) -> None:
        """幂等写入插件动作元数据，保留已有开关和动作级 Webhook。"""
        from sqlalchemy import select

        from plugins.addon.wecom_webhook.action_catalog import ACTION_CATALOG
        from plugins.addon.wecom_webhook.models import WecomWebhookAction

        rows = (await self.db.execute(select(WecomWebhookAction))).scalars().all()
        existing = {row.action_key: row for row in rows}
        for action_key, definition in ACTION_CATALOG.items():
            row = existing.get(action_key)
            if row is None:
                self.db.add(WecomWebhookAction(
                    action_key=action_key,
                    action_name=definition["name"],
                    action_type=definition["type"],
                    enabled=0,
                    webhook_url="",
                ))
                continue
            row.action_name = definition["name"]
            row.action_type = definition["type"]

    async def _run_sql_file(self, filename: str) -> None:
        """读取 migrations/ 下的 SQL 文件并执行"""
        sql_path = Path(__file__).parent / "migrations" / filename
        if not sql_path.exists():
            return
        with open(sql_path, "r", encoding="utf-8") as f:
            sql = f.read()
        if sql.strip():
            await self._exec_sql(sql)
