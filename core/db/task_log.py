# -*- coding: utf-8 -*-
"""
任务执行日志模型（核心表，非插件表）
记录所有通过 task_manager 注册的定时任务执行结果
"""
from datetime import datetime
from core.time_utils import china_now
from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class TaskLog(Base):
    """任务执行日志表"""
    __tablename__ = "hy_task_log"
    __table_args__ = {"comment": "任务执行日志表"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="日志ID")
    task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="队列任务ID")
    owner: Mapped[str] = mapped_column(String(64), default="system", comment="任务定义所有者")
    definition: Mapped[str] = mapped_column(String(128), default="", comment="任务定义名称")
    attempt: Mapped[int] = mapped_column(Integer, default=1, comment="本次执行尝试序号")
    correlation_id: Mapped[str] = mapped_column(String(128), default="", comment="业务关联标识")
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="可靠事件ID")
    task_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="任务名称（英文标识）")
    task_desc: Mapped[str] = mapped_column(String(256), default="", comment="任务描述（中文可读）")
    task_type: Mapped[str] = mapped_column(String(32), default="system", comment="任务类型: system/weather/notice/plugin")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="状态: success/failed")
    error_msg: Mapped[str | None] = mapped_column(Text, comment="错误信息（成功时为空）")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, comment="执行耗时（毫秒）")
    start_time: Mapped[datetime | None] = mapped_column(DateTime, comment="开始时间")
    end_time: Mapped[datetime | None] = mapped_column(DateTime, comment="结束时间")
    handle_status: Mapped[int] = mapped_column(Integer, default=0, comment="处理状态: 0=未处理 1=已处理 2=已忽略")
    handled_by: Mapped[int] = mapped_column(Integer, default=0, comment="处理人管理员ID")
    handled_by_name: Mapped[str] = mapped_column(String(64), default="", comment="处理人名称")
    handled_time: Mapped[datetime | None] = mapped_column(DateTime, comment="处理时间")
    handle_note: Mapped[str] = mapped_column(String(256), default="", comment="处理备注")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, comment="手动重试次数")
    is_manual: Mapped[int] = mapped_column(Integer, default=0, comment="是否手动触发: 0=定时 1=手动重试")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
