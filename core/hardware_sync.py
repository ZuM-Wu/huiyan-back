"""多来源设备镜像同步；外部失败与设备缺失严格区分。"""

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.hardware_device import HardwareDevice
from core.hardware_provider import hardware_provider_registry
from core.hardware_types import HardwareError
from core.time_utils import china_now
from core.platform.health import platform_health

_sync_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def hardware_discovery_guard(owner: str):
    """串行化来源发现与设备删除；必须先于来源提交门禁获取，防止旧发现结果复活设备。"""
    async with _sync_locks.setdefault(owner, asyncio.Lock()):
        yield


async def _sync_provider(owner: str) -> dict:
    """同一来源串行发现与提交，防止较旧列表晚返回覆盖较新列表。"""
    async with hardware_discovery_guard(owner):
        connection = await hardware_provider_registry.connect(owner)
        devices = await connection.list_devices()
        async with connection.commit_guard():
            return await _store_devices(owner, devices)


async def _store_devices(owner: str, devices: list) -> dict:
    now = china_now()
    normalized = {item.provider_device_id: item for item in devices}
    async with async_session_factory() as db:
        existing = (await db.execute(select(HardwareDevice).where(
            HardwareDevice.provider_id == owner,
        ))).scalars().all()
        by_id = {item.provider_device_id: item for item in existing}
        created = 0
        for previous in existing:
            if previous.provider_device_id not in normalized:
                previous.available = 0
                previous.last_sync_error = "平台本次未返回该设备"
                previous.last_sync_time = now
        for external_id, dto in normalized.items():
            stored = by_id.get(external_id)
            if stored is None:
                stored = HardwareDevice(provider_id=owner, provider_device_id=external_id)
                db.add(stored)
                created += 1
            for field in ("device_name", "device_type", "device_type_label", "nickname", "external_id", "bot_id", "capabilities"):
                setattr(stored, field, getattr(dto, field))
            stored.external_latitude, stored.external_longitude, stored.external_address = dto.lat, dto.lng, dto.address
            stored.provider_title = hardware_provider_registry.describe(owner)["title"]
            stored.available, stored.last_sync_error, stored.last_sync_time = 1, "", now
        await db.commit()
    return {"provider_id": owner, "success": True, "received": len(devices), "created": created,
            "updated": len(devices) - created, "unavailable": len(set(by_id) - set(normalized)),
            "sync_time": now.isoformat(timespec="seconds") + "+08:00"}


async def sync_hardware_devices(*, provider_id: str = "") -> dict:
    """返回每个来源的结果；全部失败也保留可审阅的失败明细。"""
    owners = [provider_id] if provider_id else hardware_provider_registry.owners()
    results = []
    for owner in owners:
        try:
            results.append(await _sync_provider(owner))
            platform_health.mark(f"hardware:{owner}", "ready")
        except HardwareError as exc:
            results.append({"provider_id": owner, "success": False, "error": str(exc)})
            platform_health.mark(f"hardware:{owner}", "degraded", str(exc))
    totals = {key: sum(item.get(key, 0) for item in results) for key in ("received", "created", "updated", "unavailable")}
    return {**totals, "providers": results, "skipped": not owners,
            "sync_time": china_now().isoformat(timespec="seconds") + "+08:00"}
