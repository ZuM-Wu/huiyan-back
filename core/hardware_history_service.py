"""地块硬件指标历史的只读最近值查询门面。"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable

from core.hardware_device_service import list_hardware_devices_for_plot
from core.time_utils import CHINA_TIMEZONE
from core.hardware_catalog import HardwareHistoryRouter
from core.hardware_types import HardwareHistoryAdapter

DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_EXTERNAL_CONCURRENCY = 5
HISTORY_RETENTION_NOTE = (
    "最近值仅在物联平台当前实际保留的历史范围内选择，平台可能清理较早数据。"
)


def _normalize_target_time(value: datetime) -> datetime:
    """把数据库无时区时间和外部带时区时间统一为中国标准时间。"""
    if not isinstance(value, datetime):
        raise TypeError("target_time 必须是 datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=CHINA_TIMEZONE)
    return value.astimezone(CHINA_TIMEZONE)


def _parse_observed_time(value: Any) -> datetime | None:
    """解析物联历史时间；无偏移值按中国标准时间解释。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TIMEZONE)
    return parsed.astimezone(CHINA_TIMEZONE)


def _metric_meta(raw: dict) -> dict | None:
    """从当前标识清单提取稳定字段，并按 identifier 去重。"""
    identifier = str(raw.get("identifier") or "").strip()
    if not identifier:
        return None
    return {
        "identifier": identifier,
        "name": str(
            raw.get("name") or raw.get("identifierName") or identifier
        ).strip(),
        "unit": str(raw.get("unit") or "").strip(),
        "data_type": str(
            raw.get("dataType") or raw.get("data_type") or ""
        ).strip(),
    }


