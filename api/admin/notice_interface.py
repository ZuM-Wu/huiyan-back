"""
通知插件接口管理 API（管理员端）
列出所有已安装的 sms/mail 插件，支持查看信息、测试连接、编辑配置。
插件配置经 ConfigManager 存储于 hy_configuration（key 格式 {plugin_name}.{key}）。
"""
import logging
import importlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok
from core.plugin_query_service import (
    list_plugins_by_names,
    list_orphan_plugins,
    get_plugin_config_map,
    get_single_plugin_config,
    save_plugin_config_and_maybe_enable,
    toggle_plugin_with_pm,
    get_plugin_by_name,
)
from core.notice_template_service import sync_sms_templates_from_remote

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/notice/interfaces", tags=["通知接口管理"])


class PluginConfigUpsert(BaseModel):
    """插件配置保存请求"""
    config: dict


@router.get("/list", dependencies=[Depends(require_permission("notice:config"))])
async def list_notice_plugins(
    request: Request,
    module: str = Query(None, description="模块筛选: sms/mail，不传则返回全部"),
    _: None = Depends(check_admin),
):
    """
    列出通知插件及其配置与状态

    可通过 module 参数筛选 sms 或 mail 类型插件。
    返回每个插件的: name/title/description/version/module/config_schema/config/status
    """
    pm = request.app.state.plugin_manager
    discovered = pm.discover()
    # 按 module 参数过滤；不传则返回 sms + mail 全部
    if module:
        notice_plugins = [p for p in discovered if p["module"] == module]
    else:
        notice_plugins = [p for p in discovered if p["module"] in ("sms", "mail")]

    # 查询 hy_plugin 表中已注册的插件记录
    plugin_names = [p["name"] for p in notice_plugins]
    records_list = await list_plugins_by_names(plugin_names)
    records = {r["name"]: r for r in records_list}

    # 查询孤儿插件：已安装但磁盘文件已删除的插件
    orphan_records = await list_orphan_plugins(module, plugin_names)

    # 读取各插件当前配置
    configs = await get_plugin_config_map(plugin_names)

    result_list = []
    for p in notice_plugins:
        meta = {}
        schema = []
        try:
            meta = pm.load_metadata(p["name"])
            # 动态加载插件类，获取配置 schema
            mod = importlib.import_module(
                f"plugins.{p['module']}.{p['name']}.plugin"
            )
            plugin_cls = getattr(mod, "Plugin", None)
            if plugin_cls:
                instance = plugin_cls(None, {})
                schema = instance.get_config_schema() or []
        except Exception as e:
            logger.warning("[通知接口] 加载插件配置 schema 失败 %s: %s", p["name"], e)

        record = records.get(p["name"])
        # 从 schema 默认值构建初始配置
        default_config = {}
        for field in schema:
            if "default" in field:
                default_config[field["key"]] = field["default"]

        # 合并: schema 默认值 < 已存配置
        merged_config = {**default_config, **configs.get(p["name"], {})}

        result_list.append({
            "name": p["name"],
            "title": meta.get("title", p["name"]),
            "description": meta.get("description", ""),
            "version": meta.get("version", ""),
            "module": p["module"],
            "config_schema": schema,
            "config": merged_config,
            "status": record["status"] if record else 0,
            "installed": record is not None,
        })

    # 追加孤儿插件（已安装但磁盘文件已删除，仅提供卸载操作）
    for orphan in orphan_records:
        result_list.append({
            "name": orphan["name"],
            "title": orphan["title"] or orphan["name"],
            "description": "插件文件已丢失，建议卸载清理",
            "version": orphan["version"] or "",
            "module": orphan["module"] or "",
            "config_schema": [],
            "config": {},
            "status": orphan["status"],
            "installed": True,
            "orphan": True,
        })

    return ok({"list": result_list})


@router.put("/{plugin_name}/config", dependencies=[Depends(require_permission("notice:config"))])
async def save_plugin_config(plugin_name: str, data: PluginConfigUpsert, request: Request):
    """
    保存通知插件配置

    逐键写入 hy_configuration，key 格式为 {plugin_name}.{key}
    """
    pm = request.app.state.plugin_manager
    router_mgr = request.app.state.router_manager
    discovered = pm.discover()
    # 校验插件存在且属于 sms/mail 模块
    match = [p for p in discovered if p["name"] == plugin_name and p["module"] in ("sms", "mail")]
    if not match:
        raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 不存在或非通知插件")

    await save_plugin_config_and_maybe_enable(plugin_name, data.config, pm, router_mgr)

    logger.info("[通知接口] 插件 %s 配置已保存", plugin_name)
    return ok(msg="配置已保存")


