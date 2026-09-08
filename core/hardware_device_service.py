"""硬件设备本地镜像、绑定与地图标记业务服务。"""

from decimal import Decimal

from sqlalchemy import func, or_, select, update

from core.db.base import async_session_factory
from core.db.hardware_device import HardwareDevice
from core.db.production_area import AreaFarmer, Plot, ProductionArea
from core.hardware_marker_geometry import (
    choose_interior_point,
    parse_plot_boundary,
    point_in_polygon,
    resolve_marker_position,
)
from core.hardware_provider import hardware_provider_registry
from core.hardware_sync import sync_hardware_devices  # noqa: F401


def _serialize_time(value) -> str:
    """序列化数据库中的中国本地时间。"""
    return value.isoformat(timespec="seconds") + "+08:00" if value else ""


def _device_dict(device: HardwareDevice, area_name: str = "", plot_name: str = "") -> dict:
    """输出管理端设备列表 DTO。"""
    return {
        "id": device.id,
        "provider_id": device.provider_id,
        "provider_device_id": device.provider_device_id,
        "provider_title": device.provider_title,
        "provider_available": hardware_provider_registry.active(device.provider_id),
        "capabilities": list(device.capabilities or []),
        "external_id": device.external_id,
        "device_name": device.device_name,
        "device_type": device.device_type,
        "device_type_label": device.device_type_label,
        "nickname": device.nickname,
        "bot_id": device.bot_id,
        "external_latitude": device.external_latitude,
        "external_longitude": device.external_longitude,
        "external_address": device.external_address,
        "image_url": device.image_url,
        "visible_metric_identifiers": (
            list(device.visible_metric_identifiers)
            if isinstance(device.visible_metric_identifiers, list) else None
        ),
        "area_id": device.area_id,
        "area_name": area_name or "",
        "plot_id": device.plot_id,
        "plot_name": plot_name or "",
        "marker_ratio": float(device.marker_ratio) if device.marker_ratio is not None else None,
        "marker_longitude": (
            float(device.marker_longitude) if device.marker_longitude is not None else None
        ),
        "marker_latitude": (
            float(device.marker_latitude) if device.marker_latitude is not None else None
        ),
        "available": bool(device.available),
        "last_sync_error": device.last_sync_error or "",
        "last_sync_time": _serialize_time(device.last_sync_time),
        "create_time": _serialize_time(device.create_time),
        "update_time": _serialize_time(device.update_time),
    }


async def list_hardware_devices(
    *, provider_id: str = "", keyword: str = "", device_type: str = "", binding_status: str = "",
    available: int | None = None, page: int = 1, size: int = 20,
) -> dict:
    """分页查询本地设备镜像。"""
    conditions = []
    if provider_id:
        conditions.append(HardwareDevice.provider_id == provider_id)
    if keyword:
        conditions.append(or_(
            HardwareDevice.device_name.contains(keyword),
            HardwareDevice.nickname.contains(keyword),
            HardwareDevice.device_type_label.contains(keyword),
        ))
    if device_type:
        conditions.append(HardwareDevice.device_type == device_type)
    if binding_status == "bound":
        conditions.append(HardwareDevice.plot_id.is_not(None))
    elif binding_status == "unbound":
        conditions.append(HardwareDevice.plot_id.is_(None))
    if available is not None:
        conditions.append(HardwareDevice.available == available)
    async with async_session_factory() as db:
        total = (await db.execute(
            select(func.count(HardwareDevice.id)).where(*conditions)
        )).scalar() or 0
        rows = (await db.execute(
            select(HardwareDevice, ProductionArea.name, Plot.name)
            .outerjoin(ProductionArea, ProductionArea.id == HardwareDevice.area_id)
            .outerjoin(Plot, Plot.id == HardwareDevice.plot_id)
            .where(*conditions)
            .order_by(HardwareDevice.available.desc(), HardwareDevice.id.desc())
            .offset((page - 1) * size).limit(size)
        )).all()
    return {
        "list": [_device_dict(device, area_name, plot_name) for device, area_name, plot_name in rows],
        "total": total,
        "page": page,
        "size": size,
    }


async def get_hardware_device(device_id: int) -> HardwareDevice | None:
    """按本地主键读取设备。"""
    async with async_session_factory() as db:
        return (await db.execute(
            select(HardwareDevice).where(HardwareDevice.id == device_id)
        )).scalar_one_or_none()


async def get_hardware_device_info(device_id: int) -> dict | None:
    """按本地主键返回不暴露 ORM 的设备只读 DTO。"""
    async with async_session_factory() as db:
        row = (await db.execute(
            select(HardwareDevice, ProductionArea.name, Plot.name)
            .outerjoin(ProductionArea, ProductionArea.id == HardwareDevice.area_id)
            .outerjoin(Plot, Plot.id == HardwareDevice.plot_id)
            .where(HardwareDevice.id == device_id)
        )).one_or_none()
    if not row:
        return None
    device, area_name, plot_name = row
    return _device_dict(device, area_name or "", plot_name or "")


async def list_hardware_devices_for_plot(plot_id: int) -> list[dict]:
    """返回地块当前绑定设备的历史查询 DTO，不过滤平台可用状态。"""
    async with async_session_factory() as db:
        rows = (await db.execute(select(
            HardwareDevice.id,
            HardwareDevice.device_name,
            HardwareDevice.nickname,
            HardwareDevice.device_type,
            HardwareDevice.device_type_label,
            HardwareDevice.available,
        ).where(
            HardwareDevice.plot_id == plot_id,
        ).order_by(HardwareDevice.id))).all()
    return [
        {
            "device_id": int(row.id),
            "platform_device_name": str(row.device_name or ""),
            "name": str(row.nickname or row.device_name or ""),
            "type": str(row.device_type or ""),
            "type_name": str(row.device_type_label or ""),
            "available": bool(row.available),
        }
        for row in rows
    ]


