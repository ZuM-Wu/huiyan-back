# -*- coding: utf-8 -*-
"""
邮件通知管理员插件 — 可靠事件订阅处理器。

监听 task.failed；核心层已负责记录日志和系统内告警。
核心层已负责记录日志和系统内告警，本插件仅负责：
- 任务失败时按配置发送邮件/短信通知管理员
"""
import logging

logger = logging.getLogger(__name__)


async def on_task_failed(payload: dict) -> None:
    """可靠处理最终失败任务；异常上抛，由订阅投递任务独立重试。"""
    from plugins.addon.admin_notifier.services.alert_service import alert_service
    await alert_service.handle_failed_task(
        payload.get("definition", ""),
        payload.get("definition", ""),
        payload.get("error_msg", ""),
        payload.get("owner", "system"),
    )
