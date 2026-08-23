"""可靠业务事件 Outbox 模型。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class EventOutbox(Base):
    __tablename__ = "hy_event_outbox"
    __table_args__ = (
        Index("idx_event_outbox_status", "status", "id"),
        Index("idx_event_outbox_name", "event_name", "id"),
        {"comment": "可靠业务事件Outbox表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="事件ID")
    event_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="事件名称")
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="事件契约版本")
    owner: Mapped[str] = mapped_column(String(64), nullable=False, comment="事件发布方")
    payload: Mapped[str] = mapped_column(Text, nullable=False, comment="已校验事件数据JSON")
    correlation_id: Mapped[str] = mapped_column(String(128), default="", comment="业务关联标识")
    status: Mapped[str] = mapped_column(String(20), default="Pending", comment="状态: Pending/Dispatched")
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, comment="分发完成时间")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
