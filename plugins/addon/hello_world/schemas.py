# -*- coding: utf-8 -*-
"""
Hello World 插件 Pydantic 模型（Schema）

演示请求体/响应体的定义规范：禁止在 router.py 中直接定义模型，
所有校验模型集中在 schemas.py，便于复用与维护。
"""
from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    """创建留言请求体"""

    title: str = Field(..., min_length=1, max_length=128, description="留言标题")
    content: str = Field(..., min_length=1, description="留言内容")
    author: str = Field(default="", max_length=64, description="留言人")


class MessageUpdate(BaseModel):
    """更新留言请求体 — 仅更新传入的非 None 字段"""

    title: str | None = Field(default=None, max_length=128, description="留言标题")
    content: str | None = Field(default=None, description="留言内容")
    author: str | None = Field(default=None, max_length=64, description="留言人")
    status: int | None = Field(default=None, description="状态：0=隐藏, 1=显示")


class StatusUpdate(BaseModel):
    """状态切换请求体 — 演示 RESTful 状态切换模式"""

    status: int = Field(..., ge=0, le=1, description="状态：0=隐藏, 1=显示")


class ConfigUpdate(BaseModel):
    """更新插件配置请求体"""

    welcome_text: str = Field(..., max_length=256, description="欢迎语")
    page_size: int = Field(..., ge=1, le=100, description="默认分页大小")
