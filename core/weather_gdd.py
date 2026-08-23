# -*- coding: utf-8 -*-
"""
天气积温计算与逐日历史定格

从 core/weather_service.py 拆出的农业指标子模块:
- get_accumulated_temp: 按种植批次计算自定植以来的活动/有效积温
- finalize_daily:       每日 23:55 cron 定格当日日均温
- list_daily:           逐日历史查询（管理端图表数据）

积温定义（一期基点温度为系统级配置 weather_gdd_base_temp，默认 10℃）:
- 活动积温: 日均温 >= 基点温度的日均温累计
- 有效积温: Σ(日均温 - 基点温度)，仅统计日均温 >= 基点的日子
- 起算点: 种植批次的定植日期（plant_date），缺失日跳过并标注缺失天数
"""
import logging
from datetime import datetime, date

from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.production_area import PlantingBatch
from core.db.weather import WeatherDaily
from core.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# 积温基点温度默认值（℃）
DEFAULT_GDD_BASE_TEMP = 10.0


def _row_avg(row: WeatherDaily):
    """取日均温: 优先定格值，缺失时按极值现算"""
    if row.temp_avg is not None:
        return row.temp_avg
    if row.temp_max is not None and row.temp_min is not None:
        return (row.temp_max + row.temp_min) / 2
    return None


async def get_accumulated_temp(area_id: int, batch_id: int) -> dict:
    """按种植批次计算自定植以来的活动/有效积温（缺失日跳过）"""
    async with async_session_factory() as db:
        batch = (await db.execute(
            select(PlantingBatch).where(
                PlantingBatch.id == batch_id,
                PlantingBatch.area_id == area_id,
            )
        )).scalar_one_or_none()
        if not batch:
            return {"status": "error", "msg": "种植批次不存在或不属于该产区"}
        if not batch.plant_date:
            return {"status": "error", "msg": "批次未设置种植日期，无法起算积温"}

        base_temp = float(
            await ConfigManager().get("weather_gdd_base_temp", db)
            or DEFAULT_GDD_BASE_TEMP
        )
        start_date = batch.plant_date.date() \
            if isinstance(batch.plant_date, datetime) else batch.plant_date

        rows = (await db.execute(
            select(WeatherDaily).where(
                WeatherDaily.area_id == area_id,
                WeatherDaily.date >= start_date,
            ).order_by(WeatherDaily.date)
        )).scalars().all()

    active_gdd = 0.0     # 活动积温
    effective_gdd = 0.0  # 有效积温
    counted_days = 0
    for row in rows:
        avg = _row_avg(row)
        if avg is None:
            continue
        counted_days += 1
        if avg >= base_temp:
            active_gdd += avg
            effective_gdd += avg - base_temp

    total_days = (date.today() - start_date).days + 1
    return {
        "status": "success",
        "batch_id": batch_id,
        "crop_name": batch.crop_name or "",
        "batch_no": batch.batch_no or "",
        "plant_date": str(start_date),
        "base_temp": base_temp,
        "active_gdd": round(active_gdd, 1),
        "effective_gdd": round(effective_gdd, 1),
        "counted_days": counted_days,
        "missing_days": max(total_days - counted_days, 0),
    }


async def list_daily(area_id: int, start: date | None = None, end: date | None = None) -> list:
    """逐日历史查询（图表数据，支持日期范围，默认全部）"""
    async with async_session_factory() as db:
        query = select(WeatherDaily).where(WeatherDaily.area_id == area_id)
        if start:
            query = query.where(WeatherDaily.date >= start)
        if end:
            query = query.where(WeatherDaily.date <= end)
        rows = (await db.execute(query.order_by(WeatherDaily.date))).scalars().all()
    return [
        {
            "date": str(r.date),
            "temp_max": r.temp_max,
            "temp_min": r.temp_min,
            "temp_avg": r.temp_avg,
            "humidity": r.humidity,
            "precip": r.precip,
            "wind_scale": r.wind_scale or "",
            "text_day": r.text_day or "",
        }
        for r in rows
    ]


async def finalize_daily():
    """定格当日日均温 =(max+min)/2（每日 23:55 cron 调用）"""
    today = date.today()
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(WeatherDaily).where(WeatherDaily.date == today)
        )).scalars().all()
        for row in rows:
            if row.temp_max is not None and row.temp_min is not None:
                row.temp_avg = round((row.temp_max + row.temp_min) / 2, 1)
        await db.commit()
    logger.info(f"[天气服务] 已定格 {len(rows)} 个产区的当日日均温")
    # 返回定格产区数，供 Worker 层组织系统日志文案
    return len(rows)
