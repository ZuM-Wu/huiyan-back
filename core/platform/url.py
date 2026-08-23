"""主题和插件 URL 公共门面。"""

import posixpath
import re
from urllib.parse import urlencode


_SAFE_PART = re.compile(r"^[A-Za-z0-9._-]+$")
_RESERVED = ("/api", "/mcp", "/health", "/static", "/upload")


def get_admin_path() -> str:
    """返回当前后台入口；本轮不提供入口修改能力。"""
    return "admin"


def build_admin_path(route: str, params: dict | None = None) -> str:
    """将逻辑路由拼接为当前后台页面 URL。"""
    route = (route or "").strip().lstrip("/")
    if not route or ".." in route.split("/"):
        raise ValueError("后台逻辑路由无效")
    path = f"/{get_admin_path()}/{route}"
    if params:
        path = f"{path}?{urlencode(params)}"
    return path


def build_plugin_page(surface: str, plugin_id: str, page_key: str) -> str:
    """生成插件页面 canonical URL。"""
    if not all(_SAFE_PART.fullmatch(part) for part in (plugin_id, page_key)):
        raise ValueError("插件页面 key 无效")
    if surface == "admin":
        return build_admin_path(f"plugins/{plugin_id}/{page_key}")
    if surface == "farmer":
        prefix = "/farmer/plugins"
    elif surface == "site":
        prefix = "/plugins"
    else:
        raise ValueError(f"不支持的页面端面: {surface}")
    return posixpath.join(prefix, plugin_id, page_key)


def is_admin_path(path: str) -> bool:
    """判断请求是否落在当前后台入口下。"""
    return (path or "").rstrip("/") == f"/{get_admin_path()}" or (path or "").startswith(f"/{get_admin_path()}/")


def get_reserved_paths() -> tuple[str, ...]:
    return _RESERVED
