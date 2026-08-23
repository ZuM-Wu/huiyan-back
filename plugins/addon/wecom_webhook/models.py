# -*- coding: utf-8 -*-
"""
企业微信通知插件 — 数据模型

包含管理员通知动作配置和企业微信发送日志表。
"""
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, Text

from core.db.base import Base


class WecomWebhookAction(Base):
    """企业微信管理员通知动作配置。"""
    __tablename__ = "hy_wecom_webhook_action"
    __table_args__ = {"comment": "企业微信通知动作配置表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="配置ID")
    action_key = Column(String(64), unique=True, nullable=False, comment="管理员通知动作标识")
    action_name = Column(String(128), default="", comment="动作名称")
    action_type = Column(String(32), default="other", comment="动作分类")
    enabled = Column(Integer, default=0, comment="是否启用:0否 1是")
    webhook_url = Column(String(512), default="", comment="动作级Webhook地址，留空继承全局")
    create_time = Column(DateTime, default=datetime.now, comment="创建时间")
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class WecomWebhookLog(Base):
    """
    企业微信发送日志表
    记录每次企业微信通知的发送结果
    """
    __tablename__ = "hy_wecom_webhook_log"
    __table_args__ = {"comment": "企业微信发送日志表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="日志ID")
    action_key = Column(String(64), default="", comment="通知动作标识")
    msgtype = Column(String(32), default="template_card", comment="消息类型")
    content = Column(Text, comment="实际发送的消息内容（截断前500字符）")
    status = Column(Integer, default=0, comment="发送状态:0=失败 1=成功")
    error_msg = Column(String(512), default="", comment="错误信息")
    msg_id = Column(String(128), default="", comment="企业微信返回的消息ID")
    create_time = Column(DateTime, default=datetime.now, comment="发送时间")
