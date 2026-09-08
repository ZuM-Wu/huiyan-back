"""
官网视图控制器

负责官网（根路径 /）的 Jinja2 模板渲染与公开 API。
主题解析、回退、Environment 构建统一委托 core.theme_manager。

路由:
    GET /                       官网首页（直接渲染 site_theme 主题，不重定向）
    GET /api/site/v1/config     官网公开配置（导航 / 页脚 / Banner / 简介）

权限边界:
    - 官网首页、公开配置对所有人开放（无需登录）
    - 官网主题切换走 POST /api/admin/v1/theme/activate（需 admin 角色，已实现）
    - 导航 / 页脚 / Banner / 简介的写入走 POST /api/admin/v1/site/config（需 admin 角色）

主题回退:
    - 若 site_theme 指向的主题目录不存在 / 缺 base.html / 缺 theme.json，
      theme_manager 自动回退官网端面默认主题；此处无需额外处理。
"""
import json
import logging

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from core.config_service import get_config
from core.db.base import async_session_factory
from core.config_manager import ConfigManager
from core.theme_manager import theme_manager

logger = logging.getLogger(__name__)

# 未 seed 时的兜底默认值（避免首次启动 / 未 seed 场景官网白屏）
_DEFAULT_NAV    = "[]"
_DEFAULT_FOOTER = "[]"
_DEFAULT_BANNER = '{"title": "慧眼护农", "subtitle": "智慧农业业务系统", "background": ""}'
_DEFAULT_INTRO  = ""


def _json_loads_or(value: str, default):
    """安全解析 JSON，失败 / None 返回 default"""
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class SiteViewController:
    """官网页面路由 + 公开 API"""

    def setup(self, app: FastAPI):
        """注册官网路由（须在 API 路由之后、静态文件之前均可；此处由 main.py 在 MPA 路由段调用）"""
        self._register_routes(app)
        logger.info("[SiteViewController] 官网路由已注册")

    def _register_routes(self, app: FastAPI):
        # ---- 根路径：根据官网主题可见性配置决定渲染官网首页或跳转 ----
        @app.get("/", response_class=HTMLResponse)
        async def site_index(request: Request):
            return await self._site_index_response(request)

        # ---- 公开配置 API（前端 AJAX 或 SSR 备用）----
        router = APIRouter(prefix="/api/site/v1", tags=["官网"])

        @router.get("/config")
        async def site_public_config():
            """返回官网公开配置：导航、页脚、Banner、简介"""
            cm = ConfigManager()
            async with async_session_factory() as db:
                raw_nav    = await cm.get("site_nav",    db) or _DEFAULT_NAV
                raw_footer = await cm.get("site_footer", db) or _DEFAULT_FOOTER
                raw_banner = await cm.get("site_banner", db) or _DEFAULT_BANNER
                raw_intro  = await cm.get("site_intro",  db) or _DEFAULT_INTRO
            return {
                "module": "site",
                "theme":  theme_manager.get_active("site"),
                "nav":    _json_loads_or(raw_nav,    []),
                "footer": _json_loads_or(raw_footer, []),
                "banner": _json_loads_or(raw_banner, {}),
                "intro":  raw_intro,
            }

        app.include_router(router)

    async def _site_index_response(self, request: Request):
        """按持久化开关生成根路径响应，并阻止浏览器缓存旧入口状态。"""
        visible = await get_config("site_theme_visible")
        response: Response
        if visible == "false":
            response = RedirectResponse(url="/farmer/home", status_code=302)
        else:
            response = await self._render_home(request)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    async def _render_home(self, request: Request) -> HTMLResponse:
        """渲染官网首页：注入导航 / 页脚 / Banner / 简介到 site_theme 主题模板"""
        cm = ConfigManager()
        async with async_session_factory() as db:
            raw_nav    = await cm.get("site_nav",    db) or _DEFAULT_NAV
            raw_footer = await cm.get("site_footer", db) or _DEFAULT_FOOTER
            raw_banner = await cm.get("site_banner", db) or _DEFAULT_BANNER
            raw_intro  = await cm.get("site_intro",  db) or _DEFAULT_INTRO

        # 支持临时预览：URL 带 ?__theme=xxx 时临时使用指定主题渲染
        preview = request.query_params.get("__theme")
        env = theme_manager.get_env("site", preview=preview)
        template = env.get_template("index.html")

        ctx = {
            "request":     request,
            "site_theme":  theme_manager.get_active("site"),
            "site_nav":    _json_loads_or(raw_nav,    []),
            "site_footer": _json_loads_or(raw_footer, []),
            "site_banner": _json_loads_or(raw_banner, {}),
            "site_intro":  raw_intro,
        }
        html = template.render(ctx)
        return HTMLResponse(content=html)


# 全局单例
site_view_controller = SiteViewController()
