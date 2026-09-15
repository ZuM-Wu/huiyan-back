# -*- coding: utf-8 -*-
"""产区管理演示插件数据模型。

表名前缀统一为 ``hy_plugin_production_area_management_``，插件私有表只在
安装迁移中创建，系统启动不会自动建表。每个字段都保留中文 COMMENT。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.time_utils import china_now


class ProductionAreaManagementLog(Base):
    """产区日志演示表。"""

    __tablename__ = "hy_plugin_production_area_management_log"
    __table_args__ = {"comment": "产区管理演示插件-日志表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="日志ID")
    area_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="产区ID")
    area_name: Mapped[str] = mapped_column(String(128), default="", comment="产区名称快照")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="日志标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="日志正文")
    stage: Mapped[str] = mapped_column(String(64), default="", comment="作物生育阶段")
    weather_json: Mapped[str] = mapped_column(Text, default="{}", comment="真实天气快照JSON")
    gdd_json: Mapped[str] = mapped_column(Text, default="{}", comment="活动积温与有效积温快照JSON")
    is_latest: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="是否最新生成日志 0否 1是")
    is_seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否演示种子数据 0否 1是")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, index=True, comment="创建时间")


class ProductionAreaManagementTask(Base):
    """产区任务演示表。"""

    __tablename__ = "hy_plugin_production_area_management_task"
    __table_args__ = {"comment": "产区管理演示插件-任务表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="任务ID")
    log_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="来源日志ID")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="任务标题")
    description: Mapped[str] = mapped_column(Text, default="", comment="任务说明")
    ai_summary: Mapped[str] = mapped_column(Text, default="", comment="AI生成依据摘要")
    priority: Mapped[str] = mapped_column(String(16), default="medium", comment="优先级 high/medium/low")
    assignee: Mapped[str] = mapped_column(String(64), default="", comment="负责人")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True, comment="状态 pending/completed")
    plan_time: Mapped[datetime | None] = mapped_column(DateTime, comment="计划执行时间")
    completed_time: Mapped[datetime | None] = mapped_column(DateTime, comment="完成时间")
    mcp_trace: Mapped[str] = mapped_column(Text, default="[]", comment="模拟MCP调用轨迹JSON")
    is_seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否演示种子数据 0否 1是")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class ProductionAreaManagementFeedback(Base):
    """任务执行反馈表。"""

    __tablename__ = "hy_plugin_production_area_management_feedback"
    __table_args__ = {"comment": "产区管理演示插件-反馈表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="反馈ID")
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="任务ID")
    admin_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="反馈管理员ID")
    admin_name: Mapped[str] = mapped_column(String(64), default="", comment="反馈人名称")
    result: Mapped[str] = mapped_column(String(16), nullable=False, comment="反馈结果 success/partial/failed")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="反馈正文")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}", comment="反馈指标快照JSON")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, index=True, comment="反馈时间")
