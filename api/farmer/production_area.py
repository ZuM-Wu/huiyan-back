"""农户端产区只读 API

农户只能查看绑定给自己（hy_area_farmer.farmer_id = request.state.user_id）的产区；
产区与农户为多对多，农户绑定产区后可见该产区下全部地块/批次。
无任何编辑操作，无地图设置。三级下钻：产区列表 → 地块列表 → 批次列表。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from core.auth.middleware_chain import check_farmer
from core.config_service import get_config
from core.production_area_service import (
    list_farmer_area_page, list_farmer_batches, list_farmer_plots,
)
from core.hardware_device_service import list_farmer_hardware_markers
from core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/production-area", tags=["农户产区"])


@router.get("/list")
async def list_my_areas(
    request: Request,
    keywords: str = Query("", description="搜索关键词"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    _: None = Depends(check_farmer),
):
    """当前农户的产区列表（只读，仅已绑定自己的产区，绑定来源 hy_area_farmer）"""
    farmer_id = request.state.user_id
    return ok(await list_farmer_area_page(farmer_id, keywords, page, limit))


@router.get("/map-config")
async def get_farmer_map_config(_: None = Depends(check_farmer)):
    """农户端地图配置（只读）：仅下发浏览器端需要的高德 Web JS Key + 安全密钥 + 地图开关

    安全红线：绝不读取/回传高德 Web 服务 Key（服务端专用密钥只能留在服务端）。
    注：本路由必须注册在 /{area_id}/plots 之前，避免被路径参数路由捕获。
    """
    web_key = await get_config("amap_web_key") or ""
    security_code = await get_config("amap_js_security_code") or ""
    enabled_raw = await get_config("production_area_map_enabled")
    # 开关默认开启（与管理端默认值 "1" 一致），仅显式配置 "0" 时关闭
    enabled = (enabled_raw if enabled_raw is not None else "1") != "0"
    return ok({
        "amap_web_key": web_key,
        "amap_js_security_code": security_code,
        "enabled": enabled,
    })


@router.get("/{area_id}/plots")
async def list_my_plots(area_id: int, request: Request,
                        _: None = Depends(check_farmer)):
    """指定产区下的地块列表（校验该产区已绑定当前农户，返回产区下全部地块）"""
    data = await list_farmer_plots(request.state.user_id, area_id)
    if data is None:
        raise HTTPException(status_code=404, detail="产区不存在或无权访问")
    return ok(data)


@router.get("/{area_id}/hardware-markers")
async def list_my_hardware_markers(
    area_id: int, request: Request, _: None = Depends(check_farmer),
):
    """返回当前农户有权访问产区的只读硬件标记最小字段。"""
    markers = await list_farmer_hardware_markers(request.state.user_id, area_id)
    if markers is None:
        raise HTTPException(status_code=404, detail="产区不存在或无权访问")
    return ok(markers)


@router.get("/plot/{plot_id}/batches")
async def list_my_batches(plot_id: int, request: Request,
                          _: None = Depends(check_farmer)):
    """指定地块下的种植批次列表（校验地块所属产区已绑定当前农户）"""
    data = await list_farmer_batches(request.state.user_id, plot_id)
    if data is None:
        raise HTTPException(status_code=404, detail="地块不存在或无权访问")
    return ok(data)
