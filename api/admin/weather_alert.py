# -*- coding: utf-8 -*-
"""
天气预警记录 API（管理员端）

功能: 全量预警记录分页列表（产区/等级/类型/生效状态/日期范围多条件筛选），
供后台天气服务页「预警记录」Tab（/admin/weather?tab=alerts）使用。

独立成文件原因: api/admin/weather.py 已逼近单文件 400 行红线，预警记录
查询与天气服务配置职责可分离，故独立路由文件。

权限: weather:view（沿用天气服务查询类权限码）
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/weather/alerts", tags=["天气预警记录"])

# 等级同义映射: 和风源以英文色值入库（yellow/orange 等），前端筛选传中文，
# 两种存储取值都需命中
_LEVEL_SYNONYMS = {
    "蓝色": ["蓝色", "blue", "Blue"],
    "黄色": ["黄色", "yellow", "Yellow"],
    "橙色": ["橙色", "orange", "Orange"],
    "红色": ["红色", "red", "Red"],
}


def _build_filters(area_id, level, alert_type, active, start_date, end_date):
    """
    组装预警记录筛选条件列表

    参数含义与 list_alerts 端点一致；日期解析失败抛 400。
    返回: SQLAlchemy 条件表达式列表
    """
    from core.db.weather import WeatherAlert
    now = datetime.now()
    filters = []
    if area_id is not None:
        filters.append(WeatherAlert.area_id == area_id)
    if level:
        # 中文等级扩展为同义集合匹配，非预置等级原样精确匹配
        filters.append(WeatherAlert.level.in_(_LEVEL_SYNONYMS.get(level, [level])))
    if alert_type:
        filters.append(WeatherAlert.alert_type == alert_type)
    # 生效状态: 1=生效中（无结束时间视为持续生效）/ 0=已结束
    if active == "1":
        filters.append(
            (WeatherAlert.end_time.is_(None)) | (WeatherAlert.end_time >= now)
        )
    elif active == "0":
        filters.append(WeatherAlert.end_time < now)
    # 日期范围按入库时间 create_time 过滤，结束日包含当天
    if start_date:
        try:
            filters.append(
                WeatherAlert.create_time >= datetime.strptime(start_date, "%Y-%m-%d")
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="开始日期格式错误，应为 YYYY-MM-DD")
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            filters.append(WeatherAlert.create_time < end_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="结束日期格式错误，应为 YYYY-MM-DD")
    return filters


@router.get("/list", dependencies=[Depends(require_permission("weather:view"))])
async def list_alerts(
    area_id: int = Query(None, description="产区ID筛选"),
    level: str = Query("", description="预警等级筛选（蓝色/黄色/橙色/红色）"),
    alert_type: str = Query("", description="预警类型筛选（台风/暴雨/霜冻等）"),
    active: str = Query("", description="生效状态: 空=全部 1=生效中 0=已结束"),
    start_date: str = Query("", description="开始日期 YYYY-MM-DD（按入库时间）"),
    end_date: str = Query("", description="结束日期 YYYY-MM-DD（含当天）"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
    _: None = Depends(check_admin),
):
    """预警记录列表 — 全量预警分页查询，支持多条件筛选"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea
    from core.db.weather import WeatherAlert
    filters = _build_filters(area_id, level, alert_type, active, start_date, end_date)

    async with async_session_factory() as db:
        # LEFT JOIN 产区表附带产区名称（产区已删除时名称为空）
        q = (
            select(WeatherAlert, ProductionArea.name)
            .join(ProductionArea, ProductionArea.id == WeatherAlert.area_id, isouter=True)
        )
        count_q = select(func.count(WeatherAlert.id))
        if filters:
            q = q.where(and_(*filters))
            count_q = count_q.where(and_(*filters))

        total = (await db.execute(count_q)).scalar() or 0
        rows = (await db.execute(
            q.order_by(WeatherAlert.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).all()

    now = datetime.now()
    result_list = []
    for alert, area_name in rows:
        # 生效状态由后端统一判定，前端直接渲染标签
        is_active = alert.end_time is None or alert.end_time >= now
        result_list.append({
            "id": alert.id,
            "area_id": alert.area_id,
            "area_name": area_name or "",
            "source": alert.source or "",
            "alert_type": alert.alert_type or "",
            "level": alert.level or "",
            "title": alert.title or "",
            "text": alert.text or "",
            "active": 1 if is_active else 0,
            "start_time": str(alert.start_time) if alert.start_time else "",
            "end_time": str(alert.end_time) if alert.end_time else "",
            "create_time": str(alert.create_time) if alert.create_time else "",
        })

    return ok({"total": total, "page": page, "limit": limit, "list": result_list})
