"""系统日志模型"""

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from core.time_utils import china_now
from core.db.base import Base


class SystemLog(Base):
    """系统操作日志"""
    __tablename__ = "hy_system_log"
    __table_args__ = {"comment": "系统日志表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="日志ID")
    type: Mapped[str] = mapped_column(String(50), default="", comment="操作类型")
    rel_id: Mapped[int] = mapped_column(Integer, default=0, comment="关联ID")
    description: Mapped[str | None] = mapped_column(Text, comment="描述")
    user_type: Mapped[str] = mapped_column(String(20), default="admin", comment="操作人类型: admin/farmer/system/cron")
    user_id: Mapped[int] = mapped_column(Integer, default=0, comment="操作人ID")
    user_name: Mapped[str] = mapped_column(String(100), default="", comment="操作人名称")
    ip: Mapped[str] = mapped_column(String(50), default="", comment="IP地址")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
