"""个人 API Key 管理 Pydantic Schema（后台/农户端共用）"""
from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    """创建个人 API Key 请求体"""

    name: str = Field(..., min_length=1, max_length=64, description="密钥备注名")


class ApiKeyRevoke(BaseModel):
    """吊销个人 API Key 请求体"""

    id: int = Field(..., ge=1, description="密钥记录ID")
