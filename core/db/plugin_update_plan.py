"""插件更新计划持久化模型。"""

from datetime import datetime
from core.time_utils import china_now
from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class PluginUpdatePlanModel(Base):
    """记录插件更新预检、确认和重启应用状态。"""

    __tablename__ = "hy_plugin_update_plan"
    __table_args__ = {"comment": "插件更新计划表"}

    operation_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="更新操作唯一标识"
    )
    plugin_name: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="插件唯一标识"
    )
    current_version: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="确认时的当前运行版本"
    )
    target_version: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="待应用目标版本"
    )
    package_ref: Mapped[str] = mapped_column(
        String(512), nullable=False, default="", comment="规范化暂存插件包路径"
    )
    package_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="暂存插件包 SHA-256 摘要"
    )
    package_module: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", comment="插件所属模块目录"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="计划状态: prepared/awaiting_restart/applying/applied/cancelled/failed"
    )
    restart_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否必须重启后生效"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=china_now, comment="计划创建时间"
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="计划确认时间"
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="计划应用完成时间"
    )
    error_reason: Mapped[str] = mapped_column(
        String(1024), nullable=False, default="", comment="失败原因"
    )
    identity: Mapped[str] = mapped_column(
        String(128), nullable=False, default="system", comment="创建计划的身份"
    )
    confirmed_by: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="确认计划的身份"
    )
    task_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="预检任务编号"
    )
    operation_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="upgrade", comment="操作类型: upgrade/repair"
    )
    diagnostics_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", comment="体检报告摘要 JSON"
    )
