# -*- coding: utf-8 -*-
"""由 EventRegistry 派生企业微信卡片动作及可外发字段。"""

from collections.abc import Mapping
from core.time_utils import china_now
from typing import Any, Dict, Iterator

from core.events import event_registry

COMMON_VARIABLES = {"event_time": "事件发生时间"}

# 渠道只维护“一个事件如何呈现为一个或多个动作”，业务标题、分类、版本及
# 可外发字段始终来自 EventRegistry。
_ACTION_EVENTS = {
    "user_registered": "farmer.registered",
    "cert_approved": "certification.reviewed",
    "cert_rejected": "certification.reviewed",
    "area_farmer_bindied": "area.farmer_bound",
    "plot_created": "plot.created",
    "batch_status_change": "batch.status_changed",
    "system_maintenance": "config.changed",
    "weather_alert": "weather.alert",
    "weather_alert_admin": "weather.alert",
    "task_failed": "task.failed",
    "password_reset": "notice.sending",
    "verify_code_login": "notice.sending",
    "verify_code_register": "notice.sending",
    "order_created": "notice.sending",
    "order_completed": "notice.sending",
}

_TITLE_SUFFIX = {
    "cert_approved": "（通过）", "cert_rejected": "（驳回）",
    "weather_alert_admin": "（管理员）", "password_reset": "（密码重置）",
    "verify_code_login": "（登录验证码）", "verify_code_register": "（注册验证码）",
    "order_created": "（订单创建）", "order_completed": "（订单完成）",
}

_ADAPTER_FIELDS = {
    "password_reset": {"expire"}, "verify_code_login": {"expire"},
    "verify_code_register": {"expire"},
    "order_created": {"order_id", "order_no", "amount", "status"},
    "order_completed": {"order_id", "order_no", "amount", "status"},
    "area_farmer_bindied": {"area_id", "area_name", "farmer_ids", "farmer_count"},
    "weather_alert": {"area_id", "area_name", "alert_title", "alert_level", "alert_text", "start_time", "end_time"},
    "weather_alert_admin": {"area_id", "area_name", "alert_title", "alert_level", "alert_text", "start_time", "end_time"},
    "task_failed": {"task_name", "task_desc", "task_type", "error_msg", "duration_ms", "attempt"},
}

_FIELD_TITLES = {
    "farmer_id": "农户 ID", "username": "登录账号", "nickname": "农户昵称",
    "real_name": "认证姓名", "review_remark": "审核备注", "area_id": "产区 ID",
    "area_name": "产区名称", "farmer_ids": "农户 ID 列表", "farmer_count": "绑定农户数",
    "plot_id": "地块 ID", "plot_name": "地块名称", "batch_id": "种植批次 ID",
    "batch_name": "批次名称", "old_status": "原状态", "new_status": "新状态",
    "expire": "验证码有效分钟数", "order_id": "订单 ID", "order_no": "订单编号",
    "amount": "订单金额", "status": "订单状态", "alert_title": "预警标题",
    "alert_level": "预警等级", "alert_text": "预警详情", "start_time": "生效时间",
    "end_time": "结束时间", "task_name": "任务标识", "task_desc": "任务名称",
    "task_type": "任务类型", "error_msg": "错误信息", "duration_ms": "执行耗时（毫秒）",
    "attempt": "执行次数",
}


def get_action_catalog() -> dict[str, dict[str, Any]]:
    """按当前事件注册表生成渠道动作目录。"""
    result = {}
    for action_key, event_name in _ACTION_EVENTS.items():
        event = event_registry.require_definition(event_name)
        fields = set(event.export_fields) | _ADAPTER_FIELDS.get(action_key, set())
        fields -= set(event.sensitive_fields)
        result[action_key] = {
            "event_name": event.name,
            "event_title": event.title,
            "event_version": event.version,
            "name": f"{event.title}{_TITLE_SUFFIX.get(action_key, '')}",
            "type": event.category,
            "export_fields": sorted(event.export_fields),
            "variables": {field: _FIELD_TITLES.get(field, field) for field in sorted(fields)},
        }
    return result


class _DynamicCatalog(Mapping):
    def __getitem__(self, key: str) -> dict[str, Any]:
        return get_action_catalog()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(_ACTION_EVENTS)

    def __len__(self) -> int:
        return len(_ACTION_EVENTS)


ACTION_CATALOG = _DynamicCatalog()


def available_variables(action_key: str) -> list[dict]:
    definition = ACTION_CATALOG[action_key]
    variables = {**COMMON_VARIABLES, **definition["variables"]}
    return [{"var": f"{{{key}}}", "desc": desc} for key, desc in variables.items()]


def sanitize_variables(action_key: str, variables: Dict[str, Any] | None) -> Dict[str, str]:
    definition = ACTION_CATALOG[action_key]
    allowed = set(COMMON_VARIABLES) | set(definition["variables"])
    source = variables or {}
    result = {key: str(source.get(key) if source.get(key) is not None else "") for key in allowed}
    result["event_time"] = str(source.get("event_time") or china_now().strftime("%Y-%m-%d %H:%M:%S"))
    return result


def build_card_fields(
    action_key: str, variables: Dict[str, Any] | None, limit: int = 6,
) -> list[dict[str, str]]:
    """按动作白名单生成卡片字段，避免敏感数据进入外发消息。"""
    if limit <= 0:
        return []
    sanitized = sanitize_variables(action_key, variables)
    definition = ACTION_CATALOG[action_key]
    ordered_keys = ["event_time", *definition["variables"].keys()]
    fields: list[dict[str, str]] = []
    for key in ordered_keys:
        value = str(sanitized.get(key) or "").strip()
        if not value:
            continue
        fields.append({
            "keyname": _FIELD_TITLES.get(key, definition["variables"].get(key, key))[:5],
            "value": value[:30],
        })
        if len(fields) >= limit:
            break
    return fields
