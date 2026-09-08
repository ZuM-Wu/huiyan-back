"""
管理员挂件配置模型
映射 hy_admin_widget 表，存储每个管理员的仪表盘挂件显示配置（排序+显隐）
"""

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from core.time_utils import china_now
from core.db.base import Base


class AdminWidget(Base):
    """管理员挂件配置表"""
    __tablename__ = "hy_admin_widget"
    __table_args__ = {"comment": "管理员挂件配置表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    admin_id: Mapped[int] = mapped_column(Integer, ForeignKey("hy_admin.id"), nullable=False, comment="管理员ID")
    widgets: Mapped[str] = mapped_column(Text, default="[]", comment="已启用挂件标识的有序JSON数组，如 [\"admin_count\",\"farmer_count\"]")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")
