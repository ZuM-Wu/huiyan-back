"""
视图控制器
负责后台管理端（/admin/...）页面路由注册与静态资源挂载。

Jinja2 环境（主题解析 / 回退 / 插件模板收集）统一委托 core.theme_manager，
后台使用 admin 模块当前启用主题的环境（默认 templates/admin/vue_default/）。
"""
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.static_files import CachedStaticFiles

logger = logging.getLogger(__name__)


class ViewController:
    """
    后台视图控制器路由映射:
      admin_page()      系统页面  /admin/{page}
      plugin_page()     插件页面  /admin/plugin/{name}/{page}
    模板渲染统一使用 theme_manager 提供的 admin 主题环境。
    """

    def __init__(self):
        self.base_dir = Path(__file__).parent.parent

    def setup(self, app: FastAPI):
        """挂载静态资源并注册路由（admin Jinja2 环境惰性由 theme_manager 构建）"""
        # 1. 挂载共享静态资源（全模块共用：vendor / 令牌 / 公共 JS）
        static_dir = self.base_dir / "templates" / "shared" / "static"
        if static_dir.exists():
            app.mount("/static", CachedStaticFiles(directory=str(static_dir)), name="shared_static")

        # 2. 挂载各模块主题专属静态资源 /theme/{module}/{theme}
        from core.theme_manager import theme_manager
        theme_manager.mount_theme_static(app)
        theme_manager.mount_plugin_static(app)

        # 3. 挂载上传目录（上传文件通过 HTTP 直接访问）
        upload_dir = self.base_dir / "upload"
        upload_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/upload", StaticFiles(directory=str(upload_dir)), name="upload_files")

        # 4. 注册页面路由（必须在 API 路由之后，避免被通配符拦截）
        self._register_routes(app)

        logger.info("[ViewController] 后台页面路由已注册（主题环境委托 theme_manager）")

    def _iframe_page_response(self, request: Request) -> HTMLResponse:
        """iframe 容器页响应（外部链接内嵌显示，含 url 参数安全校验）"""
        url = request.query_params.get('url', '')
        if not url:
            return HTMLResponse(
                content=_error_html("缺少参数", "请提供 url 参数", 400, "/admin/dashboard"),
                status_code=400
            )
        # 安全校验：仅允许 http/https 协议，阻断 javascript:/data: 等注入向量
        if not url.startswith(("http://", "https://")):
            return HTMLResponse(
                content=_error_html("参数非法", "url 参数仅支持 http:// 或 https:// 开头的链接", 400, "/admin/dashboard"),
                status_code=400
            )
        return self._render("iframe_page.html", request, {
            "page": "iframe_page",
            "iframe_url": url,
            "iframe_title": "外部页面"
        })

    def _register_routes(self, app: FastAPI):
        """注册页面路由"""

        # ---- 登录页（独立布局，不继承 base.html）----
        @app.get("/admin/login", response_class=HTMLResponse)
        async def admin_login(request: Request):
            return self._render("login.html", request, {"page": "login"})

        # ---- 插件页面: /admin/plugin/{name}/{page} ----
        @app.get("/admin/plugin/{name}/{page}", response_class=HTMLResponse)
        async def plugin_page(request: Request, name: str, page: str):
            from core.plugin_pages import resolve_plugin_page
            declaration = resolve_plugin_page(name, page.removesuffix(".html"), "admin")
            if not declaration:
                return HTMLResponse(
                    content=_error_html("插件页面不存在", f"插件 '{name}' 未声明页面 '{page}'", 404, "/admin/dashboard"),
                    status_code=404,
                )
            try:
                return self._render(declaration["template"], request, {
                    "page": page,
                    "plugin_name": name,
                    "plugin_page": declaration,
                })
            except Exception:
                return HTMLResponse(
                    content=_error_html("插件页面不存在", f"插件 '{name}' 的页面 '{page}' 未找到", 404, "/admin/dashboard"),
                    status_code=404
                )

        # ---- 系统页面: /admin/{page} ----
        @app.get("/admin/{page}", response_class=HTMLResponse)
        async def admin_page(request: Request, page: str):
            # 特殊页面：iframe 容器（校验与渲染抽取至 _iframe_page_response，降低本函数复杂度）
            if page == 'iframe_page':
                return self._iframe_page_response(request)
            # 已合并页面重定向（缓存管理已合并到系统设置的“系统缓存”Tab）
            if page == 'cache':
                return RedirectResponse(url="/admin/system")
            # 已移除页面重定向（地块管理已并入产区详情页，独立地块页不再保留）
            if page == 'plot':
                return RedirectResponse(url="/admin/production-area")
            # 旧版通知页面重定向到新路径
            if page in ('notice-actions', 'notice-test'):
                return RedirectResponse(url="/admin/notice-sms")
            if page == 'notice-sms-templates':
                return RedirectResponse(url="/admin/notice-sms")
            if page == 'notice-email-templates':
                return RedirectResponse(url="/admin/notice-email")
            # 已移除页面重定向（API规则白名单机制已清理，权限由路由级 require_permission 控制）
            if page == 'rule':
                return RedirectResponse(url="/admin/dashboard")
            tpl_name = f"{page}.html" if not page.endswith(".html") else page
            try:
                return self._render(tpl_name, request, {"page": page})
            except Exception:
                return HTMLResponse(
                    content=_error_html("页面不存在", f"页面 '{page}' 未找到", 404, "/admin/dashboard"),
                    status_code=404
                )

        # ---- 首页重定向 ----
        @app.get("/admin")
        async def admin_index():
            return RedirectResponse(url="/admin/dashboard")

    def _render(self, template_name: str, request: Request, context: dict) -> HTMLResponse:
        """使用 admin 主题 Jinja2 环境渲染模板并返回 HTML 响应"""
        from core.theme_manager import theme_manager
        try:
            env = theme_manager.get_env("admin")
            template = env.get_template(template_name)
            ctx = {"request": request}
            ctx.update(context)
            html = template.render(ctx)
            return HTMLResponse(content=html)
        except Exception as e:
            logger.error(f"[ViewController] 渲染模板 '{template_name}' 失败: {e}", exc_info=True)
            raise


