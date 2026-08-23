# -*- coding: utf-8 -*-
"""
农业知识库插件 Pydantic 模型（Schema）

所有请求体校验模型集中在此，禁止在路由文件中直接定义模型。
典型图片以 URL 数组形式提交（前端已调 /api/admin/v1/upload/image 得到 URL）。
"""
from pydantic import BaseModel, Field, field_validator


class CategoryCreate(BaseModel):
    """新建分类请求体"""

    name: str = Field(..., min_length=1, max_length=50, description="分类名称")
    parent_id: int = Field(default=0, ge=0, description="父分类ID 0=大类")
    sort_order: int = Field(default=0, description="排序值 越小越靠前")


class CategoryUpdate(BaseModel):
    """编辑分类请求体"""

    name: str = Field(..., min_length=1, max_length=50, description="分类名称")
    sort_order: int = Field(default=0, description="排序值 越小越靠前")
    status: int = Field(default=1, ge=0, le=1, description="状态 0停用 1启用")


class EntryCreate(BaseModel):
    """新建知识条目请求体"""

    title: str = Field(..., min_length=1, max_length=200, description="知识标题")
    category_id: int = Field(..., ge=1, description="所属分类ID")
    crop: str = Field(default="", max_length=200, description="适用作物 逗号分隔")
    summary: str = Field(default="", max_length=500, description="摘要")
    cause: str = Field(default="", description="发生原因")
    solution: str = Field(default="", description="解决方案")
    images: list[str] = Field(default_factory=list, description="典型图片URL列表")
    sort_order: int = Field(default=0, description="排序值 越小越靠前")


class EntryUpdate(EntryCreate):
    """编辑知识条目请求体（字段同新建）"""


class EntryBatchCreate(BaseModel):
    """批量新建知识条目请求体（共享分类/作物 + 多行标题，每行一条）"""

    category_id: int = Field(..., ge=1, description="所属分类ID 批量共享")
    crop: str = Field(default="", max_length=200, description="适用作物 批量共享 选填")
    titles: list[str] = Field(..., min_length=1, description="标题列表 每行一条")

    @field_validator("titles")
    @classmethod
    def _clean_titles(cls, v: list[str]) -> list[str]:
        """逐项 strip 过滤空串，单条限 1-200 字符，全空则报错"""
        cleaned = [t.strip() for t in v if t and t.strip()]
        if not cleaned:
            raise ValueError("标题列表不能全为空")
        for t in cleaned:
            if len(t) > 200:
                raise ValueError("单条标题不能超过200字符")
        return cleaned


class CorrectionSubmit(BaseModel):
    """农户提交勘误请求体"""

    content: str = Field(..., min_length=1, max_length=1000, description="修正建议")


class CorrectionHandle(BaseModel):
    """管理员处理勘误请求体"""

    status: int = Field(..., ge=1, le=2, description="处理结果 1已采纳 2已驳回")
    admin_note: str = Field(default="", max_length=500, description="处理备注")
