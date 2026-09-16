"""慧眼护农 3.4.1 主入口
单进程: FastAPI 托管 API + 前端静态文件

部署约束: 仅支持单 worker 部署（uvicorn 默认单进程）。
系统内的防重复提交缓存、频控计数、插件禁用集合等均为进程内内存状态，
多 worker 部署会导致状态漂移与安全机制失效，严禁使用 --workers > 1。
"""
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.lifespan import startup, shutdown
from core.exception_handlers import register_exception_handlers
from core.view_controller import view_controller
from core.view_farmer import farmer_view_controller
from core.view_site import site_view_controller
from core.middleware.maintenance import MaintenanceMiddleware
from core.pt_model_safety import enable_safe_pt_loading

# AgentScope Service：固定使用 agentscope==2.0.7。
# AgentScope 是 AI 子系统唯一运行链路，导入失败时直接暴露启动错误。
try:
    from services.agentscope.runtime import (
        agentscope_app,
        install_chat_failure_logging,
    )
    from services.agentscope.shutdown import shutdown_signal_proxy
except Exception as exc:  # pragma: no cover - 仅覆盖未安装可选依赖的降级环境
    agentscope_app = None
    install_chat_failure_logging = None
    shutdown_signal_proxy = None
    logging.getLogger(__name__).error("AgentScope Service 导入失败: %s", exc)

# 核心路由
from api.admin.auth import router as admin_auth_router
from api.admin.plugin import router as admin_plugin_router, platform_router as admin_plugin_platform_router
from api.admin.plugin_database import router as admin_plugin_database_router
from api.admin.config import router as admin_config_router
from api.admin.cache import router as admin_cache_router
from api.admin.menu import router as admin_menu_router
from api.admin.log import router as admin_log_router
from api.admin.widget import router as admin_widget_router
from api.admin.admin import router as admin_admin_router
from api.admin.role import router as admin_role_router
from api.admin.permission import router as admin_permission_router
from api.admin.farmer import router as admin_farmer_router
from api.admin.certification import router as admin_certification_router
from api.admin.system_info import router as admin_system_info_router
from api.admin.upload import router as admin_upload_router
from api.admin.oss_config import router as admin_oss_config_router
from api.storage import router as storage_router
from api.admin.profile import router as admin_profile_router
from api.admin.theme import router as admin_theme_router
from api.admin.site_config import router as admin_site_config_router
from api.admin.production import router as admin_production_router
from api.admin.notice import router as admin_notice_action_router
from api.admin.notice_sms import router as admin_notice_sms_router
from api.admin.notice_email import router as admin_notice_email_router
from api.admin.notice_log import router as admin_notice_log_router
from api.admin.notice_send import router as core_notice_send_router
from api.admin.notice_interface import router as admin_notice_interface_router
from api.admin.notice_params import router as admin_notice_params_router
from api.admin.weather import router as admin_weather_router
from api.admin.weather_alert import router as admin_weather_alert_router
from api.admin.task_monitor import router as admin_task_monitor_router
from api.admin.task_queue import router as admin_task_queue_router
from api.admin.api_key import router as admin_api_key_router
from api.admin.ai_resources import router as admin_ai_resources_router
from api.admin.inbox import router as admin_inbox_router
from api.admin.hardware_device import (
    marker_router as admin_hardware_marker_router,
    router as admin_hardware_device_router,
)
from api.farmer.auth import router as farmer_auth_router
from api.farmer.verify_code import router as farmer_verify_code_router
from api.farmer.password_reset import router as farmer_password_reset_router
from api.farmer.profile import router as farmer_profile_router
from api.farmer.upload import router as farmer_upload_router
from api.farmer.menu import router as farmer_menu_router
from api.farmer.certification import router as farmer_certification_router
from api.farmer.production_area import router as farmer_production_area_router
from api.farmer.weather import router as farmer_weather_router
from api.farmer.api_key import router as farmer_api_key_router
from api.farmer.inbox import router as farmer_inbox_router
from api.farmer.plugins import router as farmer_plugins_router
from api.farmer.widget import router as farmer_widget_router

def _configure_logging() -> None:
    """配置统一应用日志；文件不可写时保留 stderr 并继续启动。"""
    level_name = str(settings.LOG_LEVEL or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    log_file = str(settings.LOG_FILE or "").strip()
    if not log_file:
        return
    target = str(Path(log_file).expanduser().resolve())
    if any(getattr(handler, "_huiyan_log_file", "") == target for handler in root_logger.handlers):
        return
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        handler._huiyan_log_file = target  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        ))
        root_logger.addHandler(handler)
    except OSError:
        root_logger.exception("[日志降级] LOG_FILE 不可写，继续使用 stderr: %s", target)


_configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期上下文管理器（替代 on_event）"""
    signal_context = (
        shutdown_signal_proxy.installed()
        if shutdown_signal_proxy is not None
        else nullcontext()
    )
    with signal_context:
        started = False
        try:
            await startup(app)
            started = True
            if agentscope_app is not None:
                async with agentscope_app.router.lifespan_context(agentscope_app):
                    install_chat_failure_logging()
                    yield
            else:
                yield
        finally:
            if started:
                await shutdown()
# MCP 服务接线（MCP_ENABLED=true 时启用）
# 历史坑: FastMCP 子应用自带 lifespan（初始化 session manager），
# 不用 combine_lifespans 合并则 MCP 请求会挂死；合并后退出顺序为
# 先停 MCP 再走 shutdown（保证 MCP 关闭后才 dispose 数据库引擎）
if settings.MCP_ENABLED:
    from fastmcp.utilities.lifespan import combine_lifespans
    from services.mcp.server import build_http_app
    from services.mcp.registry import register_core_capabilities

    mcp_app = build_http_app()
    register_core_capabilities()
    _app_lifespan = combine_lifespans(lifespan, mcp_app.lifespan)
else:
    mcp_app = None
    _app_lifespan = lifespan

# PT 权重的 safe_only 安全加载开关必须在插件动态导入 ultralytics 之前生效。
enable_safe_pt_loading()

app = FastAPI(title="慧眼护农 3.4.1", version="3.4.1", lifespan=_app_lifespan)

if agentscope_app is not None:
    # AgentScope 原生资源路由统一形成 /api/ai 入口。
    app.mount("/api/ai", agentscope_app)


@app.middleware("http")
async def platform_request_context(request: Request, call_next):
    """为请求、审计和异步任务绑定统一 request_id，并在响应中回传。"""
    from core.platform.context import bind_context, clear_context

    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    token = bind_context(request_id=request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        clear_context(token)


# CORS 跨域配置 — 从 Settings 读取，生产环境收窄为具体域名
_cors_origins = (
    ["*"]
    if settings.CORS_ORIGINS.strip() == "*"
    else [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
)
# 通配源(*)与 allow_credentials=True 组合存在 CSRF/凭据泄露风险，强制关闭凭据携带
# 前端统一使用 localStorage + Authorization Bearer 头认证，不依赖 Cookie，无感知影响
_allow_credentials = _cors_origins != ["*"]
if not _allow_credentials:
    logging.getLogger(__name__).warning(
        "[CORS] 当前为通配源(*)模式，已强制 allow_credentials=False；生产环境请在 .env 中配置 CORS_ORIGINS 为具体域名"
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 维护模式中间件（在 CORS 之后注册，优先拦截页面请求）
app.add_middleware(MaintenanceMiddleware)

# 全局异常处理器
register_exception_handlers(app)

# API 路由（优先级高于静态文件）
app.include_router(admin_auth_router)
app.include_router(admin_plugin_router)
app.include_router(admin_plugin_platform_router)
app.include_router(admin_plugin_database_router)
app.include_router(admin_config_router)
app.include_router(admin_cache_router)
app.include_router(admin_menu_router)
app.include_router(admin_log_router)
app.include_router(admin_widget_router)
app.include_router(farmer_widget_router)
app.include_router(admin_admin_router)
app.include_router(admin_role_router)
app.include_router(admin_permission_router)
app.include_router(admin_farmer_router)
app.include_router(admin_certification_router)
app.include_router(admin_system_info_router)
app.include_router(admin_upload_router)
app.include_router(admin_oss_config_router)
app.include_router(storage_router)
app.include_router(admin_profile_router)
app.include_router(admin_theme_router)
app.include_router(admin_site_config_router)
app.include_router(admin_production_router)
app.include_router(admin_notice_action_router)
app.include_router(admin_notice_sms_router)
app.include_router(admin_notice_email_router)
app.include_router(admin_notice_log_router)
app.include_router(core_notice_send_router)
app.include_router(admin_notice_interface_router)
app.include_router(admin_notice_params_router)
app.include_router(admin_weather_router)
app.include_router(admin_weather_alert_router)
app.include_router(admin_task_monitor_router)
app.include_router(admin_task_queue_router)
app.include_router(admin_api_key_router)
app.include_router(admin_ai_resources_router)
app.include_router(admin_inbox_router)
app.include_router(admin_hardware_device_router)
app.include_router(admin_hardware_marker_router)
app.include_router(farmer_auth_router)
app.include_router(farmer_verify_code_router)
app.include_router(farmer_password_reset_router)
app.include_router(farmer_profile_router)
app.include_router(farmer_upload_router)
app.include_router(farmer_menu_router)
app.include_router(farmer_certification_router)
app.include_router(farmer_production_area_router)
app.include_router(farmer_weather_router)
app.include_router(farmer_api_key_router)
app.include_router(farmer_inbox_router)
app.include_router(farmer_plugins_router)

# MCP 服务挂载（Streamable HTTP，位于 API 路由之后、页面路由之前）
if mcp_app is not None:
    from services.mcp.server import McpPathMiddleware
    app.add_middleware(McpPathMiddleware, mount_path=settings.MCP_MOUNT_PATH)
    app.mount(settings.MCP_MOUNT_PATH, mcp_app)

# 官网公开 API（GET /api/site/v1/config，无需登录）
# 由 site_view_controller 内部注册（含一个 APIRouter）
site_view_controller.setup(app)

# 前端页面路由（MPA 模式）
# 必须在所有 API 路由之后注册，避免 /admin/{page} 拦截 API 请求
view_controller.setup(app)
farmer_view_controller.setup(app)


@app.get("/health")
async def health(request: Request):
    """健康检查端点 — 返回服务状态与降级组件列表

    启动流程中任一组件容错失败时，status 为 "degraded" 并列出失败组件名；
    全部正常时 status 为 "ok"，components 为空列表。
    """
    degraded = list(getattr(request.app.state, "degraded_components", []))
    platform = getattr(request.app.state, "platform_health", None)
    if platform:
        degraded.extend(
            item for item in platform.degraded_components()
            if item not in degraded
        )
    platform_components = platform.snapshot() if platform else {}
    if platform:
        from core.platform.resource import resource_registry
        resource_components = resource_registry.health_snapshot()
        previous_resource = platform_components.get("resource_registry", {})
        if previous_resource.get("status") == "degraded":
            resource_components.update(previous_resource)
        platform_components["resource_registry"] = resource_components
    if degraded:
        return {
            "code": 200, "status": "degraded", "components": degraded,
            "platform": platform_components, "version": "3.4.1",
        }
    return {
        "code": 200, "status": "ok", "components": [],
        "platform": platform_components, "version": "3.4.1",
    }
