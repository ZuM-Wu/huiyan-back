"""插件数据库体检状态模型。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.time_utils import china_now


class PluginDatabaseStateModel(Base):
    """保存每个插件最近一次数据库体检快照。"""

    __tablename__ = "hy_plugin_database_state"
    __table_args__ = {
        "comment": "插件数据库体检最新状态表",
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
    }

    plugin_name: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="插件唯一标识"
    )
    module: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", comment="插件模块"
    )
    title: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="插件显示名称"
    )
    db_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", comment="数据库登记插件版本"
    )
    disk_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", comment="磁盘 plugin.json 版本"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unverified",
        comment="总体状态: missing_files/invalid_manifest/not_installed/unverified/awaiting_restart/version_mismatch/local_newer/schema_mismatch/current",
    )
    schema_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unverified",
        comment="结构状态: not_applicable/current/mismatch/unverified",
    )
    report_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", comment="体检报告 JSON"
    )
    expected_schema_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="声明式结构摘要"
    )
    repair_supported: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否提供数据库修复钩子"
    )
    pending_operation_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="待重启计划编号"
    )
    last_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近扫描时间"
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近成功扫描时间"
    )
    last_repair_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近修复成功时间"
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近失败时间"
    )
    error_reason: Mapped[str] = mapped_column(
        String(1024), nullable=False, default="", comment="最近失败原因"
    )

    @classmethod
    def from_report(cls, report: dict) -> "PluginDatabaseStateModel":
        """从服务层报告构建 ORM 行，避免 API 层直接拼接模型字段。"""
        now = china_now()
        return cls(
            plugin_name=str(report.get("plugin_name") or report.get("name") or ""),
            module=str(report.get("module") or ""),
            title=str(report.get("title") or report.get("plugin_name") or ""),
            db_version=str(report.get("db_version") or ""),
            disk_version=str(report.get("disk_version") or ""),
            status=str(report.get("status") or "unverified"),
            schema_status=str(report.get("schema_status") or "unverified"),
            report_json=report.get("report_json", "{}") if isinstance(report.get("report_json"), str) else "{}",
            expected_schema_digest=str(report.get("expected_schema_digest") or ""),
            repair_supported=bool(report.get("repair_supported")),
            pending_operation_id=str(report.get("pending_operation_id") or ""),
            last_scan_at=report.get("last_scan_at") or now,
            last_success_at=report.get("last_success_at"),
            last_repair_at=report.get("last_repair_at"),
            last_failure_at=report.get("last_failure_at"),
            error_reason=str(report.get("error_reason") or ""),
        )
