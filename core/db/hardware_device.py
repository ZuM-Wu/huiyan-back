"""物联硬件设备本地镜像模型。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.time_utils import china_now


class HardwareDevice(Base):
    """统一物联平台设备及其本地展示、地块绑定配置。"""

    __tablename__ = "hy_hardware_device"
    __table_args__ = (
        UniqueConstraint("provider_id", "provider_device_id", name="uk_hardware_provider_device"),
        CheckConstraint(
            "marker_ratio IS NULL OR (marker_ratio >= 0 AND marker_ratio <= 1)",
            name="ck_hardware_device_marker_ratio",
        ),
        CheckConstraint(
            "(marker_longitude IS NULL AND marker_latitude IS NULL) OR "
            "(marker_longitude BETWEEN -180 AND 180 AND "
            "marker_latitude BETWEEN -90 AND 90)",
            name="ck_hardware_device_marker_coordinates",
        ),
        {"comment": "物联硬件设备本地镜像表"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="设备本地主键"
    )
    provider_id: Mapped[str] = mapped_column(String(64), default="hardware_jjr", index=True, comment="硬件来源插件标识")
    provider_device_id: Mapped[str] = mapped_column(String(128, collation="utf8mb4_bin"), default="", comment="来源内稳定设备ID")
    provider_title: Mapped[str] = mapped_column(String(128), default="JJR", comment="来源名称快照")
    capabilities: Mapped[list] = mapped_column(JSON, default=list, comment="设备支持的操作能力")

    external_id: Mapped[int] = mapped_column(
        Integer, default=0, comment="物联平台设备ID"
    )
    device_name: Mapped[str] = mapped_column(
        String(128), index=True, comment="物联平台设备编号"
    )
    device_type: Mapped[str] = mapped_column(
        String(32), default="", index=True, comment="设备类型编码"
    )
    device_type_label: Mapped[str] = mapped_column(
        String(64), default="", comment="设备类型名称"
    )
    nickname: Mapped[str] = mapped_column(
        String(128), default="", comment="物联平台设备名称"
    )
    bot_id: Mapped[int] = mapped_column(
        Integer, default=0, comment="物联平台机器人ID"
    )
    external_latitude: Mapped[str] = mapped_column(
        String(32), default="", comment="物联平台纬度快照"
    )
    external_longitude: Mapped[str] = mapped_column(
        String(32), default="", comment="物联平台经度快照"
    )
    external_address: Mapped[str] = mapped_column(
        String(256), default="", comment="物联平台地址快照"
    )
    image_url: Mapped[str] = mapped_column(
        String(512), default="", comment="设备自定义图片URL"
    )
    visible_metric_identifiers: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        comment="实时卡片显示标识列表，NULL表示使用有值指标，空数组表示全部隐藏",
    )
    area_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("hy_production_area.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="绑定产区ID",
    )
    plot_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("hy_plot.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="绑定地块ID",
    )
    marker_ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 8), nullable=True, comment="地块外边界周长位置比例0到1"
    )
    marker_longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(11, 8), nullable=True, comment="硬件标记在绑定地块内的经度"
    )
    marker_latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 8), nullable=True, comment="硬件标记在绑定地块内的纬度"
    )
    available: Mapped[int] = mapped_column(
        Integer, default=1, index=True, comment="平台可用状态: 0=不可用, 1=可用"
    )
    last_sync_error: Mapped[str] = mapped_column(
        Text, default="", comment="最近同步错误"
    )
    last_sync_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近同步时间（中国时间）"
    )
    create_time: Mapped[datetime] = mapped_column(
        DateTime, default=china_now, comment="创建时间（中国时间）"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime,
        default=china_now,
        onupdate=china_now,
        comment="更新时间（中国时间）",
    )


class HardwareRealtimeSnapshot(Base):
    """每台物联设备最近一次成功的实时数据快照。"""

    __tablename__ = "hy_hardware_realtime_snapshot"
    __table_args__ = (
        {
            "comment": "物联硬件实时数据最新快照表",
            # 设备删除需与快照清理同事务，且外键要求 InnoDB，显式声明引擎。
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
        },
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="快照主键"
    )
    device_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("hy_hardware_device.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="硬件设备本地主键",
    )
    payload: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, comment="最近成功的实时数据JSON"
    )
    last_attempt_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近尝试获取时间（中国时间）"
    )
    last_success_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近成功获取时间（中国时间）"
    )
    last_error: Mapped[str] = mapped_column(
        Text, default="", comment="最近获取错误"
    )
    create_time: Mapped[datetime] = mapped_column(
        DateTime, default=china_now, comment="创建时间（中国时间）"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime,
        default=china_now,
        onupdate=china_now,
        comment="更新时间（中国时间）",
    )
