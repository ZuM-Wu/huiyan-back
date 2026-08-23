"""角色管理 Pydantic Schema"""
from typing import List

from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    """创建角色请求体"""

    name: str = Field(..., min_length=1, max_length=64, description="角色名称")
    description: str = Field(default="", max_length=256, description="角色描述")
    auth: List[int] = Field(default_factory=list, description="权限ID数组")


class RoleUpdate(BaseModel):
    """更新角色请求体"""

    name: str = Field(..., min_length=1, max_length=64, description="角色名称")
    description: str = Field(default="", max_length=256, description="角色描述")
    auth: List[int] = Field(default_factory=list, description="权限ID数组")
