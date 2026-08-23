"""
管理员模型
"""

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from core.db.base import Base


class Admin(Base):
    """管理员表"""
    __tablename__ = "hy_admin"
    __table_args__ = {"comment": "管理员表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="管理员ID")
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="用户名")
    password: Mapped[str] = mapped_column(String(256), nullable=False, comment="密码哈希")
    nickname: Mapped[str] = mapped_column(String(64), default="", comment="昵称")
    email: Mapped[str] = mapped_column(String(128), default="", comment="邮箱")
    phone: Mapped[str] = mapped_column(String(32), default="", comment="手机号")
    status: Mapped[int] = mapped_column(Integer, default=1, comment="状态: 0=禁用, 1=启用")
    last_login_ip: Mapped[str] = mapped_column(String(50), default="", comment="最后登录IP")
    last_action_time: Mapped[datetime | None] = mapped_column(DateTime, comment="最后操作时间")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")


class AdminLogin(Base):
    """管理员登录记录"""
    __tablename__ = "hy_admin_login"
    __table_args__ = {"comment": "管理员登录记录"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="记录ID")
    admin_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="管理员ID")
    last_login_ip: Mapped[str] = mapped_column(String(50), default="", comment="登录IP")
    last_action_time: Mapped[datetime | None] = mapped_column(DateTime, comment="最后操作时间")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")


class AdminRole(Base):
    """管理员角色"""
    __tablename__ = "hy_admin_role"
    __table_args__ = {"comment": "管理员角色表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="角色ID")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="角色名称")
    description: Mapped[str] = mapped_column(String(256), default="", comment="描述")
    is_system: Mapped[int] = mapped_column(Integer, default=0, comment="是否系统内置")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")


class AdminRoleLink(Base):
    """管理员↔角色关联"""
    __tablename__ = "hy_admin_role_link"
    __table_args__ = {"comment": "管理员角色关联表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="关联ID")
    admin_id: Mapped[int] = mapped_column(Integer, ForeignKey("hy_admin.id"), nullable=False, comment="管理员ID")
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("hy_admin_role.id"), nullable=False, comment="角色ID")
