"""
通知模块 Pydantic 数据验证模式
用于 API 请求参数校验和响应序列化
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, field_serializer


# ========== 通知动作相关 ==========

class NoticeActionBase(BaseModel):
    """通知动作基础模型"""
    action_name: str = Field(..., min_length=1, max_length=128, description="动作名称")
    action_type: str = Field(default="other", description="类型:user/order/production")

    # 国内短信配置
    sms_enabled: bool = Field(False, description="是否启用短信")
    sms_interface: str = Field("", max_length=64, description="短信接口标识")
    sms_template_id: int = Field(0, ge=0, description="短信模板 ID")

    # 国际短信配置
    sms_global_enabled: bool = Field(False, description="是否启用国际短信")
    sms_global_interface: str = Field("", max_length=64, description="国际短信接口标识")
    sms_global_template_id: int = Field(0, ge=0, description="国际短信模板 ID")

    # 邮件配置
    email_enabled: bool = Field(False, description="是否启用邮件")
    email_interface: str = Field("", max_length=64, description="邮件接口标识")
    email_template_id: int = Field(0, ge=0, description="邮件模板 ID")

    # 联动配置
    trigger_inbox: bool = Field(False, description="发送时是否联动站内信")


class NoticeActionCreate(NoticeActionBase):
    """创建通知动作"""
    action_key: str = Field(..., min_length=1, max_length=64, description="动作标识")


class NoticeActionUpdate(BaseModel):
    """更新通知动作（所有字段可选）"""
    action_name: Optional[str] = Field(None, max_length=128, description="动作名称")
    action_type: Optional[str] = Field(None, max_length=32, description="动作类型")

    sms_enabled: Optional[bool] = Field(None, description="是否启用短信通知")
    sms_interface: Optional[str] = Field(None, max_length=64, description="短信接口标识")
    sms_template_id: Optional[int] = Field(None, ge=0, description="短信模板ID")

    sms_global_enabled: Optional[bool] = Field(None, description="是否启用全局短信")
    sms_global_interface: Optional[str] = Field(None, max_length=64, description="全局短信接口标识")
    sms_global_template_id: Optional[int] = Field(None, ge=0, description="全局短信模板ID")

    email_enabled: Optional[bool] = Field(None, description="是否启用邮件通知")
    email_interface: Optional[str] = Field(None, max_length=64, description="邮件接口标识")
    email_template_id: Optional[int] = Field(None, ge=0, description="邮件模板ID")

    trigger_inbox: Optional[bool] = Field(None, description="是否联动站内信")


class NoticeActionResponse(NoticeActionBase):
    """通知动作响应模型"""
    id: int = Field(..., description="动作 ID")
    action_key: str = Field(..., description="动作标识")
    create_time: datetime = Field(..., description="创建时间")
    update_time: datetime = Field(..., description="更新时间")
    model_config = ConfigDict(from_attributes=True)

    @field_serializer('create_time', 'update_time')
    @classmethod
    def _fmt_dt(cls, v):
        return v.strftime("%Y-%m-%d %H:%M:%S") if v else None


class NoticeActionListResponse(BaseModel):
    """通知动作列表响应"""
    total: int = Field(..., description="总数")
    page: int = Field(..., description="页码")
    limit: int = Field(..., description="每页数量")
    list: List[NoticeActionResponse] = Field(..., description="动作列表")


# ========== 短信模板相关 ==========

class SmsTemplateBase(BaseModel):
    """短信模板基础模型"""
    interface: str = Field(..., max_length=64, description="接口标识，系统预置模板为空")
    type: int = Field(0, ge=0, le=1, description="类型:0=国内 1=国际")
    title: str = Field(..., min_length=1, max_length=128, description="模板标题")
    content: str = Field(..., description="模板内容")
    signature: str = Field("", max_length=64, description="签名")
    action_key: str = Field("", max_length=64, description="默认关联动作")
    remark: str = Field("", max_length=256, description="备注")


class SmsTemplateCreate(SmsTemplateBase):
    """创建短信模板"""
    template_id: str = Field("", max_length=128, description="第三方模板 ID")
    status: int = Field(0, ge=0, le=3, description="状态:0 草稿 1 待审核 2 已通过 3 未通过")


class SmsTemplateUpdate(BaseModel):
    """更新短信模板（部分字段）"""
    title: Optional[str] = Field(None, max_length=128, description="模板标题")
    content: Optional[str] = Field(None, description="模板内容")
    signature: Optional[str] = Field(None, max_length=64, description="签名")
    status: Optional[int] = Field(None, ge=0, le=3, description="状态:0 草稿 1 待审核 2 已通过 3 未通过")
    action_key: Optional[str] = Field(None, max_length=64, description="默认关联动作")
    remark: Optional[str] = Field(None, max_length=256, description="备注")


class SmsTemplateResponse(SmsTemplateBase):
    """短信模板响应模型"""
    id: int = Field(..., description="模板 ID")
    template_id: str = Field(..., description="第三方模板 ID")
    status: int = Field(..., description="模板状态")
    third_status: Optional[str] = Field(None, description="第三方审核状态")
    is_local: bool = Field(False, description="是否为系统预置本地模板")
    create_time: datetime = Field(..., description="创建时间")
    update_time: datetime = Field(..., description="更新时间")
    model_config = ConfigDict(from_attributes=True)

    @field_serializer('create_time', 'update_time')
    @classmethod
    def _fmt_dt(cls, v):
        return v.strftime("%Y-%m-%d %H:%M:%S") if v else None


class SmsTemplateListResponse(BaseModel):
    """短信模板列表响应"""
    total: int = Field(..., description="总数")
    page: int = Field(..., description="页码")
    limit: int = Field(..., description="每页数量")
    list: List[SmsTemplateResponse] = Field(..., description="短信模板列表")


# ========== 邮件模板相关 ==========

class EmailTemplateBase(BaseModel):
    """邮件模板基础模型（邮件无审核要求，模板不绑定插件接口）"""
    name: str = Field(..., min_length=1, max_length=128, description="模板名称")
    subject: str = Field(..., min_length=1, max_length=256, description="邮件主题")
    content: str = Field(..., description="邮件内容 (HTML)")
    attachment: str = Field("", description="附件 URL 列表 JSON")
    action_key: str = Field("", max_length=64, description="默认关联动作")


class EmailTemplateCreate(EmailTemplateBase):
    """创建邮件模板"""


class EmailTemplateUpdate(BaseModel):
    """更新邮件模板（部分字段）"""
    name: Optional[str] = Field(None, max_length=128, description="模板名称")
    subject: Optional[str] = Field(None, max_length=256, description="邮件主题")
    content: Optional[str] = Field(None, description="邮件内容 (HTML)")
    attachment: Optional[str] = Field(None, description="附件 URL 列表 JSON")
    action_key: Optional[str] = Field(None, max_length=64, description="默认关联动作")


class EmailTemplateResponse(EmailTemplateBase):
    """邮件模板响应模型"""
    id: int = Field(..., description="模板 ID")
    create_time: datetime = Field(..., description="创建时间")
    update_time: datetime = Field(..., description="更新时间")
    model_config = ConfigDict(from_attributes=True)

    @field_serializer('create_time', 'update_time')
    @classmethod
    def _fmt_dt(cls, v):
        return v.strftime("%Y-%m-%d %H:%M:%S") if v else None


class EmailTemplateListResponse(BaseModel):
    """邮件模板列表响应"""
    total: int = Field(..., description="总数")
    page: int = Field(..., description="页码")
    limit: int = Field(..., description="每页数量")
    list: List[EmailTemplateResponse] = Field(..., description="邮件模板列表")


# ========== 通知日志相关 ==========

class NoticeLogResponse(BaseModel):
    """通知日志响应模型"""
    id: int = Field(..., description="日志 ID")
    action_key: str = Field(..., description="动作标识")
    recipient: str = Field(..., description="接收者")
    channel: str = Field(..., description="发送渠道")
    template_id: int = Field(..., description="模板 ID")
    content: Optional[str] = Field(None, description="发送内容")
    status: int = Field(..., description="发送状态")
    error_msg: Optional[str] = Field(None, description="错误信息")
    recipient_id: int = Field(..., description="接收者 ID")
    extra: Optional[Dict[str, Any]] = Field(None, description="扩展信息")
    send_time: Optional[datetime] = Field(None, description="发送时间")
    create_time: datetime = Field(..., description="创建时间")
    model_config = ConfigDict(from_attributes=True)

    @field_serializer('send_time', 'create_time')
    @classmethod
    def _fmt_dt(cls, v):
        return v.strftime("%Y-%m-%d %H:%M:%S") if v else None


class NoticeLogListResponse(BaseModel):
    """通知日志列表响应"""
    total: int = Field(..., description="总数")
    page: int = Field(..., description="页码")
    limit: int = Field(..., description="每页数量")
    list: List[NoticeLogResponse] = Field(..., description="通知日志列表")


# ========== 发送通知相关 ==========

class NoticeSendRequest(BaseModel):
    """发送通知请求"""
    action_key: str = Field(..., min_length=1, max_length=64, description="动作标识")
    recipient: str = Field(..., description="接收者手机/邮箱")
    recipient_type: str = Field(..., description="渠道:sms/email")
    variables: Dict[str, Any] = Field(..., description="模板变量")
    recipient_id: Optional[int] = Field(None, description="接收者 ID(farmer_id/admin_id)")


class NoticeSendResponse(BaseModel):
    """发送通知响应"""
    task_id: str = Field(..., description="队列任务标识")
    log_id: int = Field(..., description="通知日志 ID")
    model_config = ConfigDict(from_attributes=True)


# ========== 测试发送相关 ==========

class NoticeTestRequest(BaseModel):
    """测试发送请求"""
    template_id: int = Field(..., ge=1, description="模板 ID")
    recipient: str = Field(..., description="测试手机号/邮箱")
    variables: Dict[str, Any] = Field(..., description="变量值")
