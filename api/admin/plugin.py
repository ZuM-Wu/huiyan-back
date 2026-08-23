"""插件管理 API"""

import logging
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel
from typing import Dict, Any

from core.db.base import async_session_factory
from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.config_service import get_config, set_config
from core.plugin_query_service import list_all_plugins, get_plugin_by_name, MODULE_LABELS
from core.log.active_log import active_log
from core.response import ok
from services.upload_policy import build_policy_items, plugin_policy_definition
from core.platform.plugin import plugin_platform

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1/plugin", tags=["插件管理"])
platform_router = APIRouter(prefix="/api/admin/v1/plugins", tags=["插件平台"])


def _managers(request: Request):
    """取 lifespan 挂载的全局单例（插件管理器 + 路由管理器）

    统一从 app.state 获取，禁止端点内新建实例——
    新建会丢失 _loaded/_registered_plugins/_disabled 状态，导致重复挂载路由。
    """
    return request.app.state.plugin_manager, request.app.state.router_manager

# 应用列表默认排除的类型（由各自独立管理页维护）
_EXCLUDE_FROM_APP_LIST = {
    "sms", "mail", "certification", "oss", "weather", "llm",
}
_REMOVED_PLUGIN_NAMES = {"admin_notifier"}


def _visible_in_app_list(plugin: dict) -> bool:
    """判断插件是否应出现在通用应用页。"""
    return (
        plugin.get("module") not in _EXCLUDE_FROM_APP_LIST
        and plugin.get("name") not in _REMOVED_PLUGIN_NAMES
    )


def _visible_in_discovery(plugin: dict, scope: str) -> bool:
    """按发现范围过滤插件，同时始终隐藏明确退役的插件。"""
    if plugin.get("name") in _REMOVED_PLUGIN_NAMES:
        return False
    return scope == "all" or _visible_in_app_list(plugin)

@router.get("/list")
async def list_plugins(
    module: str = Query(None, description="模块筛选: addon/gateway/sms/mail/...，不传则默认排除 sms/mail/certification"),
    _: None = Depends(check_admin),
):
    """获取已安装的插件列表

    不传 module 时默认排除由独立管理页维护的模块（短信/邮件/实名/存储/天气/AI）。
    传 module=sms 则只返回短信类插件。
    """
    all_plugins = await list_all_plugins()
    if module:
        plugins = [p for p in all_plugins if p["module"] == module]
    else:
        plugins = [p for p in all_plugins if _visible_in_app_list(p)]
    return ok({
        "total": len(plugins),
        "list": [
            {
                "id": p["id"], "name": p["name"], "title": p["title"],
                "version": p["version"], "author": p.get("author", "") or "",
                "module": p["module"],
                "module_label": MODULE_LABELS.get(p["module"], p["module"]),
                "status": p["status"],
                "install_time": p.get("install_time")
            }
            for p in plugins
        ]
    })


@router.get("/discover")
async def discover_plugins(
    request: Request,
    scope: str = Query("apps", pattern="^(apps|all)$", description="扫描范围: apps/all"),
    _: None = Depends(check_admin),
):
    """扫描 plugins/ 目录，返回磁盘与安装版本状态。"""
    pm, _mgr = _managers(request)
    discovered = pm.discover()
    discovered = [d for d in discovered if _visible_in_discovery(d, scope)]

    # 通过 service 层获取已安装插件信息
    all_plugins = await list_all_plugins()
    installed_map = {p["name"]: p for p in all_plugins}

    result_list = []
    for d in discovered:
        meta = {}
        try:
            meta = pm.load_metadata(d["name"])
        except Exception as e:
            logger.warning("[插件管理] 加载插件元数据失败 %s: %s", d["name"], e)
        disk_version = str(meta.get("version") or d.get("version") or "")
        installed = installed_map.get(d["name"])
        installed_ver = str(installed.get("version") or "") if installed else ""
        is_installed = installed is not None
        upgrade_available = False
        version_state = "invalid" if not disk_version else "not_installed"
        if is_installed and disk_version:
            try:
                comparison = pm.compare_versions(disk_version, installed_ver)
                if comparison > 0:
                    version_state = "update_available"
                    upgrade_available = True
                elif comparison < 0:
                    version_state = "local_newer"
                else:
                    version_state = "current"
            except ValueError:
                version_state = "invalid"
                logger.warning("插件 %s 版本号无效，无法比较版本状态", d["name"])
        result_list.append({
            "name": d["name"],
            "module": d["module"],
            "module_label": MODULE_LABELS.get(d["module"], d["module"]),
            "title": meta.get("title", d["name"]),
            "version": disk_version,
            "installed_version": installed_ver if is_installed else "",
            "author": meta.get("author", ""),
            "description": meta.get("description", ""),
            "installed": is_installed,
            "status": installed.get("status") if installed else None,
            "upgrade_available": upgrade_available,
            "version_state": version_state,
        })
    return ok({"total": len(result_list), "list": result_list})


