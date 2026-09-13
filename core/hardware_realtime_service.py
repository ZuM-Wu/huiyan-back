"""硬件实时数据快照、自动获取设置与批量采集服务。"""

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from core.db.hardware_device import HardwareDevice, HardwareRealtimeSnapshot
from core.hardware_device_service import sync_hardware_devices
from core.time_utils import china_now
from core.hardware_provider import get_device_connection, hardware_provider_registry
from core.hardware_types import HardwareError

AUTO_FETCH_ENABLED_KEY = "hardware_realtime_enabled"
AUTO_FETCH_INTERVAL_SECONDS_KEY = "hardware_realtime_interval_seconds"
LEGACY_AUTO_FETCH_INTERVAL_KEY = "hardware_realtime_interval_minutes"
DEFAULT_INTERVAL_SECONDS = 15 * 60
MIN_INTERVAL_SECONDS = 1
MAX_INTERVAL_SECONDS = 24 * 60 * 60


class HardwareRealtimePullError(RuntimeError):
    """整轮实时数据采集未取得任何成功结果。"""


def _serialize_time(value) -> str:
    """把数据库中的中国本地时间输出为带时区的秒级 ISO 字符串。"""
    return value.isoformat(timespec="seconds") + "+08:00" if value else ""


def _normalize_interval_seconds(value: Any, default: int = DEFAULT_INTERVAL_SECONDS) -> int:
    """容错读取存量配置，并钳制到管理端允许范围。"""
    try:
        interval = int(value)
    except (TypeError, ValueError):
        interval = default
    return min(max(interval, MIN_INTERVAL_SECONDS), MAX_INTERVAL_SECONDS)


async def get_auto_fetch_settings() -> dict:
    """读取硬件实时数据自动获取设置，兼容旧的分钟配置。"""
    async with async_session_factory() as db:
        manager = ConfigManager()
        enabled_value = await manager.get(AUTO_FETCH_ENABLED_KEY, db)
        interval_value = await manager.get(AUTO_FETCH_INTERVAL_SECONDS_KEY, db)
        interval_seconds: Any = interval_value
        if interval_value in (None, ""):
            legacy_value = await manager.get(LEGACY_AUTO_FETCH_INTERVAL_KEY, db)
            if legacy_value in (None, ""):
                interval_seconds = DEFAULT_INTERVAL_SECONDS
            else:
                try:
                    interval_seconds = int(legacy_value) * 60
                except (TypeError, ValueError):
                    interval_seconds = DEFAULT_INTERVAL_SECONDS
    enabled = True if enabled_value in (None, "") else str(enabled_value) == "1"
    return {
        "enabled": enabled,
        "interval_seconds": _normalize_interval_seconds(interval_seconds),
    }


async def save_auto_fetch_settings(enabled: bool, interval_seconds: int) -> dict:
    """持久化秒级自动获取设置，调度重排由 API 层在保存后显式触发。"""
    interval = _normalize_interval_seconds(interval_seconds)
    async with async_session_factory() as db:
        manager = ConfigManager()
        await manager.set(
            AUTO_FETCH_ENABLED_KEY,
            "1" if enabled else "0",
            db,
            description="硬件实时数据自动获取开关",
        )
        await manager.set(
            AUTO_FETCH_INTERVAL_SECONDS_KEY,
            str(interval),
            db,
            description="硬件实时数据自动获取间隔秒数",
        )
    return {"enabled": bool(enabled), "interval_seconds": interval}


def _snapshot_response(snapshot: HardwareRealtimeSnapshot, source: str) -> dict:
    """构造不包含外部凭据和设备位置的实时快照响应。"""
    payload = snapshot.payload if isinstance(snapshot.payload, list) else []
    return {
        "list": payload,
        "fetched_at": _serialize_time(snapshot.last_success_time),
        "last_attempt_time": _serialize_time(snapshot.last_attempt_time),
        "last_error": snapshot.last_error or "",
        "source": source,
    }


def _empty_snapshot_response() -> dict:
    """缓存尚未生成时返回稳定的空快照，避免自动刷新打外部平台。"""
    return {
        "list": [],
        "fetched_at": "",
        "last_attempt_time": "",
        "last_error": "",
        "source": "snapshot",
    }


async def _get_snapshot(device_id: int) -> HardwareRealtimeSnapshot | None:
    async with async_session_factory() as db:
        return (await db.execute(select(HardwareRealtimeSnapshot).where(
            HardwareRealtimeSnapshot.device_id == device_id
        ))).scalar_one_or_none()


async def read_latest_device_snapshot(device_id: int) -> dict | None:
    """只读最新成功快照，不因缓存缺失而访问外部物联平台。"""
    snapshot = await _get_snapshot(device_id)
    if not snapshot or not snapshot.last_success_time:
        return None
    return _snapshot_response(snapshot, "snapshot")


