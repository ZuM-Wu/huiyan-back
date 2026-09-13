"""系统 MCP 硬件能力，只使用硬件公共门面和当前调用者身份。"""

import asyncio
from math import isfinite

from core.hardware_device_service import (
    get_area_hardware_summary, get_hardware_device_info, list_hardware_devices_for_plot,
)
from core.hardware_realtime_service import read_device_identifiers, read_latest_device_snapshot
from services.mcp.errors import McpToolError
from services.mcp.tool_utils import current_claims, ensure_farmer_bound, ensure_plot_access, positive_id

MAX_DEVICES = 5
MAX_METRICS = 4
DEVICE_TIMEOUT_SECONDS = 5
DEVICE_INFO_TIMEOUT_SECONDS = 3
DEVICE_INFO_CONCURRENCY = 5


def _device_summary(info: dict) -> dict:
    """显式字段白名单，设备 DTO 内的坐标、平台编号和错误原文不得进入 MCP。"""
    return {
        "device_id": int(info["id"]),
        "name": str(info.get("nickname") or info.get("device_name") or "")[:40],
        "type": str(info.get("device_type_label") or info.get("device_type") or "")[:32],
        "online": bool(info.get("available") and info.get("provider_available")),
        "capabilities": [item for item in info.get("capabilities", []) if item in {"realtime", "history", "take_photo"}],
    }


def _metric_summary(snapshot: dict | None) -> dict:
    """只保留短标量指标；图片和扩展对象由后台详情读取。"""
    metrics = []
    for raw in (snapshot or {}).get("list") or []:
        if not isinstance(raw, dict) or raw.get("dataType") in {"image", "video"}:
            continue
        value = raw.get("value")
        if not isinstance(value, (str, int, float, bool)) or (isinstance(value, float) and not isfinite(value)):
            continue
        if isinstance(value, str) and (len(value) > 80 or value.lower().startswith(("data:", "http:", "https:"))):
            continue
        metrics.append({"name": str(raw.get("name") or raw.get("identifier") or "")[:32],
                        "value": value, "unit": str(raw.get("unit") or "")[:12]})
    return {"fetched_at": str((snapshot or {}).get("fetched_at") or "")[:40],
            "metrics": metrics[:MAX_METRICS], "omitted_count": max(0, len(metrics) - MAX_METRICS)}


