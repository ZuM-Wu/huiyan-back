"""公共硬件目录、详情上下文、历史分发及卸载前置保护。"""

from fastapi import HTTPException
from sqlalchemy import func, select, text

from core.db.base import async_session_factory
from core.db.hardware_device import HardwareDevice
from core.hardware_device_service import get_hardware_device_info
from core.hardware_provider import get_device_connection, hardware_provider_registry


async def list_hardware_providers() -> list[dict]:
    """结合已启用声明与存量镜像，停用来源仍可筛选。"""
    result = {owner: hardware_provider_registry.describe(owner) for owner in hardware_provider_registry.owners()}
    async with async_session_factory() as db:
        rows = (await db.execute(select(HardwareDevice.provider_id, HardwareDevice.provider_title,
                                       HardwareDevice.device_type, HardwareDevice.device_type_label).distinct())).all()
    for owner, title, code, label in rows:
        item = result.setdefault(owner, {"provider_id": owner, "title": title, "available": False,
                                         "detail_view": None, "device_types": []})
        if code and not any(kind["value"] == code for kind in item["device_types"]):
            item["device_types"].append({"value": code, "label": label or code})
    return list(result.values())


async def hardware_detail_context(device_id: int) -> dict | None:
    device = await get_hardware_device_info(device_id)
    if not device:
        return None
    return {"device": device, "provider": hardware_provider_registry.describe(device["provider_id"])}


async def ensure_hardware_uninstallable(owner: str, db) -> None:
    """任何来源设备都阻止卸载；必须在 owner 门禁和任务清理之前调用。"""
    count = int(await db.scalar(select(func.count(HardwareDevice.id)).where(HardwareDevice.provider_id == owner)) or 0)
    if count:
        raise HTTPException(status_code=409, detail=f"该插件仍关联 {count} 台硬件设备，禁止卸载；可先停用插件")


async def assert_hardware_schema_ready() -> None:
    """启动仅检查来源结构，旧环境必须由管理员停机执行一次性迁移。"""
    async with async_session_factory() as db:
        columns = set((await db.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_hardware_device'"
        ))).scalars().all())
    if not {"provider_id", "provider_device_id", "provider_title", "capabilities"}.issubset(columns):
        raise RuntimeError("硬件来源结构尚未迁移，请停止后端并运行 python scripts/migrate_hardware_providers.py --apply")


class HardwareHistoryRouter:
    """地块联合历史按本地设备 ID 路由，保留现有最近值计算规则。"""

    async def list_identifiers(self, device_id: str):
        connection, device = await get_device_connection(int(device_id), "history")
        return await connection.list_identifiers(device["provider_device_id"])

    async def get_history(self, device_id: str, **kwargs):
        connection, device = await get_device_connection(int(device_id), "history")
        return await connection.get_history(device["provider_device_id"], **kwargs)
