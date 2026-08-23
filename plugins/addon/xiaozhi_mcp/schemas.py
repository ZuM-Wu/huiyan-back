# -*- coding: utf-8 -*-
"""小智 AI MCP 管理接口请求模型。"""
from pydantic import BaseModel, Field


class XiaozhiMcpServerUpsert(BaseModel):
    """小智专属外部 MCP 服务器创建或更新请求。"""

    name: str = Field(..., description="服务器名称")
    url: str = Field(default="", description="MCP 服务地址")
    api_key: str = Field(default="", description="访问密钥")
    config_json: str = Field(default="", description="JSON 配置")
    status: int = Field(default=1, description="启用状态: 0=停用, 1=启用")
    clear_api_key: bool = Field(default=False, description="是否清除已保存密钥")


class XiaozhiConfigUpdate(BaseModel):
    """小智接入点配置更新请求。"""

    enabled: bool | None = Field(default=None, description="是否启用常驻连接")
    endpoint_url: str | None = Field(default=None, max_length=4096, description="WSS 接入点地址")
    clear_endpoint_url: bool = Field(default=False, description="是否清除接入点地址")
