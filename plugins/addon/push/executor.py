# -*- coding: utf-8 -*-
"""推送中心执行器：目标快照、渠道投递、统计汇总。"""
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select

from core.db.base import async_session_factory
from core.inbox_trigger import inbox_trigger
from core.notice_sender import notice_sender
from plugins.addon.push.models import PushCenterDeliveryLog, PushCenterTask
from plugins.addon.push.target_resolver import resolve_targets

logger = logging.getLogger(__name__)


async def execute_task(task_id: int) -> dict[str, int]:
    """执行任务并分别记录三个渠道的投递结果。"""
    async with async_session_factory() as db:
        result = await db.execute(select(PushCenterTask).where(PushCenterTask.id == task_id))
        task = result.scalar_one_or_none()
        if not task or task.status != "Wait":
            return {"success": 0, "fail": 0, "total": 0}
        task.status = "Exec"
        task.last_exec_time = datetime.now()
        await db.commit()
        task_data = {
            "content": task.content or "",
            "subject": task.subject or "",
            "channels": task.channels or {},
            "target_rule": task.target_rule or {},
            "schedule_rule": task.schedule_rule or {},
        }

    farmers, total = await resolve_targets(task_data["target_rule"])
    channels = task_data["channels"]
    success_count = 0
    fail_count = 0
    delivery_count = 0

    for farmer in farmers:
        for channel in _enabled_channels(channels):
            delivery_count += 1
            try:
                sent, notification_log_id = await _send_channel(task_id, channel, farmer, task_data)
                await _write_delivery(task_id, farmer, channel, "Success" if sent else "Failed", "", notification_log_id)
                if sent:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as exc:
                logger.exception("[推送中心] 渠道投递失败 task=%s channel=%s", task_id, channel)
                await _write_delivery(task_id, farmer, channel, "Failed", str(exc), None)
                fail_count += 1

    async with async_session_factory() as db:
        result = await db.execute(select(PushCenterTask).where(PushCenterTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.send_num = (task.send_num or 0) + delivery_count
            task.success_num = (task.success_num or 0) + success_count
            task.fail_num = (task.fail_num or 0) + fail_count
            cycle = (task.schedule_rule or {}).get("cycle", "onetime")
            end_time = (task.schedule_rule or {}).get("end_time")
            if end_time and isinstance(end_time, str):
                try:
                    end_time = datetime.fromisoformat(end_time)
                except ValueError:
                    end_time = None
            if end_time and datetime.now() > end_time:
                task.status = "Expired"
            else:
                task.status = "Finish" if cycle == "onetime" else "Wait"
            await db.commit()

    return {"success": success_count, "fail": fail_count, "total": total}


def _enabled_channels(channels: dict) -> list[str]:
    return [channel for channel in ("inbox", "sms", "email") if channels.get(channel)]


async def _send_channel(task_id: int, channel: str, farmer: dict, task_data: dict) -> tuple[bool, int | None]:
    if channel == "inbox":
        message_id = await inbox_trigger.create(
            receiver_id=farmer["id"], title="系统推送通知", content=task_data["content"],
            receiver_type="farmer", extra={"push_center_task_id": task_id},
        )
        return message_id > 0, None
    if channel == "sms":
        if not farmer.get("phone"):
            return False, None
        result = await notice_sender.send_content(
            recipient=farmer["phone"], channel="sms", content=task_data["content"],
            # 未指定接口时交给通知 Worker 从已启用的短信插件中自动选择。
            interface=task_data["channels"].get("sms_interface") or "",
            template_id=str(task_data["channels"].get("sms_template_id") or ""),
            local_template_id=task_data["channels"].get("sms_template_id") or 0,
            action_key="push_center", recipient_id=farmer["id"],
            description=f"推送中心短信: {task_id}", extra={"push_center_task_id": task_id},
        )
        return True, result.get("log_id")
    if not farmer.get("email"):
        return False, None
    result = await notice_sender.send_content(
        recipient=farmer["email"], channel="email", content=task_data["content"],
        subject=task_data["subject"] or "系统推送通知",
        local_template_id=task_data["channels"].get("email_template_id") or 0,
        action_key="push_center", recipient_id=farmer["id"],
        description=f"推送中心邮件: {task_id}", extra={"push_center_task_id": task_id},
    )
    return True, result.get("log_id")


async def _write_delivery(task_id: int, farmer: dict, channel: str, status: str, reason: str, log_id: int | None) -> None:
    try:
        async with async_session_factory() as db:
            db.add(PushCenterDeliveryLog(
                task_id=task_id, farmer_id=farmer["id"], username=farmer.get("username", ""),
                channel=channel, status=status, reason=reason or None, notification_log_id=log_id,
            ))
            await db.commit()
    except Exception as exc:
        logger.exception("[推送中心] 写入投递日志失败: %s", exc)


async def send_preview(task_id: int, email: str, phone: str) -> list[dict[str, Any]]:
    async with async_session_factory() as db:
        result = await db.execute(select(PushCenterTask).where(PushCenterTask.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError("推送任务不存在")
        data = {"content": task.content or "", "subject": task.subject or "", "channels": task.channels or {}}
    return await _send_direct(data, email, phone, task_id)


async def send_direct_preview(data) -> list[dict[str, Any]]:
    channels = {
        "sms": bool(data.test_phone), "email": bool(data.test_email),
        "sms_template_id": data.sms_template_id, "email_template_id": data.email_template_id,
    }
    payload = {"content": data.content, "subject": data.subject, "channels": channels}
    return await _send_direct(payload, data.test_email, data.test_phone, 0)


async def _send_direct(data: dict, email: str, phone: str, task_id: int) -> list[dict[str, Any]]:
    farmer = {"id": 0, "username": "预览", "email": email, "phone": phone}
    results = []
    for channel, value in (("sms", phone), ("email", email)):
        if not value:
            continue
        try:
            sent, _ = await _send_channel(task_id, channel, farmer, data)
            results.append({"channel": channel, "target": value, "ok": sent})
        except Exception as exc:
            results.append({"channel": channel, "target": value, "ok": False, "error": str(exc)})
    return results
