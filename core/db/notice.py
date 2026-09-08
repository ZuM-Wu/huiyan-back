"""
通知模块数据模型
包含通知动作、短信模板、邮件模板、通知日志、站内信消息表
"""
from datetime import datetime
from core.time_utils import china_now
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class NoticeAction(Base):
    """
    通知动作配置表
    定义每个通知事件 (如 user_registered) 的短信/邮件接口与模板配置
    """
    __tablename__ = "hy_notice_action"
    __table_args__ = {"comment": "通知动作配置表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="动作 ID")
    action_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="动作标识:user_registered")
    action_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="动作名称：用户注册")
    action_type: Mapped[str] = mapped_column(String(32), default="other", comment="类型:user/order/production")

    # 国内短信配置
    sms_enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否启用短信:0 否 1 是")
    sms_interface: Mapped[str] = mapped_column(String(64), default="", comment="短信接口标识：aliyun/idcsmart")
    sms_template_id: Mapped[int] = mapped_column(Integer, default=0, comment="短信模板 ID")

    # 国际短信配置
    sms_global_enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否启用国际短信")
    sms_global_interface: Mapped[str] = mapped_column(String(64), default="", comment="国际短信接口标识")
    sms_global_template_id: Mapped[int] = mapped_column(Integer, default=0, comment="国际短信模板 ID")

    # 邮件配置
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否启用邮件")
    email_interface: Mapped[str] = mapped_column(String(64), default="", comment="邮件接口标识：smtp/sendcloud")
    email_template_id: Mapped[int] = mapped_column(Integer, default=0, comment="邮件模板 ID")

    # 联动配置
    trigger_inbox: Mapped[bool] = mapped_column(Boolean, default=False, comment="发送时是否联动站内信")

    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class SmsTemplate(Base):
    """
    短信模板表
    支持国内 (type=0) 和国际 (type=1) 短信模板
    """
    __tablename__ = "hy_sms_template"
    __table_args__ = {"comment": "短信模板表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="模板 ID")
    interface: Mapped[str] = mapped_column(String(64), nullable=False, comment="接口标识:aliyun/idcsmart")
    type: Mapped[int] = mapped_column(Integer, default=0, comment="类型:0=国内 1=国际")

    template_id: Mapped[str] = mapped_column(String(128), default="", comment="第三方模板 ID")
    title: Mapped[str] = mapped_column(String(128), nullable=False, comment="模板标题：验证码")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="模板内容：验证码@var(code),5 分钟内有效")
    signature: Mapped[str] = mapped_column(String(64), default="", comment="签名：【慧眼护农】")

    status: Mapped[int] = mapped_column(Integer, default=0, comment="状态:0=草稿 1=待审核 2=已通过 3=未通过")
    third_status: Mapped[str | None] = mapped_column(Text, comment="第三方审核状态 JSON")

    action_key: Mapped[str] = mapped_column(String(64), default="", comment="默认关联动作")
    remark: Mapped[str] = mapped_column(String(256), default="", comment="备注")

    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class EmailTemplate(Base):
    """
    邮件模板表
    邮件无平台审核要求，模板不绑定具体插件，发送时由核心自动路由到已启用的邮件插件
    """
    __tablename__ = "hy_email_template"
    __table_args__ = {"comment": "邮件模板表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="模板 ID")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模板名称：验证码")
    subject: Mapped[str] = mapped_column(String(256), nullable=False, comment="邮件主题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="邮件内容 (HTML)")
    attachment: Mapped[str] = mapped_column(String(512), default="", comment="附件 URL 列表 JSON")

    action_key: Mapped[str] = mapped_column(String(64), default="", comment="默认关联动作")

    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class NoticeLog(Base):
    """
    通知发送日志表
    记录每次通知发送的详细状态
    """
    __tablename__ = "hy_notice_log"
    __table_args__ = {"comment": "通知发送日志表"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="日志 ID")
    action_key: Mapped[str] = mapped_column(String(64), nullable=False, comment="动作标识")
    recipient: Mapped[str] = mapped_column(String(128), nullable=False, comment="接收者 (手机/邮箱)")
    channel: Mapped[str] = mapped_column(String(32), nullable=False, comment="渠道:sms/email")
    template_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="模板 ID")
    content: Mapped[str | None] = mapped_column(Text, comment="发送内容摘要")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="状态:0=失败 1=成功")
    error_msg: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    recipient_id: Mapped[int] = mapped_column(Integer, default=0, comment="接收者 ID(farmer_id/admin_id)")
    extra: Mapped[dict[str, object] | None] = mapped_column(JSON, comment="扩展字段 (message_id 等)")
    send_time: Mapped[datetime | None] = mapped_column(DateTime, comment="发送时间")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")


class InboxMessage(Base):
    """
    站内信消息表
    通知发送时可联动生成站内信记录，接收者支持农户/管理员两类
    """
    __tablename__ = "hy_inbox_message"
    __table_args__ = {"comment": "站内信消息表"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="消息 ID")
    sender_id: Mapped[int] = mapped_column(Integer, default=0, comment="发件人 ID(系统=0)")
    receiver_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="接收者类型:farmer/admin")
    receiver_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="接收者 ID")
    title: Mapped[str] = mapped_column(String(256), nullable=False, comment="消息标题")
    content: Mapped[str | None] = mapped_column(Text, comment="消息内容")
    is_read: Mapped[int] = mapped_column(Integer, default=0, comment="是否已读:0 未读 1 已读")
    read_time: Mapped[datetime | None] = mapped_column(DateTime, comment="阅读时间")
    priority: Mapped[int] = mapped_column(Integer, default=0, comment="优先级:0=普通 1=重要")
    extra: Mapped[dict[str, object] | None] = mapped_column(JSON, comment="扩展字段 (log_id 等)")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
