# -*- coding: utf-8 -*-
"""系统 MCP 网络端点的统一地址规范化。"""
from urllib.parse import urlsplit, urlunsplit

from core.config import settings


SYSTEM_MCP_NAME = "huiyan_system_mcp"


def normalize_system_mcp_url(name: str, url: str) -> str:
    """为系统 MCP 根端点补齐尾斜杠，避免 ASGI Mount 的 307 跳转。

    仅处理固定系统 MCP 名称且路径与当前挂载点完全一致的地址；管理员
    配置的其他外部 MCP 地址保持原样，避免改变第三方服务的路由语义。
    """
    if name != SYSTEM_MCP_NAME or not url:
        return url
    parsed = urlsplit(url)
    mount_path = str(settings.MCP_MOUNT_PATH or "/mcp").rstrip("/") or "/"
    if parsed.path.rstrip("/") != mount_path:
        return url
    normalized_path = "/" if mount_path == "/" else f"{mount_path}/"
    return urlunsplit(parsed._replace(path=normalized_path))


__all__ = ["SYSTEM_MCP_NAME", "normalize_system_mcp_url"]
