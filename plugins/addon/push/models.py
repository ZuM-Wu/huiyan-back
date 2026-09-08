# -*- coding: utf-8 -*-
"""
手动推送插件数据模型

表名约定：hy_push_task / hy_push_log
ORM 模型仅用于业务代码中的查询与序列化，不参与系统启动时的 create_all。
实际建表由 plugin.install() → migrations/install.sql 完成。
"""
from core.time_utils import china_now
from sqlalchemy import Column, DateTime, Integer, BigInteger, String, Text, JSON, SmallInteger

from core.db.base import Base


class PushTask(Base):
    """推送任务表"""

    __tablename__ = "hy_push_task"
    __table_args__ = {"comment": "推送任务表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="任务ID")
    title = Column(String(128), nullable=False, comment="推送标题")
    keywords = Column(String(256), default="", comment="关键词/备注")
    type = Column(SmallInteger, nullable=False, default=1, comment="通知形式:1=站内信 2=短信+邮件")
    sms_template_id = Column(Integer, default=0, comment="短信模板ID")
    email_template_id = Column(Integer, default=0, comment="邮件模板ID")
    subject = Column(String(256), default="", comment="邮件标题")
    content = Column(Text, comment="推送内容(HTML/纯文本)")
    push_target = Column(JSON, nullable=False, comment="目标条件配置JSON")
    target_count = Column(Integer, default=0, comment="符合条件用户数")
    repeat_send = Column(SmallInteger, default=0, comment="允许重复发送:0否 1是")
    send_cycle = Column(String(32), default="onetime", comment="推送周期:onetime/day/week/month")
    week_day = Column(SmallInteger, default=0, comment="每周几(1-7)")
    month_day = Column(SmallInteger, default=0, comment="每月几号(1-28)")
    time_hour_min = Column(String(8), default="09:00", comment="发送时间HH:mm")
    push_start_time = Column(DateTime, comment="有效期开始")
    push_end_time = Column(DateTime, comment="有效期结束")
    send_num = Column(Integer, default=0, comment="总发送次数")
    success_num = Column(Integer, default=0, comment="成功次数")
    fail_num = Column(Integer, default=0, comment="失败次数")
    last_exec_time = Column(DateTime, comment="上次执行时间")
    status = Column(String(16), default="Wait", comment="状态:Wait/Exec/Suspended/Expired/Finish")
    admin_id = Column(Integer, default=0, comment="创建人ID")
    create_time = Column(DateTime, default=china_now, comment="创建时间")
    update_time = Column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class PushLog(Base):
    """推送日志表"""

    __tablename__ = "hy_push_log"
    __table_args__ = {"comment": "推送日志表"}

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="日志ID")
    task_id = Column(Integer, nullable=False, index=True, comment="任务ID")
    farmer_id = Column(Integer, nullable=False, index=True, comment="农户ID")
    username = Column(String(128), default="", comment="农户用户名")
    type = Column(String(16), nullable=False, comment="通知类型:sms/email/inbox")
    status = Column(String(16), default="Pending", comment="状态:Pending/Success/Failed")
    reason = Column(Text, comment="失败原因")
    create_time = Column(DateTime, default=china_now, comment="创建时间")


class PushCenterTask(Base):
    """推送中心任务表（2.0）。旧 PushTask 仅用于历史表兼容。"""

    __tablename__ = "hy_push_center_task"
    __table_args__ = {"comment": "推送中心任务表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="任务ID")
    title = Column(String(128), nullable=False, comment="推送标题")
    keywords = Column(String(256), default="", comment="关键词/备注")
    content = Column(Text, default="", comment="推送内容(HTML/纯文本)")
    subject = Column(String(256), default="", comment="邮件标题")
    channels = Column(JSON, nullable=False, comment="渠道配置JSON")
    target_rule = Column(JSON, nullable=False, comment="目标规则JSON")
    schedule_rule = Column(JSON, nullable=False, comment="调度规则JSON")
    target_count = Column(Integer, default=0, comment="目标农户数")
    send_num = Column(Integer, default=0, comment="投递总数")
    success_num = Column(Integer, default=0, comment="成功数")
    fail_num = Column(Integer, default=0, comment="失败数")
    last_exec_time = Column(DateTime, comment="上次执行时间")
    status = Column(String(16), default="Wait", comment="状态:Draft/Wait/Exec/Suspended/Finish/Expired")
    admin_id = Column(Integer, default=0, comment="创建人ID")
    create_time = Column(DateTime, default=china_now, comment="创建时间")
    update_time = Column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class PushCenterDeliveryLog(Base):
    """推送中心渠道投递日志表。"""

    __tablename__ = "hy_push_center_delivery_log"
    __table_args__ = {"comment": "推送中心投递日志表"}

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="日志ID")
    task_id = Column(Integer, nullable=False, index=True, comment="推送中心任务ID")
    farmer_id = Column(Integer, nullable=False, index=True, comment="农户ID")
    username = Column(String(128), default="", comment="农户用户名")
    channel = Column(String(16), nullable=False, comment="渠道:inbox/sms/email")
    status = Column(String(16), default="Pending", comment="状态:Pending/Success/Failed")
    reason = Column(Text, comment="失败原因")
    notification_log_id = Column(BigInteger, comment="通知中心日志ID")
    create_time = Column(DateTime, default=china_now, comment="创建时间")