async def _store_snapshot_success(device_id: int, payload: list[dict]) -> HardwareRealtimeSnapshot:
    """使用 MySQL upsert 原子覆盖一台设备的最新成功快照。"""
    now = china_now()
    statement = mysql_insert(HardwareRealtimeSnapshot).values(
        device_id=device_id,
        payload=payload,
        last_attempt_time=now,
        last_success_time=now,
        last_error="",
        create_time=now,
        update_time=now,
    )
    statement = statement.on_duplicate_key_update(
        payload=statement.inserted.payload,
        last_attempt_time=statement.inserted.last_attempt_time,
        last_success_time=statement.inserted.last_success_time,
        last_error="",
        update_time=statement.inserted.update_time,
    )
    async with async_session_factory() as db:
        # 删除可能发生在采集网络请求期间；锁定父记录，迟到结果不能创建孤立快照。
        if await db.scalar(select(HardwareDevice.id).where(HardwareDevice.id == device_id).with_for_update()) is None:
            raise HardwareError("设备不存在", 404)
        await db.execute(statement)
        await db.commit()
    return HardwareRealtimeSnapshot(
        device_id=device_id,
        payload=payload,
        last_attempt_time=now,
        last_success_time=now,
        last_error="",
    )


async def _store_snapshot_failure(device_id: int, message: str) -> None:
    """记录失败尝试；已存在快照时不覆盖最近成功数据。"""
    now = china_now()
    statement = mysql_insert(HardwareRealtimeSnapshot).values(
        device_id=device_id,
        payload=[],
        last_attempt_time=now,
        last_success_time=None,
        last_error=message,
        create_time=now,
        update_time=now,
    )
    statement = statement.on_duplicate_key_update(
        last_attempt_time=statement.inserted.last_attempt_time,
        last_error=statement.inserted.last_error,
        update_time=statement.inserted.update_time,
    )
    async with async_session_factory() as db:
        # 删除可能发生在采集网络请求期间；锁定父记录，迟到结果不能创建孤立快照。
        if await db.scalar(select(HardwareDevice.id).where(HardwareDevice.id == device_id).with_for_update()) is None:
            raise HardwareError("设备不存在", 404)
        await db.execute(statement)
        await db.commit()


async def read_device_identifiers(
    device_id: int,
    device_name: str = "",
    *,
    refresh: bool = False,
    cache_only: bool = False,
) -> dict:
    """读取实时指标；自动刷新可明确限制为只读快照。"""
    if not refresh:
        snapshot = await _get_snapshot(device_id)
        if snapshot and snapshot.last_success_time:
            return _snapshot_response(snapshot, "snapshot")
        if cache_only:
            return _empty_snapshot_response()
    connection, device = await get_device_connection(device_id, "realtime")
    try:
        payload = await connection.read_realtime(device["provider_device_id"])
    except HardwareError as exc:
        async with connection.commit_guard():
            await _store_snapshot_failure(device_id, str(exc))
        raise
    async with connection.commit_guard():
        snapshot = await _store_snapshot_success(device_id, payload)
    return _snapshot_response(snapshot, "live")


async def _available_devices() -> list[tuple[int, str]]:
    """返回自动采集目标，数据库会话不跨越外部网络请求。"""
    async with async_session_factory() as db:
        rows = (await db.execute(select(
            HardwareDevice.id, HardwareDevice.device_name, HardwareDevice.capabilities
        ).where(HardwareDevice.available == 1, HardwareDevice.provider_id.in_(hardware_provider_registry.owners())))).all()
    return [(int(device_id), str(device_name)) for device_id, device_name, capabilities in rows
            if "realtime" in (capabilities or [])]


async def pull_all_hardware_realtime() -> dict:
    """同步设备列表后，以最多三路并发刷新全部可用设备快照。"""
    settings = await get_auto_fetch_settings()
    if not settings["enabled"]:
        return {"skipped": True, "total": 0, "success": 0, "failed": 0}

    if not hardware_provider_registry.owners():
        return {"skipped": True, "reason": "无启用硬件来源", "total": 0, "success": 0, "failed": 0}
    device_sync = await sync_hardware_devices()
    source_failures = [item for item in device_sync.get("providers", []) if not item["success"]]
    devices = await _available_devices()
    semaphore = asyncio.Semaphore(3)

    async def fetch(device_id: int, device_name: str) -> dict:
        async with semaphore:
            try:
                await read_device_identifiers(
                    device_id, device_name, refresh=True
                )
                return {"device_id": device_id, "success": True, "error": ""}
            except HardwareError as exc:
                return {
                    "device_id": device_id,
                    "success": False,
                    "error": str(exc),
                }

    results = await asyncio.gather(*(
        fetch(device_id, device_name) for device_id, device_name in devices
    ))
    success = sum(1 for item in results if item["success"])
    summary = {
        "skipped": False,
        "device_sync": device_sync,
        "total": len(results),
        "success": success,
        "failed": len(results) - success,
        "failures": [item for item in results if not item["success"]],
        "source_failed": len(source_failures),
    }
    if results and success == 0:
        raise HardwareRealtimePullError("全部硬件设备实时数据获取失败")
    if not results and source_failures and len(source_failures) == len(device_sync.get("providers", [])):
        raise HardwareRealtimePullError("全部硬件来源同步失败，未能确定采集设备")
    return summary
