"""
配置管理模型
系统级 KV 键值对存储，插件配置也通过此表管理
"""

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from core.time_utils import china_now
from core.db.base import Base


class ConfigurationModel(Base):
    """配置表"""
    __tablename__ = "hy_configuration"
    __table_args__ = {"comment": "配置管理表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, comment="配置键（如 site_name, oss_method）")
    value: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="配置值")
    description: Mapped[str] = mapped_column(String(256), default="", comment="配置说明")
    group_name: Mapped[str] = mapped_column(String(64), default="basic", comment="配置分组：basic/security/access/plugin 等")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")
