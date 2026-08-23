# -*- coding: utf-8 -*-
"""天气查询服务

封装对 hy_weather_alert 和 hy_weather_data 表的查询操作，
供 api 层和插件层调用。所有函数返回纯 dict/list，不返回 ORM 实例。

农户相关查询按 hy_area_farmer 关联表过滤，仅返回农户绑定产区的数据。

使用方式:
    from core.weather_query_service import list_weather_alerts
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func, and_

from core.db.base import async_session_factory
from core.db.weather import WeatherAlert, WeatherData
from core.db.production_area import ProductionArea, AreaFarmer

logger = logging.getLogger(__name__)

# 等级同义映射: 和风源以英文色值入库，中文筛选需同时命中两种取值
_LEVEL_SYNONYMS = {
    "蓝色": ["蓝色", "blue", "Blue"],
    "黄色": ["黄色", "yellow", "Yellow"],
    "橙色": ["橙色", "orange", "Orange"],
    "红色": ["红色", "red", "Red"],
}


def _alert_to_dict(alert: WeatherAlert, area_name: str = "") -> dict:
    """将 WeatherAlert ORM 实例转为纯字典，并计算生效状态"""
    now = datetime.now()
    is_active = alert.end_time is None or alert.end_time >= now
    return {
        "id": alert.id,
        "area_id": alert.area_id,
        "area_name": area_name,
        "alert_id": alert.alert_id,
        "source": alert.source or "",
        "alert_type": alert.alert_type or "",
        "level": alert.level or "",
        "title": alert.title or "",
        "text": alert.text or "",
        "active": 1 if is_active else 0,
        "start_time": str(alert.start_time) if alert.start_time else "",
        "end_time": str(alert.end_time) if alert.end_time else "",
        "notified": alert.notified,
        "create_time": str(alert.create_time) if alert.create_time else "",
    }


async def list_weather_alerts(
    page: int = 1,
    limit: int = 20,
    area_id: Optional[int] = None,
    level: str = "",
) -> dict:
    """预警分页列表

    参数:
        page: 页码（从 1 开始）
        limit: 每页条数
        area_id: 产区ID筛选（可选）
        level: 预警等级筛选（蓝色/黄色/橙色/红色，支持中英文同义匹配）
    返回:
        {"total": int, "page": int, "limit": int, "list": [dict, ...]}
    """
    filters = []
    if area_id is not None:
        filters.append(WeatherAlert.area_id == area_id)
    if level:
        filters.append(
            WeatherAlert.level.in_(_LEVEL_SYNONYMS.get(level, [level]))
        )

    async with async_session_factory() as db:
        # LEFT JOIN 产区表附带产区名称（产区已删除时名称为空）
        q = (
            select(WeatherAlert, ProductionArea.name)
            .join(
                ProductionArea,
                ProductionArea.id == WeatherAlert.area_id,
                isouter=True,
            )
        )
        count_q = select(func.count(WeatherAlert.id))
        if filters:
            q = q.where(and_(*filters))
            count_q = count_q.where(and_(*filters))

        total = (await db.execute(count_q)).scalar() or 0
        rows = (await db.execute(
            q.order_by(WeatherAlert.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )).all()

    result_list = [
        _alert_to_dict(alert, area_name or "")
        for alert, area_name in rows
    ]
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": result_list,
    }


async def get_weather_alert(alert_id: int) -> Optional[dict]:
    """查询单条预警详情

    参数:
        alert_id: 预警记录ID
    返回:
        预警信息字典，不存在则返回 None
    """
    async with async_session_factory() as db:
        row = (await db.execute(
            select(WeatherAlert, ProductionArea.name)
            .join(
                ProductionArea,
                ProductionArea.id == WeatherAlert.area_id,
                isouter=True,
            )
            .where(WeatherAlert.id == alert_id)
        )).first()
        if not row:
            return None
        alert, area_name = row
        return _alert_to_dict(alert, area_name or "")


async def list_farmer_weather_alerts(
    farmer_id: int,
    page: int = 1,
    limit: int = 20,
) -> dict:
    """农户预警列表（按绑定产区过滤）

    仅返回该农户绑定的产区下的预警记录，按预警ID倒序排列。

    参数:
        farmer_id: 农户ID
        page: 页码（从 1 开始）
        limit: 每页条数
    返回:
        {"total": int, "page": int, "limit": int, "list": [dict, ...]}
    """
    async with async_session_factory() as db:
        # 子查询: 该农户绑定的产区ID集合
        area_ids_subq = select(AreaFarmer.area_id).where(
            AreaFarmer.farmer_id == farmer_id
        )

        q = (
            select(WeatherAlert, ProductionArea.name)
            .join(
                ProductionArea,
                ProductionArea.id == WeatherAlert.area_id,
                isouter=True,
            )
            .where(WeatherAlert.area_id.in_(area_ids_subq))
        )
        count_q = select(func.count(WeatherAlert.id)).where(
            WeatherAlert.area_id.in_(area_ids_subq)
        )

        total = (await db.execute(count_q)).scalar() or 0
        rows = (await db.execute(
            q.order_by(WeatherAlert.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )).all()

    result_list = [
        _alert_to_dict(alert, area_name or "")
        for alert, area_name in rows
    ]
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": result_list,
    }


async def list_farmer_weather_data(farmer_id: int) -> list:
    """农户天气数据列表（按绑定产区过滤）

    返回该农户绑定的所有产区的天气快照数据。

    参数:
        farmer_id: 农户ID
    返回:
        [dict, ...] — 每个产区的天气快照
    """
    async with async_session_factory() as db:
        area_ids_subq = select(AreaFarmer.area_id).where(
            AreaFarmer.farmer_id == farmer_id
        )

        rows = (await db.execute(
            select(WeatherData, ProductionArea.name)
            .join(
                ProductionArea,
                ProductionArea.id == WeatherData.area_id,
                isouter=True,
            )
            .where(WeatherData.area_id.in_(area_ids_subq))
            .order_by(WeatherData.area_id)
        )).all()

    result_list = []
    for wd, area_name in rows:
        result_list.append({
            "id": wd.id,
            "area_id": wd.area_id,
            "area_name": area_name or "",
            "source": wd.source or "",
            "realtime": wd.realtime or "",
            "hourly": wd.hourly or "",
            "forecast": wd.forecast or "",
            "fetch_time": str(wd.fetch_time) if wd.fetch_time else "",
            "error_msg": wd.error_msg or "",
            "update_time": str(wd.update_time) if wd.update_time else "",
        })
    return result_list
