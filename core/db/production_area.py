"""
产区管理数据模型

三级层级：产区(ProductionArea) → 地块(Plot) → 种植批次(PlantingBatch)。
归属核心框架层（plugin=""），管理员为农户建档，农户端只读查看归属自己的数据。

历史 farmer_id/area_id 冗余列保留用于兼容旧数据，但农户与产区的绑定唯一来源为
AreaFarmer；产区/地块采用软停用（status=0）优先，保护未来插件外键不悬空。
"""
from datetime import datetime
from core.time_utils import china_now
from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class ProductionArea(Base):
    """产区表"""
    __tablename__ = "hy_production_area"
    __table_args__ = {"comment": "产区表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="产区ID")
    farmer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, default=0, comment="旧归属农户ID（已退役）")
    name: Mapped[str] = mapped_column(String(128), default="", comment="产区名称")
    code: Mapped[str] = mapped_column(String(64), default="", comment="产区编号")
    province: Mapped[str] = mapped_column(String(64), default="", comment="省")
    city: Mapped[str] = mapped_column(String(64), default="", comment="市")
    district: Mapped[str] = mapped_column(String(64), default="", comment="区/县")
    address: Mapped[str] = mapped_column(String(256), default="", comment="详细地址")
    longitude: Mapped[float] = mapped_column(Float, default=0, comment="中心点经度（供天气插件定位）")
    latitude: Mapped[float] = mapped_column(Float, default=0, comment="中心点纬度（供天气插件定位）")
    boundary: Mapped[str | None] = mapped_column(Text, comment="边界GeoJSON多边形（高德拉框结果）")
    area_size: Mapped[float] = mapped_column(Float, default=0, comment="面积（亩）")
    crop_category: Mapped[str] = mapped_column(String(64), default="", comment="主要作物类别（冗余便于展示）")
    status: Mapped[int] = mapped_column(Integer, default=1, comment="状态: 0=停用, 1=正常")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序")
    description: Mapped[str] = mapped_column(String(512), default="", comment="备注说明")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class Plot(Base):
    """地块表"""
    __tablename__ = "hy_plot"
    __table_args__ = {"comment": "地块表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="地块ID")
    area_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, default=0, comment="所属产区ID")
    farmer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, default=0, comment="归属农户ID（冗余便于过滤）")
    name: Mapped[str] = mapped_column(String(128), default="", comment="地块名称")
    code: Mapped[str] = mapped_column(String(64), default="", comment="地块编号")
    longitude: Mapped[float] = mapped_column(Float, default=0, comment="中心点经度（可选）")
    latitude: Mapped[float] = mapped_column(Float, default=0, comment="中心点纬度（可选）")
    boundary: Mapped[str | None] = mapped_column(Text, comment="边界GeoJSON多边形")
    area_size: Mapped[float] = mapped_column(Float, default=0, comment="面积（亩）")
    soil_type: Mapped[str] = mapped_column(String(64), default="", comment="土壤类型（可选）")
    status: Mapped[int] = mapped_column(Integer, default=1, comment="状态: 0=停用, 1=正常")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序")
    description: Mapped[str] = mapped_column(String(512), default="", comment="备注说明")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class AreaFarmer(Base):
    """产区-农户关联表（多对多绑定）

    一个产区面积大，可绑定多个农户；农户绑定后可查看该产区下的全部地块/批次。
    以本表为绑定唯一来源，ProductionArea.farmer_id 等旧列退役保留（不再作为绑定依据）。
    """
    __tablename__ = "hy_area_farmer"
    __table_args__ = {"comment": "产区-农户关联表（多对多绑定）"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="关联ID")
    area_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, default=0, comment="产区ID")
    farmer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, default=0, comment="农户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="绑定时间")


class PlantingBatch(Base):
    """种植批次表"""
    __tablename__ = "hy_planting_batch"
    __table_args__ = {"comment": "种植批次表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="批次ID")
    plot_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, default=0, comment="所属地块ID")
    area_id: Mapped[int] = mapped_column(Integer, default=0, index=True, comment="所属产区ID（冗余便于按产区聚合）")
    farmer_id: Mapped[int] = mapped_column(Integer, default=0, index=True, comment="归属农户ID（冗余便于过滤）")
    batch_no: Mapped[str] = mapped_column(String(64), default="", comment="批次编号")
    crop_name: Mapped[str] = mapped_column(String(64), default="", comment="作物名称")
    crop_variety: Mapped[str] = mapped_column(String(64), default="", comment="作物品种")
    season: Mapped[str] = mapped_column(String(32), default="", comment="茬口/季别")
    plant_date: Mapped[datetime | None] = mapped_column(DateTime, comment="种植日期")
    expected_harvest_date: Mapped[datetime | None] = mapped_column(DateTime, comment="预计采收日期")
    actual_harvest_date: Mapped[datetime | None] = mapped_column(DateTime, comment="实际采收日期")
    plant_count: Mapped[int] = mapped_column(Integer, default=0, comment="种植株数（摄像头抽样的样本基数）")
    status: Mapped[int] = mapped_column(Integer, default=1, comment="状态: 0=未开始, 1=种植中, 2=已采收, 3=异常")
    description: Mapped[str] = mapped_column(String(512), default="", comment="备注说明")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")
