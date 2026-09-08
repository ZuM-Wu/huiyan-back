"""
权限模型
"""

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from core.time_utils import china_now
from core.db.base import Base


class Permission(Base):
    """权限节点树"""
    __tablename__ = "hy_permission"
    __table_args__ = {"comment": "权限节点表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="权限ID")
    title: Mapped[str] = mapped_column(String(128), nullable=False, comment="权限标题(语言键)")
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, comment="权限标识,如 farm:create")
    url: Mapped[str] = mapped_column(String(256), default="", comment="前端页面路径")
    parent_id: Mapped[int] = mapped_column(Integer, default=0, comment="父权限ID, 0=顶级")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序")
    plugin: Mapped[str] = mapped_column(String(64), default="", comment="所属插件,空=系统核心")
    description: Mapped[str] = mapped_column(String(256), default="", comment="描述")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")


class RolePermissionLink(Base):
    """角色↔权限节点关联"""
    __tablename__ = "hy_role_permission_link"
    __table_args__ = {"comment": "角色权限关联表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("hy_admin_role.id"), nullable=False, comment="角色ID")
    permission_id: Mapped[int] = mapped_column(Integer, ForeignKey("hy_permission.id"), nullable=False, comment="权限节点ID")
