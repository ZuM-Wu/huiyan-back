# -*- coding: utf-8 -*-
"""
天气模块数据模型

四张表（一期）：
- WeatherData(hy_weather_data)          产区天气快照表（每产区一行，upsert 最新数据）
- WeatherDaily(hy_weather_daily)        逐日天气历史表（积温计算的数据基础，滚动更新当日极值）
- WeatherAreaBinding(hy_weather_area_binding) 产区数据源绑定表（指定某产区走和风或高德）
- WeatherAlert(hy_weather_alert)        气象灾害预警表（和风官方预警入库，第三方ID去重）

归属核心框架层（plugin=""），数据由 core/weather_service.py 定时拉取写入，
管理端/农户端 API 只读，用户请求永不直穿第三方 API。
"""
from datetime import date as date_type, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class WeatherData(Base):
    """产区天气快照表（每产区仅一行最新快照）"""
    __tablename__ = "hy_weather_data"
    __table_args__ = (
        UniqueConstraint("area_id", name="uk_weather_area"),
        {"comment": "产区天气快照表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="快照ID")
    area_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="产区ID（唯一，每产区一行）")
    source: Mapped[str] = mapped_column(String(64), default="", comment="数据来源插件名（weather_qweather/weather_amap）")
    adcode: Mapped[str] = mapped_column(String(16), default="", comment="高德行政区划码缓存（避免重复逆地理编码）")
    realtime: Mapped[str | None] = mapped_column(Text, comment="实况天气JSON")
    hourly: Mapped[str | None] = mapped_column(Text, comment="24小时逐时预报JSON（高德源为空）")
    forecast: Mapped[str | None] = mapped_column(Text, comment="逐日预报JSON（3~7天）")
    fetch_time: Mapped[datetime | None] = mapped_column(DateTime, comment="最近一次成功拉取时间")
    error_msg: Mapped[str] = mapped_column(String(256), default="", comment="最近一次失败原因（成功时置空，失败保留旧快照）")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class WeatherDaily(Base):
    """逐日天气历史表（积温数据基础，当日极值由实况滚动更新）"""
    __tablename__ = "hy_weather_daily"
    __table_args__ = (
        UniqueConstraint("area_id", "date", name="uk_weather_daily_area_date"),
        {"comment": "逐日天气历史表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="记录ID")
    area_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, default=0, comment="产区ID")
    date: Mapped[date_type] = mapped_column(Date, nullable=False, comment="日期（与产区联合唯一）")
    temp_max: Mapped[float | None] = mapped_column(Float, comment="当日最高温（℃，实况滚动取极值，自上线起积累）")
    temp_min: Mapped[float | None] = mapped_column(Float, comment="当日最低温（℃，同上滚动更新）")
    temp_avg: Mapped[float | None] = mapped_column(Float, comment="日均温（℃，=(max+min)/2，日终定格）")
    humidity: Mapped[float | None] = mapped_column(Float, comment="相对湿度（%）")
    precip: Mapped[float | None] = mapped_column(Float, comment="降水量（mm）")
    wind_scale: Mapped[str] = mapped_column(String(16), default="", comment="风力等级")
    text_day: Mapped[str] = mapped_column(String(64), default="", comment="天气现象文字")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class WeatherAreaBinding(Base):
    """产区数据源绑定表（无绑定则回落全局默认源 weather_source 配置）"""
    __tablename__ = "hy_weather_area_binding"
    __table_args__ = (
        UniqueConstraint("area_id", name="uk_weather_binding_area"),
        {"comment": "产区天气数据源绑定表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="绑定ID")
    area_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="产区ID（唯一）")
    source: Mapped[str] = mapped_column(String(64), default="", comment="指定数据源插件名（weather_qweather/weather_amap）")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class WeatherAlert(Base):
    """气象灾害预警表（一期存和风官方预警；二期系统触发型阈值预警共用）"""
    __tablename__ = "hy_weather_alert"
    __table_args__ = (
        UniqueConstraint("alert_id", name="uk_weather_alert_id"),
        {"comment": "气象灾害预警表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="预警记录ID")
    area_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, default=0, comment="产区ID")
    alert_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="第三方预警唯一ID（去重键）")
    source: Mapped[str] = mapped_column(String(64), default="", comment="来源插件名")
    alert_type: Mapped[str] = mapped_column(String(64), default="", comment="预警类型（台风/暴雨/霜冻等）")
    level: Mapped[str] = mapped_column(String(32), default="", comment="预警等级（蓝色/黄色/橙色/红色）")
    title: Mapped[str] = mapped_column(String(256), default="", comment="预警标题")
    text: Mapped[str | None] = mapped_column(Text, comment="预警详情文本")
    start_time: Mapped[datetime | None] = mapped_column(DateTime, comment="预警生效时间")
    end_time: Mapped[datetime | None] = mapped_column(DateTime, comment="预警结束时间")
    notified: Mapped[int] = mapped_column(Integer, default=0, comment="通知状态: 0=未通知, 1=已通知（定时补推去重标记）")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
