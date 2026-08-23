"""系统配置管理 API"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.config_service import (
    get_config as svc_get_config,
    list_configs as svc_list_configs,
    set_config as svc_set_config,
)
from core.log.active_log import active_log
from core.middleware.maintenance import invalidate_maintenance_cache
from core.response import ok

router = APIRouter(prefix="/api/admin/v1/config", tags=["系统配置"])

# 维护模式相关配置键：保存时需同步失效中间件的 TTL 缓存，保证切换即时生效
_MAINTENANCE_KEYS = {"site_maintenance", "maintenance_message"}


# ---------------------------------------------------------------------------
# 官网主题可见性配置（单独读写）
# ---------------------------------------------------------------------------

class SiteThemeVisibleRequest(BaseModel):
    """官网主题可见性配置请求体"""
    value: str  # 'true' 或 'false'


@router.get("/site_theme_visible")
async def get_site_theme_visible(_: None = Depends(check_admin)):
    """读取官网主题可见性配置"""
    raw = await svc_get_config("site_theme_visible")
    # 默认为 true（显示）
    value = raw if raw is not None else "true"
    return ok({"value": value})


@router.post("/site_theme_visible", dependencies=[Depends(require_admin_permission("config:update"))])
async def set_site_theme_visible(data: SiteThemeVisibleRequest, request: Request):
    """设置官网主题可见性（true=显示官网主题，false=隐藏官网主题）"""
    if data.value not in ("true", "false"):
        raise HTTPException(status_code=400, detail="value 必须是 'true' 或 'false'")
    await svc_set_config("site_theme_visible", data.value)
    await active_log(f"设置官网主题可见性：{data.value}", log_type="config", request=request)
    return ok(
        {"value": data.value},
        msg="官网主题已" + ("启用" if data.value == "true" else "关闭"),
    )


# 允许匿名访问的公开品牌字段白名单（仅用于登录页/前台展示，不含任何敏感配置）
_PUBLIC_SITE_KEYS = [
    "site_name", "site_subtitle", "site_logo", "site_favicon",
    "login_bg", "site_logo_url", "site_logo_target",
    "copyright", "record_number",
    # 农户注册配置（供登录页判断是否显示注册入口及必填字段）
    "allow_farmer_register", "farmer_register_phone_required", "farmer_register_email_required",
    # 农户协议链接（供登录/注册页展示）
    "farmer_service_agreement_url", "farmer_privacy_policy_url",
    # 验证码登录 / 注册方式控制（供登录页动态控制 UI 展示）
    "farmer_allow_phone_register", "farmer_phone_register_verify",
    "farmer_phone_password_login", "farmer_phone_sms_login",
    "farmer_allow_email_register", "farmer_email_register_verify",
    "farmer_email_password_login", "farmer_email_code_login",
    "farmer_show_register_switch",
    "farmer_default_login_method", "farmer_default_password_type",
]


@router.get("/site")
async def site_config():
    """获取公开站点品牌配置（无需登录，仅暴露安全的展示字段，供登录页/前台使用）"""
    result = await svc_list_configs(page=1, limit=200)
    # 仅返回白名单中的配置项
    configs = [c for c in result["list"] if c["key"] in _PUBLIC_SITE_KEYS]
    return ok({
        "total": len(configs),
        "list": [{"key": c["key"], "value": c["value"]} for c in configs],
    })


@router.get("/list")
async def list_config(group: str = "", _: None = Depends(check_admin)):
    """获取所有配置（可选按 group 筛选）"""
    result = await svc_list_configs(page=1, limit=500)
    configs = result["list"]
    if group:
        configs = [c for c in configs if c.get("group") == group]
    return ok({
        "total": len(configs),
        "list": configs,
    })


@router.put("/update", dependencies=[Depends(require_admin_permission("config:update"))])
async def update_config(data: dict, request: Request):
    """更新配置（{key: value, ...}）— 仅更新已存在的键"""
    for key, value in data.items():
        existing = await svc_get_config(key)
        if existing is not None:
            await svc_set_config(key, str(value))
    # 命中维护模式配置时立即失效缓存，使开关切换即时生效
    if _MAINTENANCE_KEYS & set(data.keys()):
        invalidate_maintenance_cache()
    await active_log(f"更新系统配置：{list(data.keys())}", log_type="config", request=request)
    return ok({"updated": list(data.keys())}, msg="配置已更新")


@router.put("/batch", dependencies=[Depends(require_admin_permission("config:update"))])
async def batch_save_config(data: dict, request: Request):
    """
    批量保存配置（{key: value, ...}）— 键不存在时自动创建
    与 /update 的区别：此端点支持 UPSERT 语义
    """
    updated = []
    created = []
    for key, value in data.items():
        existing = await svc_get_config(key)
        await svc_set_config(key, str(value))
        if existing is not None:
            updated.append(key)
        else:
            created.append(key)
    # 命中维护模式配置时立即失效缓存，使开关切换即时生效
    if _MAINTENANCE_KEYS & set(data.keys()):
        invalidate_maintenance_cache()
    await active_log(f"批量保存系统配置：更新{len(updated)}项，新增{len(created)}项", log_type="config", request=request)
    return ok(
        {"updated": updated, "created": created},
        msg=f"配置已保存（更新 {len(updated)} 项，新增 {len(created)} 项）",
    )
