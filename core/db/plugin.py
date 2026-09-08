"""
插件注册表模型
"""
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from core.time_utils import china_now
from core.db.base import Base


class PluginModel(Base):
    """插件注册表"""
    __tablename__ = "hy_plugin"
    __table_args__ = {"comment": "插件注册表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="插件唯一标识(snake_case)")
    title: Mapped[str] = mapped_column(String(128), nullable=False, comment="插件显示名称")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0", comment="版本号")
    author: Mapped[str] = mapped_column(String(128), default="", comment="开发者")
    module: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="addon",
        comment="插件类型: addon/gateway/sms/mail/captcha/certification/oauth/oss/server/widget/weather/llm"
    )
    status: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="状态: 0=已安装未启用, 1=已启用, 2=已禁用"
    )
    config: Mapped[str | None] = mapped_column(Text, comment="插件配置(JSON格式)")
    install_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="安装时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")
