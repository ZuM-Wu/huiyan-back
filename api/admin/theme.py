"""
主题设置（模板主题）管理 API

提供后台「主题设置」页所需接口：
    GET  /api/admin/v1/theme/list?module=site|admin|farmer   扫描并返回该模块可用主题清单
    GET  /api/admin/v1/theme/current                         返回三端当前启用主题
    POST /api/admin/v1/theme/activate                        切换指定模块启用主题（持久化 + 清缓存）

主题发现 / 校验 / 环境构建统一委托 core.theme_manager，本模块只做参数校验与配置持久化。
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from core.theme_manager import theme_manager, THEME_MODULES
from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.response import ok
from core.platform.theme import theme_platform

router = APIRouter(prefix="/api/admin/v1/theme", tags=["主题设置"])


@router.get("/list")
async def list_themes(module: str = "admin", _: None = Depends(check_admin)):
    """返回指定模块（site / admin / farmer）扫描到的可用主题清单"""
    if module not in THEME_MODULES:
        raise HTTPException(status_code=400, detail=f"module 仅支持 {' / '.join(THEME_MODULES)}")
    themes = theme_manager.list_themes(module)
    return ok({"module": module, "total": len(themes), "list": themes})


@router.get("/current")
async def current_themes(_: None = Depends(check_admin)):
    """返回三端（site / admin / farmer）当前启用主题标识"""
    return ok({module: theme_manager.get_active(module) for module in THEME_MODULES})


@router.get("/snapshot")
async def theme_snapshot(module: str = "admin", _: None = Depends(check_admin)):
    """返回指定端面的活动主题快照和资源版本。"""
    try:
        return ok(theme_platform.get_active_snapshot(module))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/activate", dependencies=[Depends(require_admin_permission("theme:activate"))])
async def activate_theme(data: dict, request: Request):
    """
    切换指定模块的启用主题。

    请求体: {"module": "site|admin|farmer", "theme": "<主题目录名>"}
    校验主题存在且 base.html 可用后，写入系统配置 {module}_theme，
    并清除对应模块的 Jinja2 环境缓存（下次请求重建，免重启进程）。
    """
    module = (data.get("module") or "").strip()
    theme = (data.get("theme") or "").strip()

    if module not in THEME_MODULES:
        raise HTTPException(status_code=400, detail=f"module 仅支持 {' / '.join(THEME_MODULES)}")
    if not theme:
        raise HTTPException(status_code=400, detail="缺少 theme 参数")
    try:
        snapshot = await theme_platform.activate(module, theme, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ok(snapshot, msg="主题已切换")


@router.post("/rollback", dependencies=[Depends(require_admin_permission("theme:activate"))])
async def rollback_theme(data: dict, request: Request):
    """回退到指定端面的上一份已校验主题快照。"""
    module = (data.get("module") or "").strip()
    try:
        snapshot = await theme_platform.rollback(module, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ok(snapshot, msg="主题已回退")


@router.post("/validate", dependencies=[Depends(require_admin_permission("theme:activate"))])
async def validate_theme(data: dict, _: None = Depends(check_admin)):
    """校验本地主题目录或 ZIP，不激活主题。"""
    module = (data.get("module") or "").strip()
    package_ref = (data.get("package_ref") or "").strip()
    try:
        return ok(theme_platform.validate_theme_package(module, package_ref))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/install", dependencies=[Depends(require_admin_permission("theme:activate"))])
async def install_theme(data: dict, request: Request, _: None = Depends(check_admin)):
    """登记本地主题包；成功后仍保持原活动主题。"""
    module = (data.get("module") or "").strip()
    package_ref = (data.get("package_ref") or "").strip()
    try:
        result = await theme_platform.install(module, package_ref, request=request)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ok(result, msg="主题包已登记，未自动激活")