@router.post("/install/{name}", dependencies=[Depends(require_admin_permission("plugin:install"))])
async def install_plugin(name: str, request: Request):
    """安装插件（安装后自动注册路由）"""
    pm, router_mgr = _managers(request)
    async with async_session_factory() as db:
        success = await pm.install(name, db)
        if not success:
            raise HTTPException(status_code=500, detail=f"插件 '{name}' 安装失败")

    # 动态注册插件路由（已注册则短路），并解除可能的历史禁用门禁（卸载后重装场景）
    router_mgr.register_plugin_router(name)
    router_mgr.mark_enabled(name)

    await active_log(f"安装插件：{name}", log_type="plugin", request=request)
    return ok(msg=f"插件 '{name}' 安装成功")


@router.post("/uninstall/{name}", dependencies=[Depends(require_admin_permission("plugin:uninstall"))])
async def uninstall_plugin(name: str, request: Request):
    """卸载插件（已挂载路由经门禁即时 404）"""
    pm, router_mgr = _managers(request)
    async with async_session_factory() as db:
        success = await pm.uninstall(name, db, router_manager=router_mgr)
        if not success:
            raise HTTPException(status_code=500, detail=f"插件 '{name}' 卸载失败")
        await active_log(f"卸载插件：{name}", log_type="plugin", request=request)
        return ok(msg=f"插件 '{name}' 卸载成功")


@router.post("/enable/{name}", dependencies=[Depends(require_admin_permission("plugin:status"))])
async def enable_plugin(name: str, request: Request):
    """启用插件（钩子恢复 + 路由门禁放行，无需重启即时可用）"""
    pm, router_mgr = _managers(request)
    async with async_session_factory() as db:
        success = await pm.enable(name, db, router_manager=router_mgr)
        if not success:
            raise HTTPException(status_code=503, detail={
                "code": "plugin_enable_failed", "message": f"插件 '{name}' 启用失败",
            })
        await active_log(f"启用插件：{name}", log_type="plugin", request=request)
        return ok(msg=f"插件 '{name}' 已启用")


@router.post("/disable/{name}", dependencies=[Depends(require_admin_permission("plugin:status"))])
async def disable_plugin(name: str, request: Request):
    """禁用插件（注销钩子 + 路由门禁拦截，即时生效）"""
    pm, router_mgr = _managers(request)
    async with async_session_factory() as db:
        success = await pm.disable(name, db, router_manager=router_mgr)
        if not success:
            raise HTTPException(status_code=503, detail={
                "code": "plugin_disable_failed", "message": f"插件 '{name}' 禁用失败",
            })
        await active_log(f"禁用插件：{name}", log_type="plugin", request=request)
        return ok(msg=f"插件 '{name}' 已禁用")


@router.post("/upgrade/{name}", dependencies=[Depends(require_admin_permission("plugin:upgrade"))])
async def upgrade_plugin(name: str, request: Request):
    """升级插件。

    接口路径: POST /api/admin/v1/plugin/upgrade/{name}
    请求参数: 路径参数 name（插件唯一标识），请求体为空。
    成功响应: 标准成功信封，message 为“插件 '{name}' 升级成功”。
    错误码: not_installed(404)、already_latest(409)、downgrade_rejected(409)、
    upgrade_in_progress(409)、migration_invalid/migration_failed(422)、
    runtime_refresh_failed(503)。错误响应 detail 包含 code 和 message。
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        plan = await plugin_platform.prepare_update(
            name, str(body.get("package_ref") or ""), request=request, enqueue_task=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "update_prepare_failed", "message": str(exc),
        }) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "update_unavailable", "message": str(exc),
        }) from exc
    try:
        confirmed = await plugin_platform.confirm_update(
            name, plan["operation_id"],
            identity=str(getattr(request.state, "user_name", "admin") or "admin"),
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "update_confirm_failed", "message": str(exc)}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={"code": "update_unavailable", "message": str(exc)}) from exc
    return ok(confirmed, msg="插件更新计划已确认，等待停机重启应用")


@platform_router.post("/{name}/updates/prepare", dependencies=[Depends(require_admin_permission("plugin:upgrade"))])
async def prepare_plugin_update(name: str, request: Request):
    """预检本地插件包并生成短期更新计划，不替换运行中代码。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        plan = await plugin_platform.prepare_update(
            name, str(body.get("package_ref") or ""),
            identity=str(getattr(request.state, "user_name", "admin") or "admin"),
            request=request, enqueue_task=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "update_prepare_failed", "message": str(exc)}) from exc
    return ok(plan, msg="插件更新预检完成")


