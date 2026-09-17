# -*- coding: utf-8 -*-
"""产区管理插件的事实计算、整改建议与序列化。"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from plugins.addon.production_area_management.models import (
    ProductionAreaManagementFeedback,
    ProductionAreaManagementLog,
    ProductionAreaManagementTask,
)

PLUGIN_NAME = "production_area_management"
WEATHER_SOURCE_TITLES = {
    "weather_qweather": "和风天气",
    "weather_amap": "高德气象",
}


class FactError(Exception):
    """事实台账业务异常，统一由插件路由映射为 HTTP 错误。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def json_load(raw: str | None, default: Any) -> Any:
    """解析 JSON 字段，异常时返回默认值。"""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def json_dump(value: Any) -> str:
    """以 UTF-8 语义序列化 JSON。"""
    return json.dumps(value, ensure_ascii=False)


def parse_date(value: Any) -> date | None:
    """兼容日期对象和 ISO 日期字符串。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def as_fact_date(value: Any) -> date | None:
    """将数据库创建时间或日期统一转成事实日期。"""
    return parse_date(value)


def weather_source_title(source: str | None) -> str:
    """将天气插件标识转换为管理员可读中文名称。"""
    source = str(source or "")
    return WEATHER_SOURCE_TITLES.get(source, source or "天气服务")


def _row_avg(row: dict[str, Any]) -> float | None:
    """优先使用日均温，缺失时按最高/最低温计算。"""
    avg = row.get("temp_avg")
    if avg is not None:
        return float(avg)
    high, low = row.get("temp_max"), row.get("temp_min")
    if high is None or low is None:
        return None
    return (float(high) + float(low)) / 2


def calculate_gdd(
    daily_rows: list[dict[str, Any]],
    base_temp: float,
    start: date,
    end: date,
) -> dict[str, Any]:
    """按项目定义累计活动积温和有效积温。"""
    active_gdd = 0.0
    effective_gdd = 0.0
    counted_days = 0
    for row in daily_rows:
        avg = _row_avg(row)
        if avg is None:
            continue
        counted_days += 1
        if avg >= base_temp:
            active_gdd += avg
            effective_gdd += avg - base_temp
    total_days = max((end - start).days + 1, 0)
    return {
        "start_date": str(start),
        "end_date": str(end),
        "base_temp": round(base_temp, 1),
        "active_gdd": round(active_gdd, 1),
        "effective_gdd": round(effective_gdd, 1),
        "counted_days": counted_days,
        "missing_days": max(total_days - counted_days, 0),
        "daily_count": len(daily_rows),
    }


def normalize_weather(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """将天气服务实况压缩为页面需要的真实指标。"""
    if not result or result.get("status") != "success":
        return None
    realtime = result.get("realtime") or {}
    if realtime.get("temp") is None:
        return None
    source = result.get("source", "")
    return {
        "source": source,
        "source_title": weather_source_title(source),
        "fetch_time": result.get("fetch_time", ""),
        "temp": realtime.get("temp"),
        "humidity": realtime.get("humidity"),
        "wind_dir": realtime.get("wind_dir", ""),
        "wind_scale": realtime.get("wind_scale", ""),
        "text": realtime.get("text", ""),
        "obs_time": realtime.get("obs_time", ""),
        "error_msg": result.get("error_msg", ""),
    }


def normalize_daily_weather(row: dict[str, Any] | None) -> dict[str, Any]:
    """将逐日天气行转换为事实日志字段，缺失值不补造。"""
    if not row:
        return {}
    fields = ("temp_max", "temp_min", "temp_avg", "humidity", "precip", "wind_scale", "text_day")
    return {key: row[key] for key in fields if row.get(key) not in (None, "")}


def build_recommendation(weather: dict[str, Any]) -> str:
    """只依据已入库天气事实生成可解释整改建议。"""
    if not weather:
        return "暂无逐日天气数据，建议补录当天气象事实后再进行现场判断。"
    suggestions: list[str] = []
    precip = weather.get("precip")
    humidity = weather.get("humidity")
    temp_avg = weather.get("temp_avg")
    wind_scale = str(weather.get("wind_scale") or "")
    if precip is not None and float(precip) >= 10:
        suggestions.append("复查排水沟和低洼地块积水")
    if humidity is not None and float(humidity) >= 90:
        suggestions.append("加强通风并关注高湿病害")
    if temp_avg is not None and float(temp_avg) >= 30:
        suggestions.append("核查灌溉与遮阴措施")
    try:
        if float(wind_scale) >= 4:
            suggestions.append("检查棚架、支撑和枝条固定")
    except (TypeError, ValueError):
        pass
    if not suggestions:
        return "暂无明显整改项，建议按日继续巡查并记录现场异常。"
    return "、".join(suggestions) + "。"


def build_fact_content(fact_date: date, weather: dict[str, Any], gdd: dict[str, Any]) -> str:
    """生成事实正文；没有逐日天气时保持正文为空。"""
    if not weather:
        return ""
    parts = [f"{fact_date}逐日天气记录"]
    if weather.get("text_day"):
        parts.append(f"天气{weather['text_day']}")
    if weather.get("temp_avg") is not None:
        parts.append(f"日均温{weather['temp_avg']}℃")
    if weather.get("temp_min") is not None and weather.get("temp_max") is not None:
        parts.append(f"最低{weather['temp_min']}℃、最高{weather['temp_max']}℃")
    if weather.get("humidity") is not None:
        parts.append(f"湿度{weather['humidity']}%")
    if weather.get("precip") is not None:
        parts.append(f"降水{weather['precip']}mm")
    if weather.get("wind_scale"):
        parts.append(f"风力{weather['wind_scale']}级")
    if gdd.get("effective_gdd") is not None:
        parts.append(f"截至当日有效积温{gdd['effective_gdd']}℃")
    return "，".join(parts) + "。"


def serialize_log(row: ProductionAreaManagementLog) -> dict[str, Any]:
    """序列化按日事实日志。"""
    fact_date = getattr(row, "fact_date", None)
    return {
        "id": row.id,
        "area_id": row.area_id,
        "area_name": row.area_name or "",
        "fact_date": str(fact_date or ""),
        "title": row.title,
        "content": row.content or "",
        "stage": row.stage or "",
        "weather": json_load(row.weather_json, {}),
        "gdd": json_load(row.gdd_json, {}),
        "recommendation": getattr(row, "recommendation", "") or "",
        "images": json_load(getattr(row, "images_json", "[]"), []),
        "is_latest": bool(row.is_latest),
        "is_seed": bool(row.is_seed),
        "create_time": str(row.create_time or ""),
    }


def serialize_feedback(row: ProductionAreaManagementFeedback) -> dict[str, Any]:
    """序列化现场反馈及图片地址。"""
    return {
        "id": row.id,
        "task_id": row.task_id,
        "admin_id": row.admin_id,
        "admin_name": row.admin_name or "",
        "result": row.result,
        "content": row.content,
        "images": json_load(getattr(row, "images_json", "[]"), []),
        "create_time": str(row.create_time or ""),
    }


def serialize_task(
    row: ProductionAreaManagementTask,
    source_log_title: str = "",
    feedbacks: list[ProductionAreaManagementFeedback] | None = None,
    source_log_date: str = "",
) -> dict[str, Any]:
    """序列化整改任务，不暴露历史 AI/MCP 字段。"""
    feedbacks = feedbacks or []
    latest_feedback = feedbacks[0] if feedbacks else None
    return {
        "id": row.id,
        "log_id": row.log_id,
        "source_log_title": source_log_title,
        "source_log_date": source_log_date,
        "title": row.title,
        "description": row.description or "",
        "priority": row.priority,
        "assignee": row.assignee or "",
        "status": row.status,
        "plan_time": str(row.plan_time or ""),
        "completed_time": str(row.completed_time or ""),
        "is_seed": bool(row.is_seed),
        "feedback_count": len(feedbacks),
        "feedback_summary": latest_feedback.content[:80] if latest_feedback else "",
        "create_time": str(row.create_time or ""),
        "update_time": str(row.update_time or ""),
    }
