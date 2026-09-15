# -*- coding: utf-8 -*-
"""产区管理演示插件请求模型。"""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class FeedbackCreate(BaseModel):
    """提交任务反馈。"""

    result: str = Field(..., pattern="^(success|partial|failed)$", description="反馈结果")
    content: str = Field(..., min_length=5, max_length=2000, description="反馈正文")
    metrics: dict[str, Any] = Field(default_factory=dict, description="指标快照")

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        """去除正文首尾空白，避免提交空反馈。"""
        value = value.strip()
        if not value:
            raise ValueError("反馈正文不能为空")
        return value

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: dict[str, Any]) -> dict[str, Any]:
        """限制指标数量并去除空键，防止异常载荷膨胀。"""
        if len(value) > 20:
            raise ValueError("指标最多提交 20 项")
        return {
            str(key).strip()[:64]: item
            for key, item in value.items()
            if str(key).strip()
        }