@router.post("/{plugin_name}/test", dependencies=[Depends(require_permission("notice:config"))])
async def test_plugin_connection(plugin_name: str, request: Request,
                                 _: None = Depends(check_admin)):
    """
    测试通知插件连接（调用插件的 test_connection 方法）

    使用当前已保存的配置进行测试
    """
    pm = request.app.state.plugin_manager
    discovered = pm.discover()
    match = [p for p in discovered if p["name"] == plugin_name and p["module"] in ("sms", "mail")]
    if not match:
        raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 不存在或非通知插件")

    try:
        module = importlib.import_module(
            f"plugins.{match[0]['module']}.{plugin_name}.plugin"
        )
        plugin_cls = getattr(module, "Plugin", None)
        if not plugin_cls:
            raise RuntimeError(f"插件 {plugin_name} 缺少 Plugin 类")

        # 读取当前配置
        config = await get_single_plugin_config(plugin_name)

        instance = plugin_cls(None, config)
        result = await instance.test_connection(config)
        return ok(result)
    except ImportError:
        raise HTTPException(
            status_code=400,
            detail=f"插件 {plugin_name} 未安装或不可用"
        )
    except Exception as e:
        logger.error("[通知接口] 插件 %s 测试连接失败: %s", plugin_name, e, exc_info=True)
        return ok({"success": False, "message": str(e)})


@router.put("/{plugin_name}/status", dependencies=[Depends(require_permission("notice:config"))])
async def toggle_plugin_status(plugin_name: str, request: Request):
    """
    启用/禁用通知插件（更新 hy_plugin 表 status）

    请求体: {"status": 1}  或 {"status": 2}
    """
    import json as _json
    body = await request.body()
    try:
        payload = _json.loads(body) if body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="请求体格式错误")
    new_status = payload.get("status")
    if new_status not in (0, 1, 2):
        raise HTTPException(status_code=400, detail="status 取值必须为 0/1/2")

    pm = request.app.state.plugin_manager
    router_mgr = request.app.state.router_manager

    plugin = await get_plugin_by_name(plugin_name)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件未注册，请先在应用列表安装")

    await toggle_plugin_with_pm(plugin_name, new_status, pm, router_mgr)

    status_text = {0: "已安装未启用", 1: "已启用", 2: "已禁用"}.get(new_status, "")
    logger.info("[通知接口] 插件 %s 状态已更新为 %s", plugin_name, status_text)
    return ok(msg=f"插件已{status_text}")


@router.post("/{plugin_name}/sync-templates", dependencies=[Depends(require_permission("notice:config"))])
async def sync_plugin_templates(plugin_name: str, request: Request,
                                _: None = Depends(check_admin)):
    """
    同步远程平台已审核模板

    调用短信插件的 sync_templates 方法拉取远程模板列表，
    写入 hy_sms_template 表（template_id 去重）。
    """
    pm = request.app.state.plugin_manager
    discovered = pm.discover()
    match = [p for p in discovered if p["name"] == plugin_name and p["module"] == "sms"]
    if not match:
        raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 不存在或非短信插件")

    try:
        mod = importlib.import_module(f"plugins.sms.{plugin_name}.plugin")
        plugin_cls = getattr(mod, "Plugin", None)
        if not plugin_cls:
            raise RuntimeError(f"插件 {plugin_name} 缺少 Plugin 类")

        # 读取当前配置
        config = await get_single_plugin_config(plugin_name)

        instance = plugin_cls(None, config)
        # 检查插件是否支持 sync_templates 方法
        if not hasattr(instance, "sync_templates"):
            return ok({"success": False, "message": "该插件不支持模板同步"})

        result = await instance.sync_templates(config)
        if not result.get("success"):
            return ok(result)

        # 将拉取到的模板写入 hy_sms_template 表（按插件接口和 template_id 去重）
        templates = result.get("templates", [])
        if not templates:
            return ok({"success": True, "message": "平台无已审核模板", "synced": 0})

        synced_count = await sync_sms_templates_from_remote(templates, plugin_name)
        return ok({"success": True, "message": "同步完成", "synced": synced_count})
    except ImportError:
        raise HTTPException(status_code=400, detail=f"插件 {plugin_name} 未安装或不可用")
    except Exception as e:
        logger.error("[通知接口] 插件 %s 模板同步失败: %s", plugin_name, e, exc_info=True)
        return ok({"success": False, "message": str(e)})
