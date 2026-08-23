"""缓存管理 API"""
from fastapi import APIRouter, Depends

from core.auth.rbac import require_admin_permission
from core.cache.cache_manager import cache_manager
from core.response import ok

router = APIRouter(prefix="/api/admin/v1/cache", tags=["缓存管理"])


@router.post("/clear_all", dependencies=[Depends(require_admin_permission("cache:clear"))])
async def clear_all():
    """清除所有缓存"""
    await cache_manager.clear()
    return ok(msg="所有缓存已清除")


@router.post("/clear_plugin", dependencies=[Depends(require_admin_permission("cache:clear"))])
async def clear_plugin():
    """清除插件相关缓存"""
    await cache_manager.delete(cache_manager.CACHE_PLUGIN_LIST)
    await cache_manager.delete(cache_manager.CACHE_PLUGIN_HOOKS)
    return ok(msg="插件缓存已清除")


@router.post("/clear_config", dependencies=[Depends(require_admin_permission("cache:clear"))])
async def clear_config():
    """清除配置缓存"""
    await cache_manager.delete(cache_manager.CACHE_CONFIGURATION)
    return ok(msg="配置缓存已清除")


@router.post("/clear_permission", dependencies=[Depends(require_admin_permission("cache:clear"))])
async def clear_permission():
    """清除权限缓存"""
    await cache_manager.delete(cache_manager.CACHE_PERMISSION)
    await cache_manager.delete(cache_manager.CACHE_MENU)
    return ok(msg="权限/菜单缓存已清除")
