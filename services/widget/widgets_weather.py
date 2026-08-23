# -*- coding: utf-8 -*-
"""
天气模块仪表盘挂件

产区积温挂件（area_gdd）:
- 汇总各接入天气服务的产区最近 30 天逐日日均温与活动/有效积温累计序列
- 前端按产区轮播展示走势图（多产区按顺序滚动切换）
- 累计规则与 core/weather_gdd.py 一致: 日均温 >= 基点温度时累加，缺失日跳过
"""
import logging
from datetime import date, timedelta

from sqlalchemy import select

from services.widget.widget_engine import BaseWidget
from core.db.base import async_session_factory
from core.db.production_area import ProductionArea
from core.db.weather import WeatherDaily
from core.config_manager import ConfigManager
from core.weather_gdd import DEFAULT_GDD_BASE_TEMP, _row_avg

logger = logging.getLogger(__name__)

# 挂件展示的逐日数据窗口（天）
GDD_WIDGET_DAYS = 30


class AreaGddWidget(BaseWidget):
    """产区积温挂件（多产区轮播走势图）"""
    name = "area_gdd"
    title = "产区积温"
    columns = 2
    weight = 48
    widget_type = "chart"

    async def get_data(self) -> dict:
        start_date = date.today() - timedelta(days=GDD_WIDGET_DAYS - 1)
        async with async_session_factory() as db:
            base_temp = float(
                await ConfigManager().get("weather_gdd_base_temp", db)
                or DEFAULT_GDD_BASE_TEMP
            )
            # 只取窗口内有逐日数据且处于启用状态的产区
            rows = (await db.execute(
                select(WeatherDaily, ProductionArea.name)
                 .join(ProductionArea, ProductionArea.id == WeatherDaily.area_id)
                 .where(
                     WeatherDaily.date >= start_date,
                     ProductionArea.status == 1,
                 )
                 .order_by(WeatherDaily.area_id, WeatherDaily.date)
            )).all()

        # 按产区分组，活动/有效积温自窗口首日起累计
        areas = []
        current = None
        for daily, area_name in rows:
            if current is None or current["area_id"] != daily.area_id:
                current = {
                    "area_id": daily.area_id,
                    "area_name": area_name or f"产区{daily.area_id}",
                    "dates": [], "avgs": [], "actives": [], "effectives": [],
                }
                areas.append(current)
            avg = _row_avg(daily)
            active = current["actives"][-1] if current["actives"] else 0.0
            effective = current["effectives"][-1] if current["effectives"] else 0.0
            if avg is not None and avg >= base_temp:
                active += avg
                effective += avg - base_temp
            current["dates"].append(str(daily.date))
            current["avgs"].append(avg)
            current["actives"].append(round(active, 1))
            current["effectives"].append(round(effective, 1))

        return {
            "base_temp": base_temp,
            "days": GDD_WIDGET_DAYS,
            "areas": areas,
        }


def register_weather_widgets(engine):
    """注册天气模块挂件到引擎"""
    engine.register(AreaGddWidget())
    logger.info("[WidgetEngine] 已注册天气模块挂件: area_gdd")