# 错误状态码对应的标题和图标
_ERROR_META = {
    400: ("请求错误", "400", "请求参数有误，请检查后重试"),
    403: ("无权访问", "403", "您没有权限访问此页面"),
    404: ("页面不存在", "404", "请求的页面未找到"),
    500: ("服务器错误", "500", "服务器内部异常，请稍后重试"),
    503: ("服务不可用", "503", "系统正在维护中，请稍后访问"),
}


def _error_html(title: str, message: str, status_code: int = 404, back_url: str = "/") -> str:
    """统一错误页 HTML 模板

    参数:
        title: 页面标题
        message: 错误详情描述
        status_code: HTTP 状态码，用于显示大号数字和配色
        back_url: “返回首页”链接地址，根据请求路径智能判断
    """
    meta = _ERROR_META.get(status_code)
    display_code = meta[1] if meta else str(status_code)
    default_msg = meta[2] if meta else message
    # 403/500 用红色，其他用橙色
    accent = "#e34d59" if status_code in (403, 500) else "#ed7b2f"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - 慧眼护农</title>
<style>
body{{font-family:"Microsoft YaHei",system-ui,sans-serif;display:flex;align-items:center;justify-content:center;
  min-height:100vh;margin:0;background:#f5f5f5;color:#333}}
.card{{text-align:center;background:#fff;padding:48px 64px;border-radius:12px;
  box-shadow:0 2px 12px rgba(0,0,0,.06);max-width:480px;width:90%}}
.code{{font-size:80px;font-weight:700;margin:0;color:{accent};line-height:1;letter-spacing:-2px}}
.title{{font-size:20px;font-weight:600;margin:12px 0 8px;color:#333}}
.msg{{color:#86909c;font-size:14px;margin:0 0 24px;line-height:1.6}}
a{{display:inline-block;color:#0052d9;text-decoration:none;padding:8px 24px;
  border:1px solid #0052d9;border-radius:4px;font-size:14px;transition:all .2s}}
a:hover{{background:#0052d9;color:#fff;text-decoration:none}}
</style></head>
<body><div class="card">
  <p class="code">{display_code}</p>
  <p class="title">{title}</p>
  <p class="msg">{message or default_msg}</p>
  <a href="{back_url}">返回首页</a>
</div></body></html>"""


# 全局单例
view_controller = ViewController()
