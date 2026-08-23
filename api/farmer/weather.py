# -*- coding: utf-8 -*-
"""农户端天气只读 API

农户仅可查询绑定给自己（hy_area_farmer.farmer_id = request.state.user_id）
产区的天气数据；数据全部来自快照/历史表与缓存，用户请求永不直穿第三方 API。

接口:
- GET /api/v1/weather?area_id=            实况+逐时+预报+生效预警聚合
- GET /api/v1/weather/gdd?area_id=&batch_id=  批次积温统计
- GET /api/v1/weather/daily?area_id=&start=&end=  产区逐日天气历史
"""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_farmer
from core.weather_service import weather_service
from core.production_area_service import batch_belongs_to_area, get_area_ids_by_farmer
from core import weather_gdd
from core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/weather", tags=["农户天气"])


async def _check_area_access(area_id: int, farmer_id: int):
    """校验产区存在且已绑定当前农户"""
    area_ids = await get_area_ids_by_farmer(farmer_id)
    if area_id not in area_ids:
        raise HTTPException(status_code=404, detail="产区不存在或无权访问")


@router.get("")
async def get_area_weather(
    request: Request,
    area_id: int = Query(..., description="产区ID"),
    _: None = Depends(check_farmer),
):
    """产区天气聚合（实况 + 24h逐时 + 预报 + 生效预警），只读快照/缓存"""
    farmer_id = request.state.user_id
    await _check_area_access(area_id, farmer_id)
    data = await weather_service.get_weather(area_id)
    data["alerts"] = await weather_service.list_active_alerts(area_id)
    return ok(data)


@router.get("/gdd")
async def get_batch_gdd(
    request: Request,
    area_id: int = Query(..., description="产区ID"),
    batch_id: int = Query(..., description="种植批次ID"),
    _: None = Depends(check_farmer),
):
    """批次积温统计（活动/有效积温，自定植日起算，缺失日跳过并标注）"""
    farmer_id = request.state.user_id
    await _check_area_access(area_id, farmer_id)
    if not await batch_belongs_to_area(batch_id, area_id):
        raise HTTPException(status_code=404, detail="种植批次不存在")
    result = await weather_gdd.get_accumulated_temp(area_id, batch_id)
    return ok(result)


@router.get("/daily")
async def get_area_daily(
    request: Request,
    area_id: int = Query(..., description="产区ID"),
    start: date = Query(None, description="起始日期 YYYY-MM-DD"),
    end: date = Query(None, description="结束日期 YYYY-MM-DD"),
    _: None = Depends(check_farmer),
):
    """产区逐日天气历史（每日最高/最低/均温/湿度/降水/风力/天气文字，支持日期范围）"""
    farmer_id = request.state.user_id
    await _check_area_access(area_id, farmer_id)
    rows = await weather_gdd.list_daily(area_id, start, end)
    return ok({"list": rows})
