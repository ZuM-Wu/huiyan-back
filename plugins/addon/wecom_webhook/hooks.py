# -*- coding: utf-8 -*-
"""企业微信对统一事件目录的显式订阅处理器。"""

import logging

logger = logging.getLogger(__name__)

_NOTICE_FALLBACK_ACTIONS = {
    "password_reset", "verify_code_login", "verify_code_register",
    "order_created", "order_completed",
}


async def _enqueue(action_key: str, variables: dict | None = None) -> None:
    """把业务事件转换为插件任务；失败必须上抛给可靠投递任务。"""
    from plugins.addon.wecom_webhook.services.notice_service import wecom_notice_service

    result = await wecom_notice_service.enqueue_action(action_key, variables or {})
    if result.get("status") == "error":
        raise RuntimeError(result.get("msg", "企业微信动作入队失败"))
    logger.info("[wecom_webhook] 动作 %s 处理结果: %s", action_key, result.get("status"))


async def on_farmer_registered(payload: dict) -> None:
    await _enqueue("user_registered", {
        "farmer_id": payload.get("farmer_id", 0),
        "username": payload.get("username", ""),
        "nickname": payload.get("nickname") or payload.get("username", ""),
    })


async def on_cert_reviewed(payload: dict) -> None:
    await _enqueue("cert_approved" if payload.get("status") == 1 else "cert_rejected", {
        "farmer_id": payload.get("farmer_id", 0),
        "real_name": payload.get("real_name", ""),
        "review_remark": payload.get("review_remark", ""),
    })


async def on_area_farmer_bound(payload: dict) -> None:
    farmer_ids = payload.get("farmer_ids") or []
    if payload.get("action") != "bind" or not farmer_ids:
        return
    await _enqueue("area_farmer_bindied", {
        "area_id": payload.get("area_id", 0), "area_name": payload.get("area_name", ""),
        "farmer_count": len(farmer_ids),
        "farmer_ids": ",".join(str(item) for item in farmer_ids),
    })


async def on_plot_created(payload: dict) -> None:
    await _enqueue("plot_created", payload)


async def on_batch_status_changed(payload: dict) -> None:
    await _enqueue("batch_status_change", payload)


async def on_config_changed(payload: dict) -> None:
    if payload.get("key") == "site_maintenance" and str(payload.get("value")) == "1":
        await _enqueue("system_maintenance")


async def on_weather_alert(payload: dict) -> None:
    fields = payload.get("alert_fields") or {}
    variables = {
        "area_id": payload.get("area_id", 0), "area_name": payload.get("area_name", ""),
        "alert_title": fields.get("title", ""), "alert_level": fields.get("level", ""),
        "alert_text": fields.get("text", ""), "start_time": fields.get("start_time", ""),
        "end_time": fields.get("end_time", ""),
    }
    await _enqueue("weather_alert", variables)
    await _enqueue("weather_alert_admin", variables)


async def on_task_failed(payload: dict) -> None:
    await _enqueue("task_failed", {
        "task_name": payload.get("definition", ""),
        "task_desc": payload.get("definition", ""),
        "task_type": payload.get("owner", "system"),
        "error_msg": payload.get("error_msg", ""),
        "attempt": payload.get("attempt", 0),
    })


async def on_notice_sending(payload: dict) -> None:
    action_key = payload.get("action_key", "")
    if action_key in _NOTICE_FALLBACK_ACTIONS:
        await _enqueue(action_key, payload.get("variables") or {})
