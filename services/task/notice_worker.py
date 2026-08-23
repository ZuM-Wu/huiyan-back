# -*- coding: utf-8 -*-
"""
通知异步发送 Worker
在后台任务中加载插件、执行第三方发送并回写通知日志状态
"""
import asyncio
import importlib
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, update

from core.db.base import async_session_factory
from core.db.notice import NoticeLog
from core.db.plugin import PluginModel
from core.config_manager import ConfigManager

logger = logging.getLogger(__name__)


async def resolve_plugin(module: str, interface: str) -> Tuple[Optional[Any], Dict[str, Any], str]:
    """
    根据渠道模块与接口标识解析已启用的插件实例及其配置

    Args:
        module: 插件模块 sms/mail
        interface: 接口标识（如 aliyun/sms_aliyun/smtp/mail_smtp）；
            为空时自动选择该模块下第一个已启用的插件（邮件模板不绑定插件，
            发送时由此处自动路由到当前启用的发送接口）

    Returns:
        (插件实例, 插件配置 dict, 错误信息)；解析失败时实例为 None
    """
    async with async_session_factory() as db:
        if interface:
            # 兼容简写：aliyun -> sms_aliyun / smtp -> mail_smtp
            candidates = [interface]
            prefixed = f"{module}_{interface}"
            if prefixed not in candidates:
                candidates.append(prefixed)
            result = await db.execute(
                select(PluginModel).where(
                    PluginModel.name.in_(candidates),
                    PluginModel.module == module,
                    PluginModel.status == 1,
                )
            )
        else:
            # 未指定接口：自动回落到第一个已启用的同模块插件
            result = await db.execute(
                select(PluginModel).where(
                    PluginModel.module == module,
                    PluginModel.status == 1,
                )
            )
        record = result.scalars().first()
        if not record:
            if interface:
                return None, {}, f"接口插件未安装或未启用：{interface}"
            return None, {}, f"无已启用的 {module} 渠道插件，请先在接口列表中启用"

        # 读取插件在 hy_configuration 中的 KV 配置
        config = await ConfigManager().get_plugin_config(record.name, db)

    try:
        plugin_module = importlib.import_module(f"plugins.{module}.{record.name}.plugin")
        plugin_cls = getattr(plugin_module, "Plugin", None)
        if not plugin_cls:
            return None, {}, f"插件 {record.name} 缺少 Plugin 类"
        return plugin_cls(None, config), config, ""
    except Exception as e:
        logger.error(f"[通知Worker] 插件加载失败: {record.name}, {e}")
        return None, {}, f"插件加载失败：{e}"


async def _update_log(log_id: int, success: bool, message: str,
                      message_id: str = "") -> None:
    """回写通知日志的发送结果"""
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(NoticeLog).where(NoticeLog.id == log_id))
            log = result.scalar_one_or_none()
            if not log:
                return
            extra = dict(log.extra or {})
            extra.pop("pending", None)
            if message_id:
                extra["message_id"] = str(message_id)
            await db.execute(
                update(NoticeLog).where(NoticeLog.id == log_id).values(
                    status=1 if success else 0,
                    error_msg="" if success else message,
                    send_time=datetime.now(),
                    extra=extra,
                )
            )
            await db.commit()
    except Exception as e:
        logger.error(f"[通知Worker] 日志回写失败: log_id={log_id}, {e}")


async def send_notice_task(log_id: int, payload: Dict[str, Any]) -> None:
    """
    异步发送任务入口（由 NoticeSender 通过 asyncio.create_task 投递）

    payload 字段:
        channel: sms/email
        interface: 接口标识
        sms_type: 0=国内 1=国际（仅短信）
        recipient: 接收者手机/邮箱
        content: 已完成变量替换的内容
        subject: 邮件主题（仅邮件）
        template_id: 第三方模板 ID（仅短信）
        variables: 原始变量 dict
    """
    channel = payload.get("channel", "sms")
    module = "sms" if channel == "sms" else "mail"

    plugin, config, err = await resolve_plugin(module, payload.get("interface", ""))
    if not plugin:
        await _update_log(log_id, False, err)
        raise RuntimeError(err)

    try:
        if channel == "sms":
            params = {
                "mobile": payload.get("recipient", ""),
                "content": payload.get("content", ""),
                "template_id": payload.get("template_id", ""),
                "template_param": payload.get("variables", {}),
                "config": config,
            }
            if int(payload.get("sms_type", 0)) == 1:
                result = await plugin.send_global_sms(params)
            else:
                result = await plugin.send_cn_sms(params)
        else:
            params = {
                "to": payload.get("recipient", ""),
                "subject": payload.get("subject", ""),
                "content": payload.get("content", ""),
                "attachments": payload.get("attachments", []),
                "config": config,
            }
            result = await plugin.send_email(params)
    except Exception as e:
        logger.error(f"[通知Worker] 插件调用异常: log_id={log_id}, {e}")
        await _update_log(log_id, False, f"插件调用异常：{e}")
        raise

    result = result or {}
    success = result.get("status") == "success"
    message = result.get("msg", "") or result.get("message", "")
    message_id = result.get("message_id", "") or result.get("msg_id", "")
    await _update_log(log_id, success, message, message_id)
    logger.info(f"[通知Worker] 发送完成: log_id={log_id}, success={success}, msg={message}")
    if not success:
        raise RuntimeError(message or "第三方通知发送失败")


