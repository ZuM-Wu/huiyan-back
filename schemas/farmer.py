"""农户管理 Pydantic Schema"""
from pydantic import BaseModel, Field


class FarmerCreate(BaseModel):
    """管理员创建农户请求体"""

    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")
    nickname: str = Field(default="", max_length=64, description="昵称")
    email: str = Field(default="", max_length=128, description="邮箱")
    phone: str = Field(default="", max_length=32, description="手机号")
    company: str = Field(default="", max_length=128, description="公司/农场名")


class FarmerUpdate(BaseModel):
    """管理员更新农户信息请求体 — 仅更新传入的非 None 字段"""

    nickname: str | None = Field(default=None, max_length=64, description="昵称")
    avatar: str | None = Field(default=None, max_length=256, description="头像URL")
    email: str | None = Field(default=None, max_length=128, description="邮箱")
    phone: str | None = Field(default=None, max_length=32, description="手机号")
    company: str | None = Field(default=None, max_length=128, description="公司/农场名")
    address: str | None = Field(default=None, max_length=256, description="地址")
    remark: str | None = Field(default=None, max_length=2048, description="备注")
    country: str | None = Field(default=None, max_length=32, description="国家")
    language: str | None = Field(default=None, max_length=32, description="语言")
    password: str | None = Field(default=None, max_length=128, description="新密码，留空不修改")


class FarmerStatusUpdate(BaseModel):
    """农户状态切换请求体"""

    status: int = Field(..., ge=0, le=1, description="状态：0=禁用, 1=启用")


class FarmerProfileUpdate(BaseModel):
    """农户自行修改个人信息请求体"""

    nickname: str = Field(default="", max_length=64, description="昵称")
    avatar: str = Field(default="", max_length=256, description="头像URL")
    email: str = Field(default="", max_length=128, description="邮箱")
    phone: str = Field(default="", max_length=32, description="手机号")
    company: str = Field(default="", max_length=128, description="公司/农场名")
