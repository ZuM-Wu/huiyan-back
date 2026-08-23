# -*- coding: utf-8 -*-
"""
Hello World 插件事件订阅处理器

演示监听系统事件和插件自定义事件。
"""
import logging

logger = logging.getLogger(__name__)


def on_system_startup(payload: dict):
    """系统启动事件处理函数。"""
    logger.info("[hello_world] system_startup: 示例插件已就绪")
    return {"plugin": "hello_world", "status": "ready"}


def on_message_created(payload: dict):
    """处理 hello_world.message_created 瞬时事件。"""
    logger.info("[hello_world] 新留言已创建: id=%s, title=%s", payload.get("message_id"), payload.get("title"))
    return {"handled": True, "message_id": payload.get("message_id")}
