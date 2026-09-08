"""物联网插件管理员接口，权限与平台接入凭据相互独立。"""

from fastapi import APIRouter, Depends
from core.auth.rbac import require_admin_permission
from core.hardware_management import HardwareConfigUpdate, read_hardware_config, save_hardware_config
from core.log.active_log import active_log
from core.response import ok

router = APIRouter(prefix="/api/admin/v1/plugins/hardware_jjr", tags=["jjr物联网设备管理"])


@router.get("/config", dependencies=[Depends(require_admin_permission("plugin:config"))])
async def get_config():
    return ok(await read_hardware_config("hardware_jjr"))


@router.put("/config", dependencies=[Depends(require_admin_permission("plugin:config"))])
async def save_config(data: HardwareConfigUpdate):
    result = await save_hardware_config("hardware_jjr", data)
    await active_log("更新jjr物联网配置", "hardware_jjr_config")
    return ok(result, msg="配置已保存")
