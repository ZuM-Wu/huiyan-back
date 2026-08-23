"""认证相关 Pydantic Schema"""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """管理员登录请求体（保留原接口，管理员端继续使用）"""

    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")


class FarmerLoginRequest(BaseModel):
    """农户统一登录请求体（支持密码/验证码双模式，对标 ZJMF）"""

    type: str = Field(default="password", description="登录方式: password=密码 / code=验证码")
    account: str = Field(default="", description="手机号或邮箱（密码/验证码登录通用）")
    password: str = Field(default="", description="密码（type=password 时必填）")
    code: str = Field(default="", description="验证码（type=code 时必填）")
    # 兼容旧字段
    username: str = Field(default="", description="用户名（向后兼容旧接口）")


class RegisterRequest(BaseModel):
    """农户注册请求体"""

    username: str = Field(default="", max_length=64, description="用户名（留空时自动使用手机号或邮箱）")
    password: str = Field(..., min_length=6, max_length=128, description="密码")
    nickname: str = Field(default="", max_length=64, description="昵称")
    email: str = Field(default="", max_length=128, description="邮箱")
    phone: str = Field(default="", max_length=32, description="手机号")
    verify_code: str = Field(default="", max_length=16, description="注册验证码（配置要求时必填）")


class SendCodeRequest(BaseModel):
    """发送验证码请求体"""

    target: str = Field(..., min_length=5, max_length=128, description="接收目标（手机号/邮箱）")
    purpose: str = Field(..., description="用途: login / reset / register")
    channel: str = Field(default="sms", description="渠道: sms / email")


class PasswordResetSendRequest(BaseModel):
    """密码找回-发送验证码请求体"""

    target: str = Field(..., min_length=5, max_length=128, description="手机号或邮箱")
    channel: str = Field(default="sms", description="渠道: sms / email")


class PasswordResetVerifyRequest(BaseModel):
    """密码找回-验证并重置密码请求体"""

    target: str = Field(..., min_length=5, max_length=128, description="手机号或邮箱")
    code: str = Field(..., min_length=4, max_length=16, description="验证码")
    new_password: str = Field(..., min_length=6, max_length=128, description="新密码")


class LoginResponse(BaseModel):
    """登录响应体"""

    token: str = Field(..., description="JWT Token")
    user_id: int = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
