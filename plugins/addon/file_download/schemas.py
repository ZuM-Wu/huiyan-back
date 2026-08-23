# -*- coding: utf-8 -*-
"""
文件下载插件 Pydantic 模型（Schema）

所有请求体校验模型集中在此，禁止在路由文件中直接定义模型。
注意：上传接口为 multipart 表单（UploadFile + Form 参数），不走 Pydantic 请求体。
"""
from pydantic import BaseModel, Field


class FolderCreate(BaseModel):
    """新建文件夹请求体"""

    name: str = Field(..., min_length=1, max_length=100, description="文件夹名称")


class FolderRename(BaseModel):
    """重命名文件夹请求体"""

    name: str = Field(..., min_length=1, max_length=100, description="文件夹名称")


class FileUpdate(BaseModel):
    """编辑文件请求体 — 不更换物理文件，仅更新元信息"""

    name: str = Field(..., min_length=1, max_length=200, description="显示名称")
    folder_id: int = Field(..., ge=1, description="所属文件夹ID")
    visible_range: str = Field(
        ..., pattern="^(all|area)$",
        description="可见范围 all:所有农户 area:指定产区绑定农户",
    )
    area_ids: list[int] = Field(default_factory=list, description="产区ID列表，visible_range=area 时必填")
    description: str = Field(default="", max_length=1000, description="文件描述")


class FileHiddenToggle(BaseModel):
    """显示/隐藏切换请求体"""

    hidden: int = Field(..., ge=0, le=1, description="是否隐藏 0显示 1隐藏")
