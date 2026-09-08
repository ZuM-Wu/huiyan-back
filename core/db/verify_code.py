# -*- coding: utf-8 -*-
"""
验证码存储模型
记录发送的短信/邮件验证码，用于登录、注册、密码重置等场景
"""
from datetime import datetime
from core.time_utils import china_now
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class VerifyCode(Base):
    """验证码表"""
    __tablename__ = "hy_verify_code"
    __table_args__ = {"comment": "验证码表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    target: Mapped[str] = mapped_column(String(128), nullable=False, comment="接收目标（手机号/邮箱）")
    code: Mapped[str] = mapped_column(String(16), nullable=False, comment="验证码明文")
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, comment="用途：login/reset/register")
    channel: Mapped[str] = mapped_column(String(16), nullable=False, comment="渠道：sms/email")
    used: Mapped[int] = mapped_column(Integer, default=0, comment="是否已使用：0=未使用 1=已使用 2=已作废")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, comment="失败尝试计数（累计5次错误自动作废，防爆破）")
    expire_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="过期时间")
    ip: Mapped[str] = mapped_column(String(50), default="", comment="请求IP（审计）")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
