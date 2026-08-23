"""个人信息修改 Pydantic Schema"""
from pydantic import BaseModel, Field


class AdminProfileUpdate(BaseModel):
    """管理员修改个人信息请求体"""

    nickname: str = Field(default="", max_length=64, description="昵称")
    email: str = Field(default="", max_length=128, description="邮箱")
    phone: str = Field(default="", max_length=32, description="手机号")


class PasswordChange(BaseModel):
    """密码修改请求体"""

    old_password: str = Field(..., min_length=1, description="当前密码")
    new_password: str = Field(..., min_length=6, max_length=128, description="新密码")
