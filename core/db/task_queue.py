# -*- coding: utf-8 -*-
"""
任务队列表模型（核心表，非插件表）
持久化存储运行时提交的异步任务，由 TaskQueueWorker 轮询消费
参考 ZJMF task_wait 表设计：纯 MySQL + 乐观锁，无 Redis 依赖
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Computed, DateTime, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import BINARY
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class TaskQueue(Base):
    """任务队列表"""
    __tablename__ = "hy_task_queue"
    __table_args__ = (
        Index("idx_status_retry", "status", "retry"),
        Index("idx_status_run_at", "status", "run_at"),
        Index("idx_queue_claim", "status", "next_run_at", "priority", "id"),
        Index("idx_queue_owner", "owner", "status"),
        UniqueConstraint("idempotency_digest", name="uq_task_idempotency"),
        Index("idx_priority", "priority"),
        Index("idx_create_time", "create_time"),
        {"comment": "任务队列表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="任务ID")
    type: Mapped[str] = mapped_column(String(64), nullable=False, comment="任务类型: notice/hook/自定义")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Wait",
                                        comment="状态: Wait/Exec/Finish/Paused/Cancelled/Dead")
    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="任务定义所有者")
    definition: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="任务定义名称")
    group_name: Mapped[str] = mapped_column(String(64), nullable=False, default="default", comment="并发隔离分组")
    priority: Mapped[int] = mapped_column(Integer, default=0, comment="优先级(数字越小越优先)")
    retry: Mapped[int] = mapped_column(Integer, default=0, comment="已重试次数")
    max_retry: Mapped[int] = mapped_column(Integer, default=3, comment="最大重试次数")
    attempt: Mapped[int] = mapped_column(Integer, default=0, comment="已执行尝试次数")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, comment="最大执行尝试次数")
    idempotency_key: Mapped[str | None] = mapped_column(String(191), nullable=True, comment="提交方显式幂等键")
    idempotency_digest: Mapped[bytes | None] = mapped_column(
        BINARY(32),
        Computed(
            "IF(idempotency_key IS NULL, NULL, "
            "UNHEX(SHA2(CONCAT(definition, CHAR(0), idempotency_key), 256)))",
            persisted=True,
        ),
        nullable=True,
        comment="任务定义与幂等键SHA-256摘要",
    )
    correlation_id: Mapped[str] = mapped_column(String(128), default="", comment="业务关联标识")
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="可靠事件ID")
    task_data: Mapped[str] = mapped_column(Text, nullable=False, comment="任务数据JSON")
    description: Mapped[str] = mapped_column(String(500), default="", comment="任务描述")
    version: Mapped[int] = mapped_column(Integer, default=0, comment="版本号(乐观锁)")
    error_msg: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    start_time: Mapped[datetime | None] = mapped_column(DateTime, comment="开始执行时间")
    finish_time: Mapped[datetime | None] = mapped_column(DateTime, comment="完成时间")
    run_at: Mapped[datetime | None] = mapped_column(DateTime, comment="计划执行时间，为空时立即执行")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, comment="下次允许执行时间")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, comment="最近抢占时间")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
