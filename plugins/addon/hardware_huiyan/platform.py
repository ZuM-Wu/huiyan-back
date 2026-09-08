"""学习测试设备公开接口，无需 Token；启停由平台路由门禁管理。"""

from fastapi import APIRouter, HTTPException
from core.hardware_types import HardwareError
from core.response import ok
from .schemas import DeviceRegisterRequest, DeviceReportRequest
from . import service


router = APIRouter(prefix="/api/v1/huiyan-iot", tags=["慧眼物联网平台"])


@router.post("/devices/register")
async def register_device(data: DeviceRegisterRequest):
    try:
        return ok(await service.register_device(data), msg="设备注册成功")
    except HardwareError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/devices")
async def list_devices():
    return ok(await service.list_registered_devices())


@router.get("/devices/{device_id}")
async def get_device_detail(device_id: str):
    return ok(await service.read_registered_device(device_id))


@router.post("/devices/{device_id}/report")
async def report_device_data(device_id: str, data: DeviceReportRequest):
    try:
        return ok(await service.report_device_data(device_id, data), msg="数据上报成功")
    except HardwareError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/devices/{device_id}/realtime")
async def read_device_realtime(device_id: str):
    return ok(await service.read_device_realtime(device_id))
