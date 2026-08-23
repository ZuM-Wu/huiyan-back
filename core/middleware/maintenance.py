"""
维护模式中间件
当系统配置 site_maintenance=1 时，拦截非 API 请求并返回维护页面

规则:
- /api/ 开头的接口请求正常放行（前端仍需拉取数据）
- /health 健康检查正常放行
- /static/ 静态资源正常放行
- /admin/login 登录页正常放行
- 其他页面请求返回维护页面 HTML
"""

import logging
import time
from typing import cast

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse

logger = logging.getLogger(__name__)

# 维护配置模块级 TTL 缓存（10 秒）：避免每个页面请求都查两次数据库
# 后台保存维护配置时调用 invalidate_maintenance_cache() 立即失效，切换即时生效
_CACHE_TTL_SECONDS = 10
_maintenance_cache = {
    "expire": 0.0,          # 缓存过期时间戳（0 表示未缓存/已失效）
    "is_maintenance": False,  # 维护模式开关
    "message": "",           # 维护提示语
}


def invalidate_maintenance_cache():
    """主动失效维护配置缓存（后台保存 site_maintenance/maintenance_message 时调用）"""
    _maintenance_cache["expire"] = 0.0

# 维护页面 HTML 模板
_MAINTENANCE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>系统维护中</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; background: #f5f7fa;
        }}
        .maintenance-card {{
            text-align: center; background: #fff; padding: 64px 80px;
            border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,.06);
        }}
        .maintenance-icon {{
            font-size: 64px; margin-bottom: 24px; color: #e37318;
        }}
        h1 {{ font-size: 24px; color: #181818; margin-bottom: 12px; font-weight: 600; }}
        p {{ font-size: 16px; color: #8b8b8b; line-height: 1.6; }}
    </style>
</head>
<body>
    <div class="maintenance-card">
        <div class="maintenance-icon">&#9888;</div>
        <h1>系统维护中</h1>
        <p>{message}</p>
    </div>
</body>
</html>"""

# 白名单路径前缀 — 维护模式下仍正常放行
_PASSTHROUGH_PREFIXES = (
    "/api/",
    "/health",
    "/static/",
    "/upload/",
    "/admin/login",
    "/mcp",  # MCP 服务端点（Streamable HTTP 的 GET/SSE 流不受维护模式影响）
)


class MaintenanceMiddleware(BaseHTTPMiddleware):
    """
    维护模式中间件
    从 hy_configuration 表读取 site_maintenance 和 maintenance_message
    """

    async def dispatch(self, request: Request, call_next):
        # 仅拦截 GET 页面请求（非 API/静态资源）
        if request.method != "GET":
            return await call_next(request)

        path = request.url.path
        for prefix in _PASSTHROUGH_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # 读取维护模式配置（经 10 秒 TTL 缓存，非每请求查库）
        is_maintenance, message = await self._get_maintenance_config()
        if not is_maintenance:
            return await call_next(request)

        # 返回维护页面
        html = _MAINTENANCE_HTML.format(message=message)
        return HTMLResponse(content=html, status_code=503)

    @staticmethod
    async def _get_maintenance_config() -> tuple:
        """
        获取维护模式配置（带 TTL 缓存）

        返回 (是否维护中, 提示语)；缓存命中时直接返回内存值，
        过期或被主动失效后才重新查库，查库异常时兜底为非维护状态
        """
        now = time.time()
        expire = cast(float, _maintenance_cache["expire"])
        if now < expire:
            return _maintenance_cache["is_maintenance"], _maintenance_cache["message"]

        try:
            from core.auth.security_policy import get_config_int, get_config_value
            is_maintenance = (await get_config_int("site_maintenance", 0)) == 1
            message = await get_config_value("maintenance_message", "系统维护中，请稍后访问")
        except Exception:
            # 查库异常兜底：视为非维护状态，不影响正常访问
            is_maintenance, message = False, "系统维护中，请稍后访问"

        _maintenance_cache["is_maintenance"] = is_maintenance
        _maintenance_cache["message"] = message
        _maintenance_cache["expire"] = now + _CACHE_TTL_SECONDS
        return is_maintenance, message
