"""
农户端视图控制器

负责农户端（/farmer/...）页面的 Jinja2 模板渲染与路由注册。
主题解析、回退、Environment 构建统一委托 core.theme_manager。

路由:
    GET /farmer                       按登录态重定向（登录态由前端判断，此处默认落 home）
    GET /farmer/login                 农户端登录页（独立布局）
    GET /farmer/plugin/{name}/{page}  插件农户端页面
    GET /farmer/{page}                 农户端系统页面
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.theme_manager import theme_manager
from core.view_controller import _error_html

logger = logging.getLogger(__name__)


class FarmerViewController:
    """农户端页面路由映射"""

    def setup(self, app: FastAPI):
        """注册农户端页面路由（须在所有 API 路由之后调用）"""
        self._register_routes(app)
        logger.info("[FarmerViewController] 农户端页面路由已注册")

    def _register_routes(self, app: FastAPI):
        # ---- 农户端首页入口：按登录态重定向 ----
        # 页面本身无法读取 localStorage，登录态跳转由前端脚本兜底；
        # 服务端默认落 /farmer/home，未登录时前端 401 拦截会跳 /farmer/login。
        @app.get("/farmer")
        async def farmer_index():
            return RedirectResponse(url="/farmer/home")

        # ---- 登录页（独立布局，不继承 base.html）----
        @app.get("/farmer/login", response_class=HTMLResponse)
        async def farmer_login(request: Request):
            return self._render("login.html", request, {"page": "login"})

        # ---- 插件农户端页面: /farmer/plugin/{name}/{page} ----
        @app.get("/farmer/plugin/{name}/{page}", response_class=HTMLResponse)
        async def farmer_plugin_page(request: Request, name: str, page: str):
            from core.plugin_pages import resolve_plugin_page
            declaration = resolve_plugin_page(name, page.removesuffix(".html"), "farmer")
            if not declaration:
                return HTMLResponse(
                    content=_error_html("插件页面不存在", f"插件 '{name}' 未声明农户端页面 '{page}'"),
                    status_code=404,
                )
            try:
                return self._render(declaration["template"], request, {
                    "page": page, "plugin_name": name, "plugin_page": declaration,
                })
            except Exception:
                return HTMLResponse(
                    content=_error_html("插件页面不存在", f"插件 '{name}' 的农户端页面 '{page}' 未找到"),
                    status_code=404,
                )

        # ---- 站内信详情页: /farmer/inbox/{message_id} ----
        # 必须在 /farmer/{page} 之前注册，避免 message_id 被当作 page 参数
        @app.get("/farmer/inbox/{message_id}", response_class=HTMLResponse)
        async def farmer_inbox_detail(request: Request, message_id: int):
            return self._render("inbox_detail.html", request, {"page": "inbox_detail", "message_id": message_id})

        # ---- 农户端系统页面: /farmer/{page} ----
        @app.get("/farmer/{page}", response_class=HTMLResponse)
        async def farmer_page(request: Request, page: str):
            tpl_name = f"{page}.html" if not page.endswith(".html") else page
            try:
                return self._render(tpl_name, request, {"page": page})
            except Exception:
                return HTMLResponse(
                    content=_error_html("页面不存在", f"农户端页面 '{page}' 未找到"),
                    status_code=404,
                )

    def _render(self, template_name: str, request: Request, context: dict) -> HTMLResponse:
        """使用农户端主题 Jinja2 环境渲染模板

        支持临时预览：URL 携带 `?__theme=xxx` 时临时使用指定主题渲染
        （非法值自动回退农户端默认主题），不改变系统当前启用主题。
        """
        preview = request.query_params.get("__theme")
        env = theme_manager.get_env("farmer", preview=preview)
        template = env.get_template(template_name)
        ctx = {"request": request}
        ctx.update(context)
        return HTMLResponse(content=template.render(ctx))


# 全局单例
farmer_view_controller = FarmerViewController()