def _safe_value(value: Any) -> tuple[Any, bool]:
    """过滤内嵌 Base64/二进制值，并归一化常见 JSON 标量。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None, False
    if isinstance(value, str) and (
        value.lstrip().lower().startswith("data:")
        or ";base64," in value[:256].lower()
    ):
        return None, False
    if isinstance(value, Decimal):
        return float(value), True
    if isinstance(value, datetime):
        return _normalize_target_time(value).isoformat(timespec="seconds"), True
    return value, True


def _history_candidate(payload: dict, target_time: datetime) -> dict | None:
    """从单条分页响应提取可比较候选值。"""
    rows = payload.get("list") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    raw = rows[0]
    observed_at = _parse_observed_time(raw.get("time"))
    if observed_at is None:
        return None
    return {
        "value": raw.get("value"),
        "observed_at": observed_at,
        "offset_seconds": (observed_at - target_time).total_seconds(),
    }


async def _limited_call(
    semaphore: asyncio.Semaphore,
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    """限制全部物联请求共享的并发度。"""
    async with semaphore:
        return await operation()


async def _read_identifiers(
    client: HardwareHistoryAdapter,
    semaphore: asyncio.Semaphore,
    platform_device_name: str,
) -> list[dict]:
    return await _limited_call(
        semaphore,
        lambda: client.list_identifiers(platform_device_name),
    )


async def _nearest_observation(
    client: HardwareHistoryAdapter,
    semaphore: asyncio.Semaphore,
    platform_device_name: str,
    metric: dict,
    target_time: datetime,
) -> tuple[dict | None, str]:
    """查询单指标前后候选值，等距时优先识别前数据。"""
    identifier = metric["identifier"]
    before_payload = await _limited_call(
        semaphore,
        lambda: client.get_history(
            platform_device_name,
            identifier=identifier,
            end_time=target_time,
            page=1,
            size=1,
        ),
    )
    after_payload = await _limited_call(
        semaphore,
        lambda: client.get_history(
            platform_device_name,
            identifier=identifier,
            start_time=target_time,
            page=1,
            size=1,
        ),
    )
    before = _history_candidate(before_payload, target_time)
    after = None
    after_total = int(after_payload.get("total") or 0)
    if after_total == 1:
        after = _history_candidate(after_payload, target_time)
    elif after_total > 1:
        earliest_payload = await _limited_call(
            semaphore,
            lambda: client.get_history(
                platform_device_name,
                identifier=identifier,
                start_time=target_time,
                page=after_total,
                size=1,
            ),
        )
        after = _history_candidate(earliest_payload, target_time)

    candidates = [item for item in (before, after) if item is not None]
    if not candidates:
        return None, "物联平台保留范围内无历史数据"
    chosen = min(
        candidates,
        key=lambda item: (abs(item["offset_seconds"]), item["offset_seconds"] > 0),
    )
    value, allowed = _safe_value(chosen["value"])
    if not allowed:
        return None, "历史值为内嵌 Base64 或二进制数据，已按安全规则忽略"
    offset = round(chosen["offset_seconds"])
    return {
        **metric,
        "value": value,
        "observed_at": chosen["observed_at"].isoformat(timespec="seconds"),
        "offset_seconds": offset,
        "absolute_gap_seconds": abs(offset),
    }, ""


def _device_response(device: dict, source_device_id: int | None) -> dict:
    """构造不包含平台编号、位置或凭据的设备响应。"""
    return {
        "device_id": device["device_id"],
        "name": device["name"],
        "type": device["type"],
        "type_name": device["type_name"],
        "is_source_device": device["device_id"] == source_device_id,
        "observations": [],
    }


def _failure_reason(kind: str, timed_out: bool = False) -> str:
    if timed_out:
        return "联合硬件历史查询达到总超时"
    if kind == "device":
        return "无法读取设备当前指标清单"
    return "物联平台历史查询失败"


def _binding_timeout_result(target_time: datetime) -> dict:
    """地块绑定查询耗尽总预算时返回脱敏的空上下文。"""
    return {
        "devices": [],
        "data_quality": {
            "binding_basis": "current",
            "time_match_rule": "absolute_nearest_unbounded_before_on_tie",
            "target_time": target_time.isoformat(timespec="seconds"),
            "timed_out": True,
            "device_total_count": 0,
            "device_success_count": 0,
            "device_failed_count": 0,
            "metric_total_count": 0,
            "metric_success_count": 0,
            "metric_missing_count": 0,
            "failed_devices": [],
            "missing_metrics": [],
            "warnings": ["当前地块绑定设备查询达到总超时，未能继续读取硬件历史。"],
            "history_retention_note": HISTORY_RETENTION_NOTE,
        },
    }


async def _wait_tasks(tasks: set[asyncio.Task], timeout: float) -> tuple[set, set]:
    """在剩余预算内等待任务；空任务集直接返回。"""
    if not tasks:
        return set(), set()
    if timeout <= 0:
        return set(), set(tasks)
    return await asyncio.wait(tasks, timeout=timeout)


async def _prepare_metric_tasks(
    devices: list[dict],
    client: HardwareHistoryAdapter,
    semaphore: asyncio.Semaphore,
    target_time: datetime,
    remaining_seconds: float,
) -> tuple[dict[asyncio.Task, tuple[dict, dict]], set[int], list[dict], int, bool]:
    """读取设备指标清单，并创建全部指标历史查询任务。"""
    identifier_tasks = {
        asyncio.create_task(_read_identifiers(
            client, semaphore, device["platform_device_name"]
        )): device
        for device in devices
    }
    done, pending = await _wait_tasks(set(identifier_tasks), remaining_seconds)
    metric_tasks: dict[asyncio.Task, tuple[dict, dict]] = {}
    success_ids: set[int] = set()
    failures: list[dict] = []
    metric_total = 0
    for task in done:
        device = identifier_tasks[task]
        try:
            raw_identifiers = task.result()
        except Exception:
            failures.append({
                "device_id": device["device_id"],
                "reason": _failure_reason("device"),
            })
            continue
        success_ids.add(device["device_id"])
        unique_metrics: dict[str, dict] = {}
        for raw in raw_identifiers:
            metric = _metric_meta(raw) if isinstance(raw, dict) else None
            if metric:
                unique_metrics.setdefault(metric["identifier"], metric)
        metric_total += len(unique_metrics)
        for metric in unique_metrics.values():
            history_task = asyncio.create_task(_nearest_observation(
                client,
                semaphore,
                device["platform_device_name"],
                metric,
                target_time,
            ))
            metric_tasks[history_task] = (device, metric)
    for task in pending:
        device = identifier_tasks[task]
        task.cancel()
        failures.append({
            "device_id": device["device_id"],
            "reason": _failure_reason("device", timed_out=True),
        })
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return metric_tasks, success_ids, failures, metric_total, bool(pending)


def _missing_metric(device: dict, metric: dict, reason: str) -> dict:
    """构造不包含平台设备编号和原始异常的缺失指标说明。"""
    return {
        "device_id": device["device_id"],
        "identifier": metric["identifier"],
        "name": metric["name"],
        "reason": reason,
    }


async def _collect_metric_results(
    metric_tasks: dict[asyncio.Task, tuple[dict, dict]],
    states: dict[int, dict],
    remaining_seconds: float,
) -> tuple[list[dict], bool]:
    """收集已完成观测，并将失败或超时指标转为脱敏缺失项。"""
    done, pending = await _wait_tasks(set(metric_tasks), remaining_seconds)
    missing: list[dict] = []
    for task in done:
        device, metric = metric_tasks[task]
        try:
            observation, reason = task.result()
        except Exception:
            observation, reason = None, _failure_reason("metric")
        if observation is not None:
            states[device["device_id"]]["observations"].append(observation)
        else:
            missing.append(_missing_metric(device, metric, reason))
    for task in pending:
        device, metric = metric_tasks[task]
        task.cancel()
        missing.append(_missing_metric(
            device,
            metric,
            _failure_reason("metric", timed_out=True),
        ))
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return missing, bool(pending)


async def read_nearest_plot_hardware_history(
    plot_id: int,
    target_time: datetime,
    *,
    source_device_id: int | None = None,
    client: HardwareHistoryAdapter | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """读取当前绑定设备各指标最接近目标时间的历史观测。"""
    if plot_id <= 0:
        raise ValueError("plot_id 必须是正整数")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    normalized_time = _normalize_target_time(target_time)
    try:
        devices = await asyncio.wait_for(
            list_hardware_devices_for_plot(plot_id),
            timeout=deadline - loop.time(),
        )
    except TimeoutError:
        return _binding_timeout_result(normalized_time)
    active_client = client or HardwareHistoryRouter()
    if client is None:
        devices = [{**device, "platform_device_name": str(device["device_id"])} for device in devices]
    semaphore = asyncio.Semaphore(MAX_EXTERNAL_CONCURRENCY)
    states = {
        device["device_id"]: _device_response(device, source_device_id)
        for device in devices
    }
    (
        metric_tasks,
        device_success_ids,
        failed_devices,
        metric_total,
        identifier_timed_out,
    ) = await _prepare_metric_tasks(
        devices,
        active_client,
        semaphore,
        normalized_time,
        deadline - loop.time(),
    )
    missing_metrics, metric_timed_out = await _collect_metric_results(
        metric_tasks,
        states,
        deadline - loop.time(),
    )
    timed_out = identifier_timed_out or metric_timed_out

    for state in states.values():
        state["observations"].sort(key=lambda item: item["identifier"])
    missing_metrics.sort(key=lambda item: (item["device_id"], item["identifier"]))
    source_bound = source_device_id is None or source_device_id in states
    warnings = [] if source_bound else [
        "识别来源设备当前未绑定到该地块，因此未包含其历史指标。"
    ]
    metric_success = sum(
        len(state["observations"]) for state in states.values()
    )
    return {
        "devices": list(states.values()),
        "data_quality": {
            "binding_basis": "current",
            "time_match_rule": "absolute_nearest_unbounded_before_on_tie",
            "target_time": normalized_time.isoformat(timespec="seconds"),
            "timed_out": timed_out,
            "device_total_count": len(devices),
            "device_success_count": len(device_success_ids),
            "device_failed_count": len(failed_devices),
            "metric_total_count": metric_total,
            "metric_success_count": metric_success,
            "metric_missing_count": len(missing_metrics),
            "failed_devices": failed_devices,
            "missing_metrics": missing_metrics,
            "warnings": warnings,
            "history_retention_note": HISTORY_RETENTION_NOTE,
        },
    }
