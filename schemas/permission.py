"""权限管理 Pydantic Schema"""
from pydantic import BaseModel, Field


class PermissionCreate(BaseModel):
    """创建权限节点请求体"""

    title: str = Field(..., min_length=1, max_length=128, description="权限标题")
    code: str = Field(..., min_length=1, max_length=128, description="权限标识, 如 farm:create")
    parent_id: int = Field(default=0, ge=0, description="父权限ID, 0=顶级")
    sort_order: int = Field(default=0, ge=0, description="排序")
    description: str = Field(default="", max_length=256, description="描述")


class PermissionUpdate(BaseModel):
    """更新权限节点请求体"""

    title: str = Field(..., min_length=1, max_length=128, description="权限标题")
    code: str = Field(..., min_length=1, max_length=128, description="权限标识")
    parent_id: int = Field(default=0, ge=0, description="父权限ID, 0=顶级")
    sort_order: int = Field(default=0, ge=0, description="排序")
    description: str = Field(default="", max_length=256, description="描述")
