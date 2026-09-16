# -*- coding: utf-8 -*-
"""产区管理插件的数据模型。

插件只保存由真实产区、天气和管理员反馈形成的事实快照；旧版本的 AI/MCP
字段保留在表结构中用于平滑升级，但不再进入公开接口或页面。
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.time_utils import china_now


class ProductionAreaManagementLog(Base):
    """产区按日事实日志表。"""

    __tablename__ = "hy_plugin_production_area_management_log"
    __table_args__ = {"comment": "产区管理-按日事实日志表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="日志ID")
    area_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="产区ID")
    area_name: Mapped[str] = mapped_column(String(128), default="", comment="产区名称快照")
    fact_date: Mapped[date | None] = mapped_column(Date, index=True, comment="事实日期")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="日志标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="真实事实正文")
    stage: Mapped[str] = mapped_column(String(64), default="", comment="作物生育阶段")
    weather_json: Mapped[str] = mapped_column(Text, default="{}", comment="逐日天气事实JSON")
    gdd_json: Mapped[str] = mapped_column(Text, default="{}", comment="截至事实日期的积温JSON")
    recommendation: Mapped[str] = mapped_column(Text, default="", comment="整改建议")
    is_latest: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="是否最新事实日期 0否 1是")
    is_seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否旧版演示种子数据 0否 1是")
    # 旧版字段保留，升级时不删除历史列，接口不再序列化。
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, index=True, comment="记录生成时间")


class ProductionAreaManagementTask(Base):
    """根据日志整改建议创建的任务表。"""

    __tablename__ = "hy_plugin_production_area_management_task"
    __table_args__ = {"comment": "产区管理-整改任务表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="任务ID")
    log_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="来源日志ID")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="任务标题")
    description: Mapped[str] = mapped_column(Text, default="", comment="整改任务说明")
    # 旧版字段保留，仅用于兼容已有表结构，不再展示 AI 文案。
    ai_summary: Mapped[str] = mapped_column(Text, default="", comment="历史兼容摘要")
    priority: Mapped[str] = mapped_column(String(16), default="medium", comment="优先级 high/medium/low")
    assignee: Mapped[str] = mapped_column(String(64), default="", comment="负责人")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True, comment="状态 pending/completed")
    plan_time: Mapped[datetime | None] = mapped_column(DateTime, comment="计划执行时间")
    completed_time: Mapped[datetime | None] = mapped_column(DateTime, comment="完成时间")
    # 旧版模拟 MCP 轨迹列保留，不再读取或写入页面响应。
    mcp_trace: Mapped[str] = mapped_column(Text, default="[]", comment="历史兼容调用轨迹JSON")
    is_seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否旧版演示种子数据 0否 1是")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class ProductionAreaManagementFeedback(Base):
    """整改任务现场反馈表。"""

    __tablename__ = "hy_plugin_production_area_management_feedback"
    __table_args__ = {"comment": "产区管理-任务反馈表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="反馈ID")
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="任务ID")
    admin_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="反馈管理员ID")
    admin_name: Mapped[str] = mapped_column(String(64), default="", comment="反馈人名称")
    result: Mapped[str] = mapped_column(String(16), nullable=False, comment="反馈结果 success/partial/failed")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="现场文字说明")
    images_json: Mapped[str] = mapped_column(Text, default="[]", comment="现场图片稳定地址JSON数组")
    # 旧版指标字段保留，当前页面不再使用标签编辑。
    metrics_json: Mapped[str] = mapped_column(Text, default="{}", comment="历史兼容指标JSON")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, index=True, comment="反馈时间")
