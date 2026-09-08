"""
实名认证数据模型
包含认证记录表和认证渠道表
"""
from datetime import datetime
from core.time_utils import china_now
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class CertificationRecord(Base):
    """实名认证记录表"""
    __tablename__ = "hy_certification_record"
    __table_args__ = {"comment": "实名认证记录表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="记录ID")
    farmer_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="农户ID")
    real_name: Mapped[str] = mapped_column(String(64), default="", comment="实名名称")
    id_card: Mapped[str] = mapped_column(String(18), default="", comment="身份证号")
    cert_type: Mapped[str] = mapped_column(String(20), default="personal", comment="认证类型: personal=个人")
    cert_no: Mapped[str] = mapped_column(String(128), default="", comment="认证ID（第三方返回或系统生成）")
    certify_url: Mapped[str] = mapped_column(String(512), default="", comment="第三方认证链接（待认证期间供用户重新获取）")
    status: Mapped[int] = mapped_column(Integer, default=0, comment="状态: 0=待审核, 1=已认证, 2=未通过")
    phone: Mapped[str] = mapped_column(String(32), default="", comment="提交的手机号")
    front_image: Mapped[str] = mapped_column(String(256), default="", comment="身份证正面照URL")
    back_image: Mapped[str] = mapped_column(String(256), default="", comment="身份证背面照URL")
    channel: Mapped[str] = mapped_column(String(64), default="manual", comment="认证渠道: manual=人工, 插件名=第三方")
    submit_time: Mapped[datetime | None] = mapped_column(DateTime, comment="提交时间")
    review_time: Mapped[datetime | None] = mapped_column(DateTime, comment="审核时间")
    reviewer_id: Mapped[int] = mapped_column(Integer, default=0, comment="审核管理员ID")
    reviewer_name: Mapped[str] = mapped_column(String(64), default="", comment="审核管理员名称")
    review_remark: Mapped[str] = mapped_column(String(512), default="", comment="审核备注")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")


class CertificationChannel(Base):
    """认证渠道表"""
    __tablename__ = "hy_certification_channel"
    __table_args__ = {"comment": "认证渠道表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="渠道ID")
    plugin_name: Mapped[str] = mapped_column(String(64), default="", comment="插件标识")
    channel_name: Mapped[str] = mapped_column(String(64), default="", comment="渠道名称")
    channel_type: Mapped[str] = mapped_column(String(20), default="personal", comment="类型: personal=个人, company=企业")
    status: Mapped[int] = mapped_column(Integer, default=0, comment="状态: 0=禁用, 1=启用")
    config: Mapped[str | None] = mapped_column(Text, comment="配置参数JSON（AppID/AppSecret等）")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
