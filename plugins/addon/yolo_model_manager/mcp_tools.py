# -*- coding: utf-8 -*-
"""智能识别记录与地块硬件历史的只读 MCP 分析上下文。"""

from datetime import datetime
from typing import Annotated

from fastmcp.exceptions import ToolError
from pydantic import Field

from core.db.base import async_session_factory
from core.hardware_history_service import read_nearest_plot_hardware_history
from core.time_utils import CHINA_TIMEZONE
from plugins.addon.yolo_model_manager.services.recognition_service import (
    RecognitionService,
)


def _recognized_time(value) -> datetime:
    """解析记录服务输出的中国本地识别时间。"""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except ValueError as exc:
            raise ToolError("识别记录时间无效，无法关联硬件历史") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CHINA_TIMEZONE)
    return parsed.astimezone(CHINA_TIMEZONE)


def _safe_detections(raw_items) -> list[dict]:
    """只返回识别目标协议字段，避免历史扩展字段意外进入 MCP。"""
    result = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        item = {
            "label": str(raw.get("label") or ""),
            "confidence": raw.get("confidence"),
            "bbox": raw.get("bbox") if isinstance(raw.get("bbox"), list) else None,
        }
        result.append(item)
    return result


def _safe_image_url(value) -> str:
    """识别记录只允许返回 URL，不把内嵌图片数据送入模型上下文。"""
    normalized = str(value or "").strip()
    if (
        normalized.lower().startswith("data:")
        or ";base64," in normalized[:256].lower()
    ):
        return ""
    return normalized


def _record_context(record: dict, recognized_at: datetime) -> dict:
    """组装不含管理员、任务和记录创建信息的识别事实。"""
    return {
        "id": int(record["id"]),
        "area": {
            "id": int(record["area_id"]),
            "name": str(record.get("area_name") or ""),
        },
        "plot": {
            "id": int(record["plot_id"]),
            "name": str(record.get("plot_name") or ""),
        },
        "model": {
            "id": int(record["model_id"]),
            "name": str(record.get("model_name") or ""),
            "version": str(record.get("model_version") or ""),
        },
        "recognized_at": recognized_at.isoformat(timespec="seconds"),
        "detections": _safe_detections(record.get("detections")),
        "detection_count": int(record.get("detection_count") or 0),
        "max_confidence": record.get("max_confidence"),
        "source_device_id": record.get("source_device_id"),
        "image_width": record.get("image_width"),
        "image_height": record.get("image_height"),
        "image_url": _safe_image_url(record.get("image_url")),
        "annotated_image_url": _safe_image_url(record.get("annotated_image_url")),
    }


def _unavailable_hardware_context(recognized_at: datetime) -> dict:
    """公共门面异常时返回稳定的部分结果，不泄露内部异常。"""
    return {
        "devices": [],
        "data_quality": {
            "binding_basis": "current",
            "time_match_rule": "absolute_nearest_unbounded_before_on_tie",
            "target_time": recognized_at.isoformat(timespec="seconds"),
            "timed_out": False,
            "device_total_count": 0,
            "device_success_count": 0,
            "device_failed_count": 0,
            "metric_total_count": 0,
            "metric_success_count": 0,
            "metric_missing_count": 0,
            "failed_devices": [],
            "missing_metrics": [],
            "warnings": ["硬件历史上下文暂时不可用，本次仅可分析识别事实。"],
            "history_retention_note": (
                "最近值仅在物联平台当前实际保留的历史范围内选择。"
            ),
        },
    }


def _analysis_guidance() -> dict:
    """明确模型必须遵守的事实、推断和不确定性边界。"""
    return {
        "required_sections": [
            "识别事实", "硬件事实", "可能关联", "风险", "建议", "数据不确定性"
        ],
        "rules": [
            "识别结果与硬件观测必须分别陈述，不得把相关性写成因果关系。",
            "每项硬件事实应结合 observed_at、offset_seconds 和 absolute_gap_seconds。",
            "时间差较大的硬件数据必须明确披露陈旧程度，不得描述为识别同时刻事实。",
            "缺失设备、缺失指标、查询超时和历史保留限制必须纳入不确定性说明。",
            "建议应与已知事实对应；证据不足时明确使用条件性表述。",
        ],
    }


async def recognition_analysis(
    record_id: Annotated[int, Field(gt=0, description="识别记录正整数 ID")],
) -> dict:
    """获取指定识别记录及其地块各硬件指标的时间最近历史观测。"""
    if isinstance(record_id, bool) or record_id <= 0:
        raise ToolError("record_id 必须是正整数")
    async with async_session_factory() as db:
        record = await RecognitionService.get_record(db, int(record_id))
    if not record:
        raise ToolError(f"识别记录不存在: {record_id}")

    recognized_at = _recognized_time(record.get("recognized_at"))
    try:
        hardware = await read_nearest_plot_hardware_history(
            int(record["plot_id"]),
            recognized_at,
            source_device_id=record.get("source_device_id"),
        )
    except Exception:
        hardware = _unavailable_hardware_context(recognized_at)
    return {
        "record": _record_context(record, recognized_at),
        "hardware": {"devices": hardware["devices"]},
        "data_quality": hardware["data_quality"],
        "analysis_guidance": _analysis_guidance(),
    }
