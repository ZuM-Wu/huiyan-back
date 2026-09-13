"""物联网插件管理员接口；公开设备接入不影响后台权限校验。"""

from fastapi import APIRouter, Depends, HTTPException
from core.auth.rbac import require_admin_permission
from core.hardware_management import HardwareConfigUpdate, read_hardware_config, save_hardware_config
from core.log.active_log import active_log
from core.response import ok
from core.hardware_types import HardwareError
from .schemas import AdminDeviceRegisterRequest
from . import service

router = APIRouter(prefix="/api/admin/v1/plugins/hardware_huiyan", tags=["慧眼物联网设备管理"])


@router.get("/config", dependencies=[Depends(require_admin_permission("plugin:config"))])
async def get_config():
    return ok(await read_hardware_config("hardware_huiyan"))


@router.put("/config", dependencies=[Depends(require_admin_permission("plugin:config"))])
async def save_config(data: HardwareConfigUpdate):
    result = await save_hardware_config("hardware_huiyan", data)
    await active_log("更新慧眼物联网配置", "hardware_huiyan_config")
    return ok(result, msg="配置已保存")


@router.post("/devices/register", dependencies=[Depends(require_admin_permission("hardware:sync"))])
async def register_device(data: AdminDeviceRegisterRequest):
    try:
        result = await service.register_device(data, replace_existing=data.replace_existing)
    except HardwareError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    await active_log("注册慧眼物联网设备", "hardware_huiyan_register")
    return ok(result, msg="设备注册成功")


@router.get("/devices/{device_id}", dependencies=[Depends(require_admin_permission("hardware:list"))])
async def device_detail(device_id: str):
    return ok(await service.read_registered_device(device_id))


@router.delete("/devices/{device_id}", dependencies=[Depends(require_admin_permission("hardware:sync"))])
async def delete_device(device_id: str):
    try:
        result = await service.delete_registered_device(device_id)
    except HardwareError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    await active_log(f"删除慧眼物联网设备：{device_id}", "hardware_huiyan_delete")
    return ok(result, msg="设备已删除")
