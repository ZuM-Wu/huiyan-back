"""管理员管理 Pydantic Schema"""
from pydantic import BaseModel, Field


class AdminCreate(BaseModel):
    """创建管理员请求体"""

    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")
    email: str = Field(default="", max_length=128, description="邮箱")
    nickname: str = Field(default="", max_length=64, description="昵称")
    phone: str = Field(default="", max_length=32, description="手机号")
    role_id: int = Field(default=1, ge=1, description="角色ID")


class AdminUpdate(BaseModel):
    """更新管理员请求体"""

    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(default="", max_length=128, description="新密码，留空不修改")
    email: str = Field(default="", max_length=128, description="邮箱")
    nickname: str = Field(default="", max_length=64, description="昵称")
    phone: str = Field(default="", max_length=32, description="手机号")
    role_id: int = Field(default=1, ge=1, description="角色ID")


class AdminStatusUpdate(BaseModel):
    """管理员状态切换请求体"""

    status: int = Field(..., ge=0, le=1, description="状态：0=禁用, 1=启用")
