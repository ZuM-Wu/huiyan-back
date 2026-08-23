"""地图配置 API（管理员端）— 高德 Key/安全密钥/地图开关 读写

密钥落库 hy_configuration（分组 map），源码与种子默认值均为空。
GET 用于地图设置 Tab 回显 + 编辑弹窗注入 Key（仅回传给已登录管理员）。
"""
import logging

from fastapi import APIRouter, Depends, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.config_service import get_config, set_config
from core.log.active_log import active_log
from core.response import ok
from schemas.production_area import MapConfigUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/production-area", tags=["产区管理"])

# 地图配置项列表
_MAP_CONFIG_KEYS = [
    "amap_web_key", "amap_js_security_code", "amap_web_service_key", "production_area_map_enabled",
]
_MAP_CONFIG_DEFAULTS = {
    "amap_web_key": "",
    "amap_js_security_code": "",
    "amap_web_service_key": "",
    "production_area_map_enabled": "1",
}


@router.get("/map-config", dependencies=[Depends(require_permission("area:map_config"))])
async def get_map_config(_: None = Depends(check_admin)):
    """读取地图配置（地图设置 Tab 回显 + 编辑弹窗注入 Key）"""
    result = {}
    for key in _MAP_CONFIG_KEYS:
        val = await get_config(key)
        result[key] = val if val is not None else _MAP_CONFIG_DEFAULTS[key]
    return ok(result)


@router.put("/map-config", dependencies=[Depends(require_permission("area:map_config"))])
async def save_map_config(data: MapConfigUpdate, request: Request, _: None = Depends(check_admin)):
    """保存高德 Key/安全密钥/地图开关"""
    await set_config("amap_web_key", data.amap_web_key)
    await set_config("amap_js_security_code", data.amap_js_security_code)
    await set_config("amap_web_service_key", data.amap_web_service_key)
    await set_config("production_area_map_enabled", data.production_area_map_enabled)
    await active_log("修改产区地图配置", "area_map_config", request=request)
    return ok(msg="地图配置已保存")
