"""企业微信通知插件请求模型。"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MESSAGE_TYPES = ("template_card",)


def _validate_optional_webhook(value: str) -> str:
    """Webhook 可留空，配置后必须使用 HTTPS。"""
    value = value.strip()
    if value and not value.startswith("https://"):
        raise ValueError("Webhook 地址必须使用 HTTPS")
    return value


def _validate_optional_url(value: str) -> str:
    """卡片跳转地址可留空，填写后必须为 HTTP(S) 地址。"""
    value = value.strip()
    if value and not value.startswith(("http://", "https://")):
        raise ValueError("卡片跳转地址必须使用 HTTP(S)")
    return value


class WecomConfigUpdate(BaseModel):
    """企业微信配置更新请求。"""

    webhook_url: str = Field("", max_length=512, description="Webhook 完整地址")
    enabled: int = Field(0, ge=0, le=1, description="是否全局启用")
    timeout_seconds: int = Field(10, ge=1, le=60, description="请求超时秒数")
    retry_times: int = Field(3, ge=0, le=5, description="队列最大重试次数")

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: str) -> str:
        return _validate_optional_webhook(value)


class WecomConfigResponse(BaseModel):
    """企业微信配置响应。"""

    webhook_url: str = Field("", description="Webhook 完整地址")
    enabled: int = Field(0, ge=0, le=1, description="是否全局启用")
    timeout_seconds: int = Field(10, ge=1, le=60, description="请求超时秒数")
    retry_times: int = Field(3, ge=0, le=5, description="队列最大重试次数")


class TemplateCardMessage(BaseModel):
    """企业微信 text_notice 模板卡片消息。"""

    card_type: str = Field("text_notice", pattern="^text_notice$", description="卡片类型")
    source_desc: str = Field("", max_length=20, description="卡片来源描述")
    main_title: str = Field(..., min_length=1, max_length=128, description="主标题")
    main_desc: str = Field("", max_length=128, description="主描述")
    sub_title_text: str = Field("", max_length=128, description="副标题")
    card_url: str = Field("", max_length=512, description="卡片跳转地址")
    horizontal_content: List[Dict[str, Any]] = Field(
        default_factory=list, max_length=6, description="卡片摘要字段，最多6项",
    )

    @field_validator("card_url")
    @classmethod
    def validate_card_url(cls, value: str) -> str:
        return _validate_optional_url(value)


class SendMessageRequest(BaseModel):
    """仅发送企业微信模板卡片。"""

    msgtype: str = Field("template_card", pattern="^template_card$", description="消息类型")
    template_card: Optional[TemplateCardMessage] = Field(None, description="模板卡片消息")

    @model_validator(mode="after")
    def validate_message_payload(self):
        """卡片消息必须提供 template_card 对象。"""
        if self.template_card is None:
            raise ValueError("模板卡片消息缺少 template_card 内容")
        return self


class WecomActionUpdate(BaseModel):
    """企业微信管理员动作开关和动作级 Webhook。"""

    enabled: int = Field(0, ge=0, le=1, description="是否启用")
    webhook_url: str = Field("", max_length=512, description="动作级 Webhook，留空继承全局")

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: str) -> str:
        return _validate_optional_webhook(value)


class WecomActionItem(WecomActionUpdate):
    """带动作标识的批量配置项。"""

    action_key: str = Field(
        ..., min_length=1, max_length=64, pattern="^[a-zA-Z0-9_:-]+$", description="管理员动作标识",
    )


class WecomActionBatchUpdate(BaseModel):
    """批量动作配置更新请求。"""

    items: List[WecomActionItem] = Field(..., min_length=1, max_length=200, description="批量动作配置")


class WecomTestRequest(SendMessageRequest):
    """测试发送请求。"""

    webhook_url: str = Field("", max_length=512, description="临时 Webhook，留空使用全局配置")

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: str) -> str:
        return _validate_optional_webhook(value)


class WecomLogResponse(BaseModel):
    """发送日志响应模型。"""

    id: int = Field(..., description="日志 ID")
    action_key: str = Field("", description="动作标识")
    msgtype: str = Field("template_card", description="消息类型")
    content: str = Field("", description="卡片内容预览")
    status: int = Field(0, description="发送状态")
    error_msg: str = Field("", description="错误信息")
    msg_id: str = Field("", description="第三方消息 ID")
    create_time: Optional[datetime] = Field(None, description="创建时间")
    model_config = ConfigDict(from_attributes=True)
