"""主题与插件平台公共门面。

本包只做稳定协议适配，底层仍复用项目现有任务、事件、日志、主题和插件服务。
业务模块不得通过本包取得底层 ORM、缓存客户端或磁盘绝对路径。
"""

from core.platform.audit import audit_log
from core.platform.context import bind_context, clear_context, current_context
from core.platform.event import publish_durable, publish_lifecycle_event, publish_transient
from core.platform.health import platform_health
from core.platform.lock import platform_lock
from core.platform.menu import menu_platform
from core.platform.plugin import plugin_platform
from core.platform.resource import asset_registry, resource_registry
from core.platform.task import (
    cancel_owner,
    drain_owner,
    ensure_platform_tasks,
    pause_owner,
    query_logs,
    query_queue,
    query_task,
    resume_owner,
    retry_task,
    submit_platform_task,
)
from core.platform.url import build_admin_path, build_plugin_page, get_admin_path
from core.platform.theme import theme_platform
from core.platform.route import route_platform

__all__ = [
    "asset_registry",
    "audit_log",
    "bind_context",
    "build_admin_path",
    "build_plugin_page",
    "cancel_owner",
    "clear_context",
    "current_context",
    "drain_owner",
    "ensure_platform_tasks",
    "get_admin_path",
    "menu_platform",
    "pause_owner",
    "platform_health",
    "platform_lock",
    "plugin_platform",
    "publish_durable",
    "publish_lifecycle_event",
    "publish_transient",
    "query_logs",
    "query_queue",
    "query_task",
    "resource_registry",
    "resume_owner",
    "retry_task",
    "route_platform",
    "submit_platform_task",
    "theme_platform",
]