async def update_device_appearance(device_id: int, image_url: str) -> bool:
    """更新设备自定义图片。"""
    async with async_session_factory() as db:
        device = (await db.execute(
            select(HardwareDevice).where(HardwareDevice.id == device_id)
        )).scalar_one_or_none()
        if not device:
            return False
        device.image_url = image_url.strip()
        await db.commit()
    return True


async def update_metric_visibility(
    device_id: int, identifiers: list[str],
) -> list[str] | None:
    """保存设备实时指标卡片显示顺序，空数组表示明确全部隐藏。"""
    normalized = list(dict.fromkeys(
        value.strip() for value in identifiers if value.strip()
    ))
    async with async_session_factory() as db:
        device = (await db.execute(
            select(HardwareDevice).where(HardwareDevice.id == device_id)
        )).scalar_one_or_none()
        if not device:
            return None
        device.visible_metric_identifiers = normalized
        await db.commit()
    return normalized


async def bind_hardware_device(
    device_id: int, area_id: int, plot_id: int,
) -> str:
    """绑定有效产区和地块，并为有边界的地块生成内部默认位置。"""
    async with async_session_factory() as db:
        device = (await db.execute(
            select(HardwareDevice).where(HardwareDevice.id == device_id)
        )).scalar_one_or_none()
        if not device:
            return "设备不存在"
        area = (await db.execute(select(ProductionArea).where(
            ProductionArea.id == area_id, ProductionArea.status == 1,
        ))).scalar_one_or_none()
        plot = (await db.execute(select(Plot).where(
            Plot.id == plot_id, Plot.area_id == area_id, Plot.status == 1,
        ))).scalar_one_or_none()
        if not area:
            return "产区不存在或已停用"
        if not plot:
            return "地块不存在、已停用或不属于所选产区"
        position = choose_interior_point(plot.boundary)
        device.area_id = area_id
        device.plot_id = plot_id
        device.marker_ratio = None
        device.marker_longitude = Decimal(str(position[0])) if position else None
        device.marker_latitude = Decimal(str(position[1])) if position else None
        await db.commit()
    return ""


async def unbind_hardware_device(device_id: int) -> bool:
    """解绑设备并清除新旧地图位置。"""
    async with async_session_factory() as db:
        result = await db.execute(
            update(HardwareDevice).where(HardwareDevice.id == device_id).values(
                area_id=None, plot_id=None, marker_ratio=None,
                marker_longitude=None, marker_latitude=None,
            )
        )
        await db.commit()
    return bool(getattr(result, "rowcount", 0))


async def update_marker_position(
    device_id: int, marker_longitude: float, marker_latitude: float,
) -> str:
    """校验拖动坐标位于绑定地块内并持久化；空字符串表示成功。"""
    async with async_session_factory() as db:
        device = (await db.execute(select(HardwareDevice).where(
            HardwareDevice.id == device_id,
            HardwareDevice.plot_id.is_not(None),
        ))).scalar_one_or_none()
        if not device:
            return "设备不存在或尚未绑定地块"
        plot = (await db.execute(select(Plot).where(
            Plot.id == device.plot_id,
        ))).scalar_one_or_none()
        if not plot:
            return "绑定地块不存在"
        point = (marker_longitude, marker_latitude)
        if not point_in_polygon(point, parse_plot_boundary(plot.boundary)):
            return "标记位置必须位于绑定地块内"
        device.marker_longitude = Decimal(str(marker_longitude))
        device.marker_latitude = Decimal(str(marker_latitude))
        device.marker_ratio = None
        await db.commit()
    return ""


async def list_hardware_markers(area_id: int, *, minimal: bool = False) -> list[dict]:
    """返回有效地块内的设备标记，存量边界比例在读取时兼容转换。"""
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(HardwareDevice, Plot.boundary)
            .join(Plot, Plot.id == HardwareDevice.plot_id).where(
                HardwareDevice.area_id == area_id,
                Plot.status == 1,
            ).order_by(HardwareDevice.plot_id, HardwareDevice.id)
        )).all()
    markers = []
    for item, boundary in rows:
        position = resolve_marker_position(
            boundary,
            item.marker_longitude,
            item.marker_latitude,
            float(item.marker_ratio) if item.marker_ratio is not None else None,
        )
        if not position:
            continue
        marker = {
            "id": item.id,
            "device_type": item.device_type,
            "image_url": item.image_url,
            "plot_id": item.plot_id,
            "marker_longitude": position[0],
            "marker_latitude": position[1],
        }
        if not minimal:
            marker.update({"device_name": item.device_name, "nickname": item.nickname})
        markers.append(marker)
    return markers


async def list_farmer_hardware_markers(
    farmer_id: int, area_id: int,
) -> list[dict] | None:
    """校验农户产区归属后返回最小硬件标记；无权访问时返回 None。"""
    async with async_session_factory() as db:
        allowed = (await db.execute(select(AreaFarmer.id).where(
            AreaFarmer.area_id == area_id,
            AreaFarmer.farmer_id == farmer_id,
        ))).scalar_one_or_none()
    if not allowed:
        return None
    return await list_hardware_markers(area_id, minimal=True)
