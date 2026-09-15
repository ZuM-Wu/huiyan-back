# -*- coding: utf-8 -*-
"""产区管理演示插件的公共常量、计算与序列化。"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from plugins.addon.production_area_management.models import (
    ProductionAreaManagementFeedback,
    ProductionAreaManagementLog,
    ProductionAreaManagementTask,
)

PLUGIN_NAME = "production_area_management"
DEMO_PLANT_DATE = date(2026, 8, 23)
DEMO_BATCH_NO = "DEMO-BLUEBERRY-2026-08-23"
LATEST_LOG_TITLE = "蓝莓进入始花期"
TASK_TITLE = "蓝莓花期授粉条件巡查与蜂箱布置"


class DemoError(Exception):
    """带有业务状态码的演示流程异常。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def json_load(raw: str | None, default: Any) -> Any:
    """解析插件 JSON 字段，异常时返回默认值。"""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def json_dump(value: Any) -> str:
    """以 UTF-8 语义序列化 JSON，保证中文可直接阅读。"""
    return json.dumps(value, ensure_ascii=False)


def _row_avg(row: dict[str, Any]) -> float | None:
    """优先使用定格日均温，缺失时按最高/最低温现算。"""
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
    """按项目积温定义计算活动积温和有效积温。"""
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
    total_days = (end - start).days + 1
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
    """将天气服务响应压缩为页面和日志需要的真实指标。"""
    if not result or result.get("status") != "success":
        return None
    realtime = result.get("realtime") or {}
    if realtime.get("temp") is None:
        return None
    return {
        "source": result.get("source", ""),
        "fetch_time": result.get("fetch_time", ""),
        "temp": realtime.get("temp"),
        "humidity": realtime.get("humidity"),
        "wind_dir": realtime.get("wind_dir", ""),
        "wind_scale": realtime.get("wind_scale", ""),
        "text": realtime.get("text", ""),
        "obs_time": realtime.get("obs_time", ""),
        "error_msg": result.get("error_msg", ""),
    }


def build_mcp_trace(
    area: dict[str, Any],
    weather: dict[str, Any],
    gdd: dict[str, Any],
) -> list[dict[str, Any]]:
    """构建页面展示并持久化的模拟 MCP 调用轨迹。"""
    return [
        {
            "seq": 1,
            "step": "读取最新产区日志",
            "tool": "plugin_log_latest",
            "status": "success",
            "duration_ms": 126,
            "summary": f"已读取“{LATEST_LOG_TITLE}”，进入花期任务推理。",
        },
        {
            "seq": 2,
            "step": "读取产区树",
            "tool": "core_agri_area_tree",
            "status": "success",
            "duration_ms": 184,
            "summary": f"产区 {area.get('name', '')} 状态正常，坐标已确认。",
        },
        {
            "seq": 3,
            "step": "读取天气快照",
            "tool": "core_agri_weather_snapshot",
            "status": "success",
            "duration_ms": 238,
            "summary": (
                f"{weather.get('text', '')}，{weather.get('temp', '-')}℃，"
                f"湿度 {weather.get('humidity', '-')}%。"
            ),
        },
        {
            "seq": 4,
            "step": "读取逐日天气并计算积温",
            "tool": "core_agri_weather_daily",
            "status": "success",
            "duration_ms": 356,
            "summary": (
                f"统计 {gdd.get('counted_days', 0)} 天，活动积温 "
                f"{gdd.get('active_gdd', 0)}℃，有效积温 {gdd.get('effective_gdd', 0)}℃。"
            ),
        },
        {
            "seq": 5,
            "step": "模型推理生成任务",
            "tool": "model_reasoning",
            "status": "success",
            "duration_ms": 612,
            "summary": f"生成 1 条待办任务：{TASK_TITLE}。",
        },
    ]


def serialize_log(row: ProductionAreaManagementLog) -> dict[str, Any]:
    """序列化日志表记录。"""
    return {
        "id": row.id,
        "area_id": row.area_id,
        "area_name": row.area_name or "",
        "title": row.title,
        "content": row.content,
        "stage": row.stage or "",
        "weather": json_load(row.weather_json, {}),
        "gdd": json_load(row.gdd_json, {}),
        "is_latest": bool(row.is_latest),
        "is_seed": bool(row.is_seed),
        "create_time": str(row.create_time or ""),
    }


def serialize_feedback(row: ProductionAreaManagementFeedback) -> dict[str, Any]:
    """序列化反馈记录。"""
    return {
        "id": row.id,
        "task_id": row.task_id,
        "admin_id": row.admin_id,
        "admin_name": row.admin_name or "",
        "result": row.result,
        "content": row.content,
        "metrics": json_load(row.metrics_json, {}),
        "create_time": str(row.create_time or ""),
    }


def serialize_task(
    row: ProductionAreaManagementTask,
    source_log_title: str = "",
    feedbacks: list[ProductionAreaManagementFeedback] | None = None,
) -> dict[str, Any]:
    """序列化任务及其反馈摘要。"""
    feedbacks = feedbacks or []
    latest_feedback = feedbacks[0] if feedbacks else None
    return {
        "id": row.id,
        "log_id": row.log_id,
        "source_log_title": source_log_title,
        "title": row.title,
        "description": row.description or "",
        "ai_summary": row.ai_summary or "",
        "priority": row.priority,
        "assignee": row.assignee or "",
        "status": row.status,
        "plan_time": str(row.plan_time or ""),
        "completed_time": str(row.completed_time or ""),
        "mcp_trace": json_load(row.mcp_trace, []),
        "is_seed": bool(row.is_seed),
        "feedback_count": len(feedbacks),
        "feedback_summary": latest_feedback.content[:80] if latest_feedback else "",
        "create_time": str(row.create_time or ""),
        "update_time": str(row.update_time or ""),
    }