async def _bound_devices(plot_id: int) -> tuple[list[dict], int]:
    devices = await list_hardware_devices_for_plot(plot_id)
    semaphore = asyncio.Semaphore(DEVICE_INFO_CONCURRENCY)

    async def load(item: dict) -> dict | None:
        async with semaphore:
            try:
                info = await asyncio.wait_for(
                    get_hardware_device_info(item["device_id"]),
                    timeout=DEVICE_INFO_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return None
        # 读取期间解绑的设备不得作为该地块数据返回。
        if info and int(info.get("plot_id") or 0) == plot_id:
            return info
        return None

    loaded = await asyncio.gather(
        *(load(item) for item in devices[:MAX_DEVICES]),
        return_exceptions=True,
    )
    details = [item for item in loaded if isinstance(item, dict)]
    return details, max(0, len(devices) - len(details))


def _plot_result(plot: dict, devices: list[dict], omitted: int) -> dict:
    result = {"plot_id": plot["plot_id"], "plot_name": plot["plot_name"], "devices": devices,
              "truncated": omitted > 0, "omitted_count": omitted,
              "next_action": "按device_id查询详情" if omitted else ""}
    if not devices:
        result.update({"code": "no_devices", "reason": "没有绑定设备"})
    return result


async def hardware_area_summary(area_id: int) -> dict:
    """查询产区设备和可读取图片设备数量。"""
    positive_id(area_id, "area_id")
    await ensure_farmer_bound(current_claims(), area_id)
    result = await get_area_hardware_summary(area_id)
    if result is None:
        raise McpToolError("area_not_found")
    return result


async def hardware_plot_devices(plot_id: int) -> dict:
    """查询地块已绑定设备状态和能力。"""
    plot = await ensure_plot_access(plot_id)
    devices, omitted = await _bound_devices(plot_id)
    return _plot_result(plot, [_device_summary(info) for info in devices], omitted)


async def _read_metrics(info: dict, *, live: bool) -> dict:
    item = {"device_id": info["id"], "name": _device_summary(info)["name"]}
    if live and (not _device_summary(info)["online"] or "realtime" not in info.get("capabilities", [])):
        return {**item, "code": "device_unavailable", "reason": "设备离线或不支持实时读取"}
    try:
        read = read_device_identifiers(info["id"], refresh=True) if live else read_latest_device_snapshot(info["id"])
        snapshot = await asyncio.wait_for(read, timeout=DEVICE_TIMEOUT_SECONDS)
    except Exception:
        return {**item, "code": "device_unavailable", "reason": "设备暂不可用"}
    summary = _metric_summary(snapshot)
    if not summary["metrics"]:
        summary.update({"code": "no_data", "reason": "暂无可用指标"})
    return {**item, **summary}


async def hardware_plot_latest(plot_id: int) -> dict:
    """读取地块设备最新快照，不发起外部请求。"""
    plot = await ensure_plot_access(plot_id)
    devices, omitted = await _bound_devices(plot_id)
    items = await asyncio.gather(
        *(_read_metrics(info, live=False) for info in devices),
        return_exceptions=True,
    )
    items = [
        item if isinstance(item, dict) else {
            "code": "device_unavailable", "reason": "设备暂不可用",
        }
        for item in items
    ]
    return _plot_result(plot, list(items), omitted)


async def hardware_plot_realtime(plot_id: int) -> dict:
    """现场读取地块实时指标；单设备最多等待五秒，允许部分成功。"""
    plot = await ensure_plot_access(plot_id)
    devices, omitted = await _bound_devices(plot_id)
    items = await asyncio.gather(
        *(_read_metrics(info, live=True) for info in devices),
        return_exceptions=True,
    )
    items = [
        item if isinstance(item, dict) else {
            "code": "device_unavailable", "reason": "设备暂不可用",
        }
        for item in items
    ]
    return _plot_result(plot, list(items), omitted)


async def hardware_device_detail(device_id: int) -> dict:
    """读取设备与地块摘要，农户不可访问未绑定设备。"""
    positive_id(device_id, "device_id")
    info = await get_hardware_device_info(device_id)
    if not info:
        raise McpToolError("device_not_found")
    if info.get("plot_id"):
        await ensure_plot_access(int(info["plot_id"]))
    elif current_claims().get("user_type") == "farmer":
        raise McpToolError("permission_denied", "无权访问未绑定地块的设备")
    return {**_device_summary(info), "plot_id": info.get("plot_id"),
            "plot_name": str(info.get("plot_name") or "")[:40], "latest": await _read_metrics(info, live=False)}


HARDWARE_TOOLS = [
    {"name": "hardware_area_summary", "description": "查询产区设备数量和在线能力摘要。", "handler": hardware_area_summary, "audience": "both", "permission_code": "hardware:list"},
    {"name": "hardware_plot_devices", "description": "查询地块绑定设备、状态和能力。", "handler": hardware_plot_devices, "audience": "both", "permission_code": "hardware:list"},
    {"name": "hardware_plot_latest", "description": "查询地块设备最新指标快照。", "handler": hardware_plot_latest, "audience": "both", "permission_code": "hardware:data"},
    {"name": "hardware_plot_realtime", "description": "现场读取地块设备实时指标，允许部分结果。", "handler": hardware_plot_realtime, "audience": "both", "permission_code": "hardware:data"},
    {"name": "hardware_device_detail", "description": "查询设备状态、绑定地块和最新指标。", "handler": hardware_device_detail, "audience": "both", "permission_code": "hardware:data"},
]
