# -*- coding: utf-8 -*-
"""首页在线硬件挂件，只读取本地硬件设备镜像。"""

import logging
from collections.abc import Sequence

from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.hardware_device import HardwareDevice
from core.hardware_provider import hardware_provider_registry
from services.widget.widget_engine import BaseWidget

logger = logging.getLogger(__name__)
MAX_ONLINE_DEVICES = 9
DEVICE_ICONS = {
    "growth": "image",
    "soil": "chart-bubble",
    "root": "tree-round-dot",
}


def _device_item(device: HardwareDevice) -> dict:
    """将设备镜像归一为模板所需的稳定磁贴数据。"""
    name = device.nickname or device.device_name or "未命名设备"
    device_type = device.device_type_label or device.device_type or "未指定"
    return {
        "key": str(device.id),
        "label": name,
        "type": device_type,
        "icon": DEVICE_ICONS.get(device.device_type, "control-platform"),
        "image_url": getattr(device, "image_url", "") or "",
        "status": "在线",
        "url": "/admin/hardware-device",
    }


def _online_devices(devices: Sequence[HardwareDevice], owners: set[str]) -> list[HardwareDevice]:
    """过滤启用来源并按最近同步时间、主键倒序截取九台。"""
    available = [
        device for device in devices
        if device.available == 1 and device.provider_id in owners
    ]
    return sorted(
        available,
        key=lambda device: (device.last_sync_time is not None,
                           device.last_sync_time or 0, device.id),
        reverse=True,
    )[:MAX_ONLINE_DEVICES]


class HardwareOnlineDevicesWidget(BaseWidget):
    """展示来源启用且本地标记为可用的最近九台设备。"""

    name = "hardware_online_devices"
    title = "在线硬件"
    columns = 2
    weight = 45
    widget_type = "hardware"

    async def get_data(self) -> dict:
        owners = set(hardware_provider_registry.owners())
        if not owners:
            return {"items": []}
        async with async_session_factory() as db:
            result = await db.execute(
                select(HardwareDevice)
                .where(HardwareDevice.available == 1)
            )
            devices = _online_devices(result.scalars().all(), owners)
        return {"items": [_device_item(device) for device in devices]}


def register_hardware_widgets(engine) -> None:
    """注册在线硬件挂件。"""
    engine.register(HardwareOnlineDevicesWidget())
    logger.info("[WidgetEngine] 已注册在线硬件挂件: hardware_online_devices")
