# -*- coding: utf-8 -*-
"""
邮件通知管理员插件 — Pydantic 请求/响应模型
仅包含告警配置相关模型（日志模型已移至核心）
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AlertConfigItem(BaseModel):
    """任务告警配置项"""
    id: Optional[int] = Field(None, description="配置 ID")
    task_name: str = Field(..., description="任务标识")
    task_title: str = Field("", description="任务标题")
    task_type: str = Field("system", description="任务类型")
    notify_enabled: int = Field(0, ge=0, le=1, description="是否启用通知")
    notify_interface: str = Field("", description="邮件接口标识")
    admin_ids: str = Field("", description="接收管理员 ID 列表")
    model_config = ConfigDict(from_attributes=True)


class AlertConfigListResponse(BaseModel):
    """告警配置列表响应"""
    items: list[AlertConfigItem] = Field(..., description="告警配置列表")


class AlertConfigUpdate(BaseModel):
    """告警配置更新请求"""
    notify_enabled: Optional[int] = Field(None, ge=0, le=1, description="是否启用通知")
    notify_interface: Optional[str] = Field(None, description="邮件接口标识")
    admin_ids: Optional[str] = Field(None, description="接收管理员 ID 列表")
    task_title: Optional[str] = Field(None, description="任务标题")


class BatchConfigUpdate(BaseModel):
    """批量更新告警配置"""
    items: list[AlertConfigItem] = Field(..., description="待更新配置列表")


class NotifyTestRequest(BaseModel):
    """发送测试通知请求"""
    notify_interface: str = Field("", description="邮件接口标识")
    admin_ids: str = Field("", description="接收管理员 ID 列表")


class AdminItem(BaseModel):
    """管理员项"""
    id: int = Field(..., description="管理员 ID")
    nickname: str = Field("", description="昵称")
    email: str = Field("", description="邮箱")
    username: str = Field("", description="用户名")
