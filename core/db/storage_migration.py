# -*- coding: utf-8 -*-
"""公共上传文件迁移任务与明细模型。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.time_utils import china_now


class StorageMigrationJob(Base):
    """对象存储迁移任务。"""

    __tablename__ = "hy_storage_migration_job"
    __table_args__ = {"comment": "公共上传文件对象存储迁移任务"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="迁移任务ID")
    target_method: Mapped[str] = mapped_column(String(64), nullable=False, comment="目标存储插件标识")
    phase: Mapped[str] = mapped_column(String(24), nullable=False, default="scanning", comment="阶段: scanning/awaiting/queued/running/finish/partial/failed/empty")
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="文件总数")
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="文件总字节数")
    processed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="已处理文件数")
    processed_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="已处理字节数")
    success_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="成功文件数")
    failed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="失败文件数")
    error_msg: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="任务错误信息")
    task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="任务队列ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class StorageMigrationItem(Base):
    """对象存储迁移任务文件明细。"""

    __tablename__ = "hy_storage_migration_item"
    __table_args__ = (
        # utf8mb4 下 512 字符路径不能直接参与联合唯一索引（旧 InnoDB 上限为 1000 字节）。
        # 保留完整路径用于展示和校验，以固定长度摘要完成同一任务内的唯一定位。
        UniqueConstraint("job_id", "path_hash", name="uk_storage_migration_item_path"),
        Index("idx_storage_migration_item_job_status", "job_id", "status"),
        {"comment": "公共上传文件对象存储迁移明细"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="明细ID")
    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="迁移任务ID")
    local_path: Mapped[str] = mapped_column(String(512), nullable=False, comment="upload/ 下相对路径")
    path_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="本地相对路径SHA-256摘要")
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, comment="目标对象键")
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="文件大小")
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="扫描时文件修改时间")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", comment="状态: pending/running/success/failed/conflict")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="尝试次数")
    error_msg: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="失败原因")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")
