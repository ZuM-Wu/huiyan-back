# -*- coding: utf-8 -*-
"""
App管理插件 Pydantic 模型（Schema）

所有请求体校验模型集中在此，禁止在路由文件中直接定义模型。
注意：APK / 广告图上传接口为 multipart 表单（UploadFile + Form 参数），不走 Pydantic 请求体。
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class VersionUpdate(BaseModel):
    """编辑版本请求体 — 不更换物理APK，仅更新元信息"""

    build_number: str = Field(default="", max_length=32, description="内部构建号")
    changelog: str = Field(default="", max_length=20000, description="更新日志(富文本HTML)")
    update_policy: int = Field(..., ge=0, le=2, description="更新策略 0可忽略 1提示可稍后 2强制")
    status: int = Field(..., ge=0, le=1, description="状态 1发布 0下架")


class AdUpdate(BaseModel):
    """开屏广告配置请求体 — 仅更新 id=1 单行（广告图经 /ad/image 单独上传）"""

    link_url: str = Field(default="", max_length=500, description="点击跳转URL，空则不跳转")
    start_time: Optional[datetime] = Field(default=None, description="投放开始时间，NULL=立即")
    end_time: Optional[datetime] = Field(default=None, description="投放结束时间，NULL=不限")
    duration: int = Field(default=3, ge=1, le=60, description="开屏展示秒数")
    enabled: int = Field(..., ge=0, le=1, description="是否启用 0禁用 1启用")


class NoticeCreate(BaseModel):
    """新建App公告请求体"""

    title: str = Field(..., min_length=1, max_length=200, description="公告标题")
    content: str = Field(default="", max_length=20000, description="公告内容(富文本HTML)")
    is_popup: int = Field(default=0, ge=0, le=1, description="是否弹窗提示 0否 1是")
    start_time: Optional[datetime] = Field(default=None, description="生效开始时间，NULL=立即")
    end_time: Optional[datetime] = Field(default=None, description="生效结束时间，NULL=永久")
    enabled: int = Field(default=1, ge=0, le=1, description="是否启用 0禁用 1启用")
    sort_order: int = Field(default=0, ge=0, description="排序值，越小越靠前")


class NoticeUpdate(NoticeCreate):
    """编辑App公告请求体 — 字段同新建"""
