"""
农户模型
"""

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from core.time_utils import china_now
from core.db.base import Base


def norm_or_none(value):
    """空串归一为 None（phone/email 写入唯一归一入口）

    hy_farmer.phone/email 带唯一索引且以 NULL 表示未绑定，
    写入空字符串会与唯一索引冲突（多条 '' 记录触发 Duplicate entry）。
    """
    value = (value or "").strip()
    return value or None


class Farmer(Base):
    """农户表"""
    __tablename__ = "hy_farmer"
    __table_args__ = {"comment": "农户表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="农户ID")
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="用户名")
    password: Mapped[str] = mapped_column(String(256), nullable=False, comment="密码哈希")
    nickname: Mapped[str] = mapped_column(String(64), default="", comment="昵称")
    avatar: Mapped[str] = mapped_column(String(256), default="", comment="头像URL")
    email: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None, comment="邮箱（NULL=未绑定，带唯一索引）")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None, comment="手机号（NULL=未绑定，带唯一索引）")
    company: Mapped[str] = mapped_column(String(128), default="", comment="公司/农场名")
    address: Mapped[str] = mapped_column(String(256), default="", comment="地址")
    remark: Mapped[str] = mapped_column(String(2048), default="", comment="备注")
    country: Mapped[str] = mapped_column(String(32), default="中国", comment="国家")
    language: Mapped[str] = mapped_column(String(32), default="中文简体", comment="语言")
    status: Mapped[int] = mapped_column(Integer, default=1, comment="状态: 0=禁用, 1=启用")
    last_login_ip: Mapped[str] = mapped_column(String(50), default="", comment="最后登录IP")
    last_action_time: Mapped[datetime | None] = mapped_column(DateTime, comment="最后操作时间")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="注册时间")


class FarmerLogin(Base):
    """农户登录记录"""
    __tablename__ = "hy_farmer_login"
    __table_args__ = {"comment": "农户登录记录"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="记录ID")
    farmer_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="农户ID")
    last_login_ip: Mapped[str] = mapped_column(String(50), default="", comment="登录IP")
    last_action_time: Mapped[datetime | None] = mapped_column(DateTime, comment="最后操作时间")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
