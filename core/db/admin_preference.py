"""管理员个人偏好模型。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.time_utils import china_now


class AdminPreference(Base):
    """管理员个人偏好表，按键保存可扩展的 JSON 文本。"""

    __tablename__ = "hy_admin_preference"
    __table_args__ = (
        UniqueConstraint("admin_id", "preference_key", name="uk_admin_preference_key"),
        {"comment": "管理员个人偏好表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="偏好ID")
    admin_id: Mapped[int] = mapped_column(Integer, ForeignKey("hy_admin.id"), nullable=False, comment="管理员ID")
    preference_key: Mapped[str] = mapped_column(String(128), nullable=False, comment="偏好键")
    preference_value: Mapped[str] = mapped_column(Text, nullable=False, default="{}", comment="偏好值JSON")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")
