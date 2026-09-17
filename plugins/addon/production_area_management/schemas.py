# -*- coding: utf-8 -*-
"""产区管理插件请求模型。"""

from pydantic import BaseModel, Field, field_validator


def _clean_image_urls(value: list[str]) -> list[str]:
    """清理图片稳定地址，过滤空值和 data URL，并保持原有顺序去重。"""
    cleaned: list[str] = []
    for item in value:
        url = str(item or "").strip()
        if not url or url.startswith("data:"):
            continue
        if not url.startswith(("/", "http://", "https://")):
            raise ValueError("图片地址必须是站内或 HTTP(S) 地址")
        if url not in cleaned:
            cleaned.append(url)
    return cleaned


class ScheduleUpdate(BaseModel):
    """更新按日事实同步计划。"""

    enabled: bool = Field(default=True, description="是否启用每日同步")
    time: str = Field(default="06:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="每日执行时间")


class FeedbackCreate(BaseModel):
    """提交现场文字和图片反馈。"""

    result: str = Field(..., pattern="^(success|partial|failed)$", description="反馈结果")
    content: str = Field(..., min_length=5, max_length=2000, description="现场文字说明")
    images: list[str] = Field(default_factory=list, max_length=9, description="现场图片稳定地址")

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        """去除正文首尾空白，避免提交空反馈。"""
        value = value.strip()
        if not value:
            raise ValueError("反馈正文不能为空")
        return value

    @field_validator("images")
    @classmethod
    def validate_images(cls, value: list[str]) -> list[str]:
        """仅允许站内稳定地址或同源相对地址，拒绝 data URL。"""
        return _clean_image_urls(value)


class LogImagesUpdate(BaseModel):
    """保存管理员为按日事实补充的图片。"""

    images: list[str] = Field(default_factory=list, max_length=9, description="日报图片稳定地址")

    @field_validator("images")
    @classmethod
    def validate_images(cls, value: list[str]) -> list[str]:
        """日报图片沿用现场反馈相同的稳定地址约束。"""
        return _clean_image_urls(value)
