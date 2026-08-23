# -*- coding: utf-8 -*-
"""
核心通知钩子监听者

启动时由 lifespan 调用 register_notice_hooks() 注册核心通知监听者，
监听业务钩子事件（farmer_registered/cert_reviewed/area_farmer_bound/
plot_created/batch_status_changed/config_changed），触发对应通知动作。

定位：核心模块（plugin_name="system"），不被插件卸载清理。
依赖：仅调用 notice_dispatcher（编排服务），不直连通知插件。
"""
import logging

from core.events import EventSubscription, event_registry
from core.notice_dispatcher import notice_dispatcher

logger = logging.getLogger(__name__)


async def _on_farmer_registered(payload: dict):
    """农户注册 → broadcast_admins 通知管理员（user_registered 动作）"""
    username = payload.get("username", "")
    nickname = payload.get("nickname", "")
    variables = {"username": username, "nickname": nickname or username}
    await notice_dispatcher.broadcast_admins(
        "user_registered", variables, "新用户注册",
        f"新用户 {nickname or username} 已完成注册")


async def _on_cert_reviewed(payload: dict):
    """认证审批 → notify_single_farmer 通知农户本人（cert_approved/cert_rejected）"""
    farmer_id = payload.get("farmer_id", 0)
    status = payload.get("status", 0)
    real_name = payload.get("real_name", "")
    review_remark = payload.get("review_remark", "")
    action_key = "cert_approved" if status == 1 else "cert_rejected"
    passed = status == 1
    variables = {"real_name": real_name or "", "review_remark": review_remark or ""}
    await notice_dispatcher.notify_single_farmer(
        action_key, farmer_id, variables,
        f"实名认证{'通过' if passed else '未通过'}",
        f"您的实名认证已{'通过审核' if passed else '未通过审核'}")


async def _on_area_farmer_bound(payload: dict):
    """产区绑定农户 → 逐新绑定农户 notify_single_farmer（area_farmer_bindied 动作）

    仅处理 action=bind 且 farmer_ids 非空（解绑不发通知）。
    """
    farmer_ids = payload.get("farmer_ids") or []
    if payload.get("action") != "bind" or not farmer_ids:
        return
    area_name = payload.get("area_name", "")
    variables = {"area_name": area_name}
    for fid in farmer_ids:
        await notice_dispatcher.notify_single_farmer(
            "area_farmer_bindied", fid, variables,
            "产区绑定通知", f"管理员已将您绑定到产区「{area_name}」")


async def _on_plot_created(payload: dict):
    """新增地块 → broadcast_farmers_by_area 通知产区绑定农户（plot_created 动作）"""
    area_id = payload.get("area_id", 0)
    plot_name = payload.get("plot_name", "")
    variables = {"plot_name": plot_name}
    await notice_dispatcher.broadcast_farmers_by_area(
        "plot_created", area_id, variables, "新增地块通知",
        f"产区已新增地块「{plot_name}」")


async def _on_batch_status_changed(payload: dict):
    """批次状态变更 → notify_single_farmer 通知批次归属农户（batch_status_change 动作）"""
    farmer_id = payload.get("farmer_id", 0)
    batch_name = payload.get("batch_name", "")
    variables = {"batch_name": batch_name, "old_status": payload.get("old_status", ""), "new_status": payload.get("new_status", "")}
    await notice_dispatcher.notify_single_farmer(
        "batch_status_change", farmer_id, variables,
        "批次状态变更", f"种植批次「{batch_name}」状态已更新")


async def _on_weather_alert(payload: dict):
    """气象预警 → broadcast_farmers_by_area + broadcast_admins

    天气服务通过钩子事件解耦后，由本监听者负责构造通知变量并调用通知编排服务。
    """
    area_id = payload.get("area_id", 0)
    area_name = payload.get("area_name", "")
    alert_fields = payload.get("alert_fields") or {}
    alert_vars = {
        "area_name": area_name,
        "alert_title": alert_fields.get("title", ""),
        "alert_level": alert_fields.get("level", ""),
        "alert_text": alert_fields.get("text", ""),
        "start_time": str(alert_fields.get("start_time", "") or ""),
        "end_time": str(alert_fields.get("end_time", "") or ""),
    }
    inbox_title = f"【紧急】{area_name}气象预警：{alert_vars['alert_title']}"
    inbox_content = alert_fields.get("text", "")
    await notice_dispatcher.broadcast_farmers_by_area(
        "weather_alert", area_id, alert_vars, inbox_title, inbox_content)
    await notice_dispatcher.broadcast_admins(
        "weather_alert_admin", alert_vars, inbox_title, inbox_content)


async def _on_config_changed(payload: dict):
    """配置变更 → 仅 site_maintenance 值为 1 时触发维护通知（system_maintenance 动作）

    关闭维护（值 0）不发"恢复"通知，避免噪音。
    """
    if payload.get("key") == "site_maintenance" and str(payload.get("value")) == "1":
        variables: dict[str, object] = {}
        await notice_dispatcher.broadcast_all_farmers(
            "system_maintenance", variables, "系统维护通知",
            "系统将于指定时间进行维护升级，届时可能暂时无法访问")


def register_notice_hooks():
    """启动时注册核心通知事件订阅者。"""
    owner = "core.notice"
    event_registry.unregister_owner(owner)
    for name, title, handler in (
        ("farmer.registered", "注册通知编排", _on_farmer_registered),
        ("certification.reviewed", "认证通知编排", _on_cert_reviewed),
        ("area.farmer_bound", "产区绑定通知编排", _on_area_farmer_bound),
        ("plot.created", "地块通知编排", _on_plot_created),
        ("batch.status_changed", "批次通知编排", _on_batch_status_changed),
        ("weather.alert", "天气通知编排", _on_weather_alert),
        ("config.changed", "配置通知编排", _on_config_changed),
    ):
        event_registry.register_subscription(EventSubscription(name, owner, handler, title))
    logger.info("[通知事件] 核心通知订阅已注册（7 个事件）")