@platform_router.post("/{name}/updates/confirm", dependencies=[Depends(require_admin_permission("plugin:upgrade"))])
async def confirm_plugin_update(name: str, request: Request):
    """确认插件更新计划；固定返回 restart_required=true。"""
    try:
        body = await request.json()
        operation_id = str(body.get("operation_id") or "")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体格式错误") from exc
    try:
        plan = await plugin_platform.confirm_update(
            name, operation_id,
            identity=str(getattr(request.state, "user_name", "admin") or "admin"),
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "update_confirm_failed", "message": str(exc)}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={"code": "update_locked", "message": str(exc)}) from exc
    return ok(plan, msg="插件更新已确认，等待停机重启应用")


@platform_router.post("/{name}/updates/rollback", dependencies=[Depends(require_admin_permission("plugin:upgrade"))])
async def rollback_plugin_update(name: str, request: Request):
    """生成插件回滚计划，不在 Web 进程替换文件。"""
    try:
        body = await request.json()
        operation_id = str(body.get("operation_id") or "")
        plan = await plugin_platform.rollback_update(name, operation_id, request=request)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail={"code": "rollback_failed", "message": str(exc)}) from exc
    return ok(plan, msg="插件回滚计划已生成")


@platform_router.get("/{name}/runtime", dependencies=[Depends(check_admin)])
async def plugin_runtime_snapshot(name: str, request: Request):
    """查询插件 owner、路由、资源、任务和事件状态。"""
    pm, router_mgr = _managers(request)
    return ok(await plugin_platform.runtime_snapshot(
        name, manager=pm, router_manager=router_mgr,
    ))


# ================================================================
# 插件配置管理
# ================================================================

class PluginConfigSave(BaseModel):
    """插件配置保存请求"""
    config: Dict[str, Any]


@router.get("/config/{name}")
async def get_plugin_config(name: str, request: Request, _: None = Depends(check_admin)):
    """读取插件配置（配置 Schema + 当前值）"""
    pm, _mgr = _managers(request)
    schema = pm.get_config_schema(name)
    upload_schema = pm.get_upload_policy_schema(name)

    # 读取当前配置值（通过 config_service 管理自己的会话）
    current = {}
    for item in schema:
        key = f"{name}.{item['key']}"
        val = await get_config(key)
        current[item["key"]] = val if val is not None else item.get("default", "")

    upload_policies = []
    if upload_schema:
        plugin = await get_plugin_by_name(name) or {
            "name": name, "title": name, "status": None,
        }
        definitions = [
            plugin_policy_definition(plugin, item) for item in upload_schema
        ]
        upload_policies, _ = await build_policy_items(definitions)

    return ok({
        "schema": schema,
        "current": current,
        "upload_policies": upload_policies,
        "upload_settings_url": "/admin/system?tab=upload",
    })


@router.put("/config/{name}", dependencies=[Depends(require_admin_permission("plugin:config"))])
async def save_plugin_config(
    name: str,
    data: PluginConfigSave,
    request: Request,
):
    """保存插件配置"""
    pm, _mgr = _managers(request)
    migrated_keys = {
        legacy_key
        for item in pm.get_upload_policy_schema(name)
        for legacy_key in (item.get("legacy_keys") or {}).values()
    }
    blocked = sorted(set(data.config) & migrated_keys)
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "upload_policy_managed",
                "message": "上传规则已迁移到系统设置的上传设置页面",
                "fields": blocked,
                "url": "/admin/system?tab=upload",
            },
        )
    for key, value in data.config.items():
        full_key = f"{name}.{key}"
        await set_config(full_key, str(value))
    await active_log(f"保存插件配置：{name}", log_type="plugin", request=request)
    return ok(msg="配置已保存")
