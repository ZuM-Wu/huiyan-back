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
      theme_manager 自动回退 default；此处无需额外处理。
"""
import json
import logging

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.db.base import async_session_factory
from core.config_manager import ConfigManager
from core.theme_manager import theme_manager
from core.auth.jwt_handler import verify_jwt

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
            # 检查官网主题可见性配置
            cm = ConfigManager()
            async with async_session_factory() as db:
                visible = await cm.get("site_theme_visible", db)

            # 如果官网主题被禁用，根据登录状态跳转到农户首页或登录页
            if visible == "false":
                # 检查是否已登录（通过 farmer token）
                token = request.cookies.get("farmer_token")
                if token:
                    # 验证 token 有效性
                    payload = verify_jwt(token, is_admin=False)
                    if payload:
                        # 已登录，跳转到农户首页
                        return RedirectResponse(url="/farmer/home", status_code=302)
                # 未登录或 token 无效，跳转到农户登录页
                return RedirectResponse(url="/farmer/login", status_code=302)

            # 官网主题启用，渲染官网首页
            return await self._render_home(request)

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