# ---------------------------------------------------------------------------
# 短信模板第三方平台同步操作（供 api 层调用，避免业务层直连内部解析符号）
# ---------------------------------------------------------------------------


async def sms_template_submit(interface: str, title: str, content: str, sms_type: int) -> Dict[str, Any]:
    """提交短信模板到第三方平台审核（返回平台结果 dict，失败时 status=failed）"""
    plugin, config, err = await resolve_plugin("sms", interface)
    if not plugin:
        return {"status": "failed", "msg": err}
    params = {"title": title, "content": content, "config": config}
    try:
        if sms_type == 0:
            return await plugin.create_cn_template(params)
        return await plugin.create_global_template(params)
    except Exception as e:
        return {"status": "failed", "msg": f"插件调用异常 {e}"}


async def sms_template_delete(interface: str, template_id: str, sms_type: int = 0) -> None:
    """删除第三方平台短信模板（失败仅告警，不影响本地删除）"""
    plugin, config, err = await resolve_plugin("sms", interface)
    if not plugin:
        logger.warning(f"[通知Worker] 删除第三方模板失败，跳过: {err}")
        return
    try:
        params = {"template_id": template_id, "config": config, "sms_type": sms_type}
        if sms_type == 1:
            await plugin.delete_global_template(params)
        else:
            await plugin.delete_cn_template(params)
    except Exception as e:
        logger.warning(f"[通知Worker] 删除第三方模板失败: {e}")


async def sms_template_query_status(
    interface: str, template_id: str, sms_type: int = 0,
) -> Dict[str, Any]:
    """查询第三方平台短信模板审核状态（返回平台结果 dict，失败时 status=failed）"""
    plugin, config, err = await resolve_plugin("sms", interface)
    if not plugin:
        return {"status": "failed", "msg": err}
    try:
        return await plugin.get_template_status({
            "template_id": template_id, "config": config, "sms_type": sms_type,
        })
    except Exception as e:
        return {"status": "failed", "msg": f"插件调用异常 {e}"}


async def send_test_notice(payload: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
    """
    测试发送：写 pending 日志 → 投递任务队列 → 轮询回写结果（最多 timeout 秒）

    测试发送需即时返回第三方发送结果，而 notice_sender.send 为异步投递且依赖动作配置，
    故此处经 submit_task 队列投递（send_notice_task 回写日志）后轮询日志获取结果；
    超时返回"结果确认超时"（发送仍可能成功，可在发送日志中查看）。

    Returns:
        {"success": bool, "msg": str, "log_id": int}
    """
    from services.task.queue_worker import submit_task
    content = payload.get("content", "")
    log = NoticeLog(
        action_key="test_send",
        recipient=payload.get("recipient", ""),
        channel=payload.get("channel", "sms"),
        template_id=payload.get("local_template_id", 0),
        content=(content[:100] + "...") if len(content) > 100 else content,
        status=0,
        extra={
            "pending": True,
            "test": True,
            "original_content": content,
            "subject": payload.get("subject", ""),
        },
        create_time=datetime.now(),
    )
    async with async_session_factory() as db:
        db.add(log)
        await db.commit()
        await db.refresh(log)
    log_id = log.id

    try:
        await submit_task("notice", {"log_id": log_id, "payload": payload},
                          description="测试发送")
    except Exception as e:
        logger.error(f"[通知Worker] 测试发送投递失败: {e}")
        await _update_log(log_id, False, f"投递失败：{e}")
        return {"success": False, "msg": f"投递失败：{e}", "log_id": log_id}

    # 轮询发送结果（worker 轮询间隔约 3s，等待至多 timeout 秒）
    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(0.5)
        async with async_session_factory() as db:
            row = (await db.execute(
                select(NoticeLog).where(NoticeLog.id == log_id)
            )).scalar_one_or_none()
        if row and not (row.extra or {}).get("pending"):
            return {"success": row.status == 1, "msg": row.error_msg or "发送成功", "log_id": log_id}
    return {"success": False, "msg": "发送结果确认超时，请稍后在发送日志中查看", "log_id": log_id}
