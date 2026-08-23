"""业务事件发布门面；新代码应优先直接使用 core.events.event_bus。"""

from core.db.base import async_session_factory
from core.events import event_bus


async def _durable(name: str, payload: dict) -> int:
    """为没有现成业务会话的旧调用点提供独立可靠事务。"""
    async with async_session_factory() as db:
        event_id = await event_bus.publish_durable(name, payload, db)
        await db.commit()
        return event_id


async def emit_admin_login(user_id, ip):
    return await event_bus.publish_transient("admin.login", {"user_id": user_id, "ip": ip or ""})


async def emit_farmer_login(user_id, ip):
    return await event_bus.publish_transient("farmer.login", {"user_id": user_id, "ip": ip or ""})


async def emit_config_changed(key, value):
    return await _durable("config.changed", {"key": key, "value": value})


async def emit_plugin_installed(plugin_name):
    return await event_bus.publish_transient("plugin.installed", {"plugin_name": plugin_name})


async def emit_plugin_uninstalled(plugin_name):
    return await event_bus.publish_transient("plugin.uninstalled", {"plugin_name": plugin_name})


async def emit_farmer_registered(farmer_id, username, phone, email, nickname):
    return await _durable("farmer.registered", {
        "farmer_id": farmer_id, "username": username, "phone": phone or "",
        "email": email or "", "nickname": nickname or "",
    })


async def emit_cert_reviewed(farmer_id, status, real_name, review_remark):
    return await _durable("certification.reviewed", {
        "farmer_id": farmer_id, "status": status, "real_name": real_name or "",
        "review_remark": review_remark or "",
    })


async def emit_area_farmer_bound(area_id, area_name, farmer_ids, action="bind"):
    return await _durable("area.farmer_bound", {
        "area_id": area_id, "area_name": area_name,
        "farmer_ids": farmer_ids, "action": action,
    })


async def emit_plot_created(plot_id, area_id, plot_name):
    return await _durable("plot.created", {
        "plot_id": plot_id, "area_id": area_id, "plot_name": plot_name,
    })


async def emit_batch_status_changed(batch_id, farmer_id, old_status, new_status, batch_name):
    return await _durable("batch.status_changed", {
        "batch_id": batch_id, "farmer_id": farmer_id,
        "old_status": str(old_status), "new_status": str(new_status),
        "batch_name": batch_name,
    })


async def emit_weather_alert(area_id, area_name, alert_fields):
    return await _durable("weather.alert", {
        "area_id": area_id, "area_name": area_name, "alert_fields": alert_fields,
    })
