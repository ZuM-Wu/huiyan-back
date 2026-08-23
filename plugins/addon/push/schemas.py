# -*- coding: utf-8 -*-
"""推送中心 2.0 请求模型与业务校验。"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


CHANNELS = {"inbox", "sms", "email"}
MODES = {"all", "specified", "filtered"}
CYCLES = {"onetime", "day", "week", "month"}


class PushChannelConfig(BaseModel):
    """渠道配置。inbox 为布尔开关，短信和邮件可附带模板。"""

    inbox: bool = Field(default=True, description="是否发送站内信")
    sms: bool = Field(default=False, description="是否发送短信")
    email: bool = Field(default=False, description="是否发送邮件")
    sms_interface: str = Field(default="", description="短信接口标识")
    sms_template_id: int = Field(default=0, description="短信模板 ID")
    email_template_id: int = Field(default=0, description="邮件模板 ID")

    @model_validator(mode="after")
    def validate_channels(self):
        if not any((self.inbox, self.sms, self.email)):
            raise ValueError("至少选择一个推送渠道")
        if self.sms and self.sms_template_id < 0:
            raise ValueError("短信模板无效")
        if self.email and self.email_template_id < 0:
            raise ValueError("邮件模板无效")
        return self

    def enabled(self) -> list[str]:
        return [name for name in ("inbox", "sms", "email") if getattr(self, name)]


class PushTargetRule(BaseModel):
    """目标农户规则。"""

    mode: str = Field(default="all", description="目标模式：全部、指定或筛选")
    farmer_ids: List[int] = Field(default_factory=list, description="指定农户 ID 列表")
    filters: Dict[str, Any] = Field(default_factory=dict, description="筛选条件")

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in MODES:
            raise ValueError("目标模式无效")
        return value

    @model_validator(mode="after")
    def validate_target(self):
        if self.mode == "specified" and not self.farmer_ids:
            raise ValueError("指定农户模式至少选择一位农户")
        return self


class PushScheduleRule(BaseModel):
    """周期调度规则。"""

    cycle: str = Field(default="onetime", description="发送周期：单次、每天、每周或每月")
    week_day: int = Field(default=1, ge=1, le=7, description="每周几，1-7")
    month_day: int = Field(default=1, ge=1, le=28, description="每月几号，1-28")
    time_hour_min: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="发送时间 HH:MM")
    start_time: Optional[datetime] = Field(default=None, description="有效期开始时间")
    end_time: Optional[datetime] = Field(default=None, description="有效期结束时间")

    @field_validator("cycle")
    @classmethod
    def validate_cycle(cls, value: str) -> str:
        if value not in CYCLES:
            raise ValueError("推送周期无效")
        return value

    @model_validator(mode="after")
    def validate_range(self):
        if self.start_time and self.end_time and self.start_time > self.end_time:
            raise ValueError("有效期开始时间不能晚于结束时间")
        return self


class PushTaskCreate(BaseModel):
    """创建推送中心任务。"""

    title: str = Field(..., min_length=1, max_length=128, description="推送标题")
    keywords: str = Field(default="", max_length=256, description="关键词")
    content: str = Field(default="", description="推送正文")
    subject: str = Field(default="", max_length=256, description="邮件主题")
    channels: PushChannelConfig = Field(default_factory=PushChannelConfig, description="推送渠道配置")
    target_rule: PushTargetRule = Field(default_factory=PushTargetRule, description="目标农户规则")
    schedule_rule: PushScheduleRule = Field(default_factory=PushScheduleRule, description="周期调度规则")


class PushTaskUpdate(BaseModel):
    """更新等待中任务。"""

    title: Optional[str] = Field(default=None, min_length=1, max_length=128, description="推送标题")
    keywords: Optional[str] = Field(default=None, max_length=256, description="关键词")
    content: Optional[str] = Field(default=None, description="推送正文")
    subject: Optional[str] = Field(default=None, max_length=256, description="邮件主题")
    channels: Optional[PushChannelConfig] = Field(default=None, description="推送渠道配置")
    target_rule: Optional[PushTargetRule] = Field(default=None, description="目标农户规则")
    schedule_rule: Optional[PushScheduleRule] = Field(default=None, description="周期调度规则")


class PushTargetPreview(BaseModel):
    """目标预览请求。"""

    target_rule: PushTargetRule = Field(..., description="目标农户规则")
    page: int = Field(default=1, ge=1, description="页码")
    limit: int = Field(default=100, ge=1, le=500, description="每页数量")


class PushPreviewSend(BaseModel):
    """发送预览请求。"""

    email: str = Field(default="", description="测试邮箱")
    phone: str = Field(default="", description="测试手机号")


class StatusChange(BaseModel):
    """旧接口状态兼容模型。"""

    status: str = Field(..., description="任务状态")


class TestSend(BaseModel):
    """旧接口测试发送兼容模型。"""

    task_id: int = Field(default=0, description="任务 ID")
    farmer_ids: List[int] = Field(default_factory=list, description="农户 ID 列表")
    type: int = Field(default=1, ge=1, le=2, description="发送类型")
    content: str = Field(default="", description="测试内容")
    subject: str = Field(default="", description="测试主题")
    sms_template_id: int = Field(default=0, description="短信模板 ID")
    email_template_id: int = Field(default=0, description="邮件模板 ID")
    test_email: str = Field(default="", description="测试邮箱")
    test_phone: str = Field(default="", description="测试手机号")
