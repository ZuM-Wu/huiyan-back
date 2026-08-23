# -*- coding: utf-8 -*-
"""
邮件通知管理员插件 — 数据模型
仅包含任务告警配置表（hy_task_log 已移至核心 core/db/task_log.py）
"""
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime

from core.db.base import Base


class TaskAlertConfig(Base):
    """任务告警配置表（插件表：仅控制邮件通知开关和接收人）"""
    __tablename__ = "hy_task_alert_config"
    __table_args__ = {"comment": "任务告警配置表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="配置ID")
    task_name = Column(String(64), unique=True, nullable=False, comment="任务名称（英文标识）")
    task_title = Column(String(128), default="", comment="任务显示名称（中文）")
    task_type = Column(String(32), default="system", comment="任务类型: system/weather/notice/plugin")
    notify_enabled = Column(Integer, default=0, comment="是否启用邮件通知")
    notify_channel = Column(String(16), default="email", comment="通知渠道: email/sms")
    notify_interface = Column(String(64), default="", comment="通知接口标识（如 mail_smtp）")
    admin_ids = Column(String(256), default="", comment="接收通知的管理员ID列表（逗号分隔）")
    create_time = Column(DateTime, default=datetime.now, comment="创建时间")
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
