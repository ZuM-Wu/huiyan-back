"""七牛对象存储管理接口的请求模型与输入规范。"""

from typing import Literal

from pydantic import BaseModel, Field


class AccessUrlRequest(BaseModel):
    """生成文件访问地址。"""

    key: str = Field(min_length=1, max_length=512)
    action: Literal["preview", "download"] = "preview"
    expires: int = Field(default=3600, ge=60, le=86400)


class DeleteFileRequest(BaseModel):
    """删除单个对象。"""

    key: str = Field(min_length=1, max_length=512)

