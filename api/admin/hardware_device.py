"""硬件设备管理与管理员产区地图标记 API。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.hardware_device_service import (
    bind_hardware_device,
    get_hardware_device_info,
    list_hardware_devices,
    list_hardware_markers,
    sync_hardware_devices,
    unbind_hardware_device,
    update_device_appearance,
    update_metric_visibility,
    update_marker_position,
)
from core.log.active_log import active_log
from core.rate_limiter import check_rate_detail
from core.response import ok
from core.hardware_realtime_service import (
    get_auto_fetch_settings,
    read_device_identifiers,
    save_auto_fetch_settings,
)
from schemas.hardware_device import (
    HardwareAppearanceUpdate,
    HardwareAutoFetchSettings,
    HardwareBindingUpdate,
    HardwareMarkerUpdate,
    HardwareMetricVisibilityUpdate,
)
from core.hardware_types import HardwareError
from core.hardware_provider import get_device_connection
from core.hardware_catalog import hardware_detail_context, list_hardware_providers

router = APIRouter(prefix="/api/admin/v1/hardware-devices", tags=["硬件设备管理"])
marker_router = APIRouter(
    prefix="/api/admin/v1/production-area", tags=["管理员产区硬件标记"]
)


def _external_error(exc: HardwareError) -> HTTPException:
    """将物联客户端错误映射为标准 HTTP 异常。"""
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/providers", dependencies=[Depends(require_permission("hardware:list"))])
async def provider_catalog(_: None = Depends(check_admin)):
    """返回来源状态、类型目录及经过资源登记校验的详情描述。"""
    return ok({"list": await list_hardware_providers()})


@router.get("/{device_id}/detail-context", dependencies=[Depends(require_permission("hardware:list"))])
async def detail_context(device_id: int, _: None = Depends(check_admin)):
    """公共抽屉仅获得本地设备信息和可用来源的本地组件地址。"""
    context = await hardware_detail_context(device_id)
    if context is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    return ok(context)


@router.get("", dependencies=[Depends(require_permission("hardware:list"))])
async def list_devices(
    provider_id: str = Query("", max_length=64),
    keyword: str = Query("", max_length=128),
    device_type: str = Query("", max_length=32),
    binding_status: str = Query("", pattern="^(|bound|unbound)$"),
    available: int | None = Query(None, ge=0, le=1),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """分页查询本地硬件设备镜像。"""
    return ok(await list_hardware_devices(
        provider_id=provider_id,
        keyword=keyword,
        device_type=device_type,
        binding_status=binding_status,
        available=available,
        page=page,
        size=size,
    ))


@router.post("/sync", dependencies=[Depends(require_permission("hardware:sync"))])
async def sync_devices(request: Request, provider_id: str = Query("", max_length=64), _: None = Depends(check_admin)):
    """显式同步物联平台设备列表。"""
    try:
        result = await sync_hardware_devices(provider_id=provider_id)
    except HardwareError as exc:
        raise _external_error(exc) from exc
    await active_log(
        f"同步硬件设备: 获取{result['received']}台",
        "hardware",
        request=request,
    )
    failures = sum(not item["success"] for item in result["providers"])
    return ok(result, msg=f"设备同步完成，{failures} 个来源失败" if failures else "设备同步完成")


@router.get(
    "/auto-fetch-settings",
    dependencies=[Depends(require_permission("hardware:list"))],
)
async def get_realtime_settings(_: None = Depends(check_admin)):
    """读取全局硬件实时数据自动获取设置。"""
    return ok(await get_auto_fetch_settings())


@router.put(
    "/auto-fetch-settings",
    dependencies=[Depends(require_permission("hardware:sync"))],
)
async def update_realtime_settings(
    data: HardwareAutoFetchSettings,
    request: Request,
    _: None = Depends(check_admin),
):
    """保存设置并即时重排硬件实时数据周期任务。"""
    saved = await save_auto_fetch_settings(data.enabled, data.interval_seconds)
    from services.task.hardware_realtime_worker import (
        apply_hardware_realtime_schedule,
    )
    await apply_hardware_realtime_schedule()
    await active_log(
        "保存硬件自动获取设置: "
        f"开关={'开启' if saved['enabled'] else '关闭'}, "
        f"频率={saved['interval_seconds']}秒",
        "hardware",
        request=request,
    )
    return ok(saved, msg="自动获取设置已保存并生效")


@router.patch(
    "/{device_id}/appearance",
    dependencies=[Depends(require_permission("hardware:update"))],
)
async def update_appearance(
    device_id: int,
    data: HardwareAppearanceUpdate,
    request: Request,
    _: None = Depends(check_admin),
):
    """更新设备自定义图片。"""
    if not await update_device_appearance(device_id, data.image_url):
        raise HTTPException(status_code=404, detail="设备不存在")
    await active_log("更新硬件设备图片", "hardware", device_id, request=request)
    return ok(msg="设备图片已更新")


@router.put(
    "/{device_id}/metric-visibility",
    dependencies=[Depends(require_permission("hardware:update"))],
)
async def update_device_metric_visibility(
    device_id: int,
    data: HardwareMetricVisibilityUpdate,
    request: Request,
    _: None = Depends(check_admin),
):
    """保存设备实时指标卡片显示配置。"""
    identifiers = await update_metric_visibility(device_id, data.identifiers)
    if identifiers is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    await active_log(
        f"更新硬件实时卡片设置: 显示{len(identifiers)}项",
        "hardware",
        device_id,
        request=request,
    )
    return ok(
        {"identifiers": identifiers},
        msg="实时卡片设置已保存",
    )


@router.put(
    "/{device_id}/binding",
    dependencies=[Depends(require_permission("hardware:bind"))],
)
async def bind_device(
    device_id: int,
    data: HardwareBindingUpdate,
    request: Request,
    _: None = Depends(check_admin),
):
    """将设备绑定到指定产区和地块。"""
    error = await bind_hardware_device(device_id, data.area_id, data.plot_id)
    if error:
        status = 404 if error == "设备不存在" else 400
        raise HTTPException(status_code=status, detail=error)
    await active_log("绑定硬件设备到地块", "hardware", device_id, request=request)
    return ok(msg="设备绑定已更新")


@router.delete(
    "/{device_id}/binding",
    dependencies=[Depends(require_permission("hardware:bind"))],
)
async def unbind_device(
    device_id: int, request: Request, _: None = Depends(check_admin),
):
    """解绑设备。"""
    if not await unbind_hardware_device(device_id):
        raise HTTPException(status_code=404, detail="设备不存在")
    await active_log("解绑硬件设备", "hardware", device_id, request=request)
    return ok(msg="设备已解绑")


@router.patch(
    "/{device_id}/marker",
    dependencies=[Depends(require_permission("hardware:bind"))],
)
async def update_marker(
    device_id: int,
    data: HardwareMarkerUpdate,
    request: Request,
    _: None = Depends(check_admin),
):
    """保存管理员拖动后的地块内坐标。"""
    error = await update_marker_position(
        device_id, data.marker_longitude, data.marker_latitude
    )
    if error:
        status = 400 if error == "标记位置必须位于绑定地块内" else 404
        raise HTTPException(status_code=status, detail=error)
    await active_log("调整硬件设备地图位置", "hardware", device_id, request=request)
    return ok(msg="设备位置已更新")


@router.get(
    "/{device_id}/identifiers",
    dependencies=[Depends(require_permission("hardware:data"))],
)
async def list_identifiers(
    device_id: int,
    refresh: bool = Query(False),
    cache_only: bool = Query(False),
    _: None = Depends(check_admin),
):
    """优先返回实时快照，显式刷新时请求物联平台并更新快照。"""
    device = await get_hardware_device_info(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    try:
        data = await read_device_identifiers(
            device["id"],
            device["device_name"],
            refresh=refresh,
            cache_only=cache_only,
        )
    except HardwareError as exc:
        raise _external_error(exc) from exc
    return ok(data)


@router.post(
    "/{device_id}/take-photo",
    dependencies=[Depends(require_permission("hardware:update"))],
)
async def take_device_photo(
    device_id: int,
    request: Request,
    _: None = Depends(check_admin),
):
    """校验设备能力后向所属硬件来源下发拍照指令。"""
    device = await get_hardware_device_info(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if "take_photo" not in device["capabilities"]:
        raise HTTPException(status_code=400, detail="设备不支持拍照")
    if not device["available"]:
        raise HTTPException(status_code=409, detail="设备当前不可用，无法拍照")
    allowed, retry_after = check_rate_detail(
        f"hardware:take-photo:{device_id}",
        limit=1,
        window=10,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"拍照指令发送过于频繁，请在 {retry_after} 秒后重试",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        connection, resolved = await get_device_connection(device_id, "take_photo")
        result = await connection.take_photo(resolved["provider_device_id"])
    except HardwareError as exc:
        raise _external_error(exc) from exc
    await active_log(
        "控制硬件设备拍照",
        "hardware",
        device_id,
        request=request,
    )
    return ok(result, msg="拍照指令已下发")


@router.get(
    "/{device_id}/history",
    dependencies=[Depends(require_permission("hardware:data"))],
)
async def get_history(
    device_id: int,
    identifier: str = Query("", max_length=128),
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """代理获取设备历史数据。"""
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=400, detail="开始时间不能晚于结束时间")
    device = await get_hardware_device_info(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    try:
        connection, resolved = await get_device_connection(device_id, "history")
        data = await connection.get_history(
            resolved["provider_device_id"],
            identifier=identifier,
            start_time=start_time,
            end_time=end_time,
            page=page,
            size=size,
        )
    except HardwareError as exc:
        raise _external_error(exc) from exc
    return ok(data)


@marker_router.get(
    "/{area_id}/hardware-markers",
    dependencies=[Depends(require_permission("area:list"))],
)
async def get_admin_markers(area_id: int, _: None = Depends(check_admin)):
    """返回管理员产区地图上的可拖动硬件标记。"""
    return ok(await list_hardware_markers(area_id))
