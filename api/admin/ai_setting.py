# -*- coding: utf-8 -*-
"""
AI 设置 API（管理员端）

三大板块（对标通知模块设置页）:
- Tab1 接口列表: llm 驱动插件的查看/配置/测试/启停（照抄 notice_interface 范式）
- Tab2 对话参数: 全局 ai.* 参数读写、技能预设 CRUD、模型列表透传
- Tab3 MCP 能力: 系统 MCP 工具列表预览、外部 MCP 服务器 CRUD + 连通测试
"""
import importlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update

from core.config_manager import ConfigManager
from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.config_service import get_config as svc_get_config, set_config as svc_set_config
from core.response import ok
from services.ai.service import AI_SETTING_DEFAULTS, get_ai_settings
from services.ai.driver import get_llm_readiness, resolve_llm_plugin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/ai/setting", tags=["AI设置"])

_PERM = Depends(require_permission("ai:setting"))


# ==================================================================
# Tab1 接口列表（llm 驱动插件管理，照抄 notice_interface 范式）
# ==================================================================

class PluginConfigUpsert(BaseModel):
    """插件配置保存请求"""
    config: dict


def _find_llm_plugin(pm, plugin_name: str) -> dict:
    """在磁盘发现结果中定位 LLM 驱动插件。"""
    match = [p for p in pm.discover()
             if p["name"] == plugin_name and p["module"] == "llm"]
    if not match:
        raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 不存在或非模型驱动插件")
    return match[0]


@router.get("/interfaces", dependencies=[_PERM])
async def list_llm_plugins(request: Request, _: None = Depends(check_admin)):
    """列出 LLM 驱动和视觉 MCP 接口及其配置与状态。"""
    from core.db.base import async_session_factory
    from core.db.plugin import PluginModel
    pm = request.app.state.plugin_manager
    llm_plugins = [p for p in pm.discover() if p["module"] == "llm"]
    plugin_names = [p["name"] for p in llm_plugins]

    async with async_session_factory() as db:
        result = await db.execute(
            select(PluginModel).where(PluginModel.name.in_(plugin_names))
        )
        records = {r.name: r for r in result.scalars().all()}
        cm = ConfigManager()
        configs = {}
        for p in llm_plugins:
            configs[p["name"]] = await cm.get_plugin_config(p["name"], db)
        active_interface = await cm.get("ai.active_interface", db) or ""

    result_list = []
    for p in llm_plugins:
        meta = {}
        schema = []
        try:
            meta = pm.load_metadata(p["name"])
            module = importlib.import_module(
                f"plugins.{p['module']}.{p['name']}.plugin")
            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls:
                schema = plugin_cls(None, {}).get_config_schema() or []
        except Exception as e:
            logger.warning("[AI设置] 加载插件配置 schema 失败 %s: %s", p["name"], e)

        record = records.get(p["name"])
        installed_version = record.version if record else ""
        disk_version = meta.get("version", "")
        upgrade_available = False
        if record and installed_version and disk_version != installed_version:
            try:
                upgrade_available = pm.compare_versions(
                    disk_version, installed_version) > 0
            except ValueError:
                logger.warning("[AI设置] 插件 %s 版本号无效，跳过升级提示", p["name"])
        result_list.append({
            "name": p["name"],
            "title": meta.get("title", p["name"]),
            "description": meta.get("description", ""),
            "version": disk_version,
            "installed_version": installed_version,
            "upgrade_available": upgrade_available,
            "module": p["module"],
            "config_schema": schema,
            "config": configs.get(p["name"], {}),
            "status": record.status if record else 0,
            "installed": record is not None,
            "active": p["name"] == active_interface,
        })
    return ok({"list": result_list, "active_interface": active_interface})


@router.put("/interfaces/{plugin_name}/config", dependencies=[_PERM])
async def save_plugin_config(plugin_name: str, data: PluginConfigUpsert,
                             request: Request, _: None = Depends(check_admin)):
    """保存 llm 插件配置（逐键写入 hy_configuration，key 格式 {插件名}.{键}）"""
    from core.db.base import async_session_factory
    from core.db.plugin import PluginModel
    pm = request.app.state.plugin_manager
    router_mgr = request.app.state.router_manager
    _find_llm_plugin(pm, plugin_name)

    for key, value in data.config.items():
        await svc_set_config(f"{plugin_name}.{key}", str(value))
    # 已注册且被禁用的插件，保存配置后自动启用（与通知接口行为一致）
    async with async_session_factory() as db:
        record = (await db.execute(
            select(PluginModel).where(PluginModel.name == plugin_name)
        )).scalar_one_or_none()
        if record and record.status == 2:
            await pm.enable(plugin_name, db, router_manager=router_mgr)

    logger.info("[AI设置] 插件 %s 配置已保存", plugin_name)
    return ok(msg="配置已保存")


@router.post("/interfaces/{plugin_name}/test", dependencies=[_PERM])
async def test_plugin_connection(plugin_name: str, request: Request,
                                 _: None = Depends(check_admin)):
    """测试 llm 插件连通性（调用插件 test_connection，使用当前已保存配置）"""
    from core.db.base import async_session_factory
    pm = request.app.state.plugin_manager
    _find_llm_plugin(pm, plugin_name)
    try:
        plugin_meta = _find_llm_plugin(pm, plugin_name)
        module = importlib.import_module(
            f"plugins.{plugin_meta['module']}.{plugin_name}.plugin")
        plugin_cls = getattr(module, "Plugin", None)
        if not plugin_cls:
            raise RuntimeError(f"插件 {plugin_name} 缺少 Plugin 类")
        async with async_session_factory() as db:
            config = await ConfigManager().get_plugin_config(plugin_name, db)
        result = await plugin_cls(None, config).test_connection(config)
        return ok(result)
    except Exception as e:
        logger.error("[AI设置] 插件 %s 测试连接失败: %s", plugin_name, e, exc_info=True)
        return ok({"success": False, "message": str(e)})


@router.put("/interfaces/{plugin_name}/status", dependencies=[_PERM])
async def toggle_plugin_status(plugin_name: str, request: Request,
                               _: None = Depends(check_admin)):
    """启用/禁用 llm 插件（请求体 {"status": 0/1/2}）"""
    from core.db.base import async_session_factory
    from core.db.plugin import PluginModel
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="请求体格式错误")
    new_status = payload.get("status")
    if new_status not in (0, 1, 2):
        raise HTTPException(status_code=400, detail="status 取值必须为 0/1/2")

    pm = request.app.state.plugin_manager
    router_mgr = request.app.state.router_manager
    async with async_session_factory() as db:
        record = (await db.execute(
            select(PluginModel).where(PluginModel.name == plugin_name)
        )).scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="插件未注册，请先在应用列表安装")
        if new_status == 1:
            await pm.enable(plugin_name, db, router_manager=router_mgr)
        elif new_status == 2:
            await pm.disable(plugin_name, db, router_manager=router_mgr)
        else:
            await db.execute(
                update(PluginModel).where(PluginModel.name == plugin_name).values(status=0)
            )
            await db.commit()

    status_text = {0: "已安装未启用", 1: "已启用", 2: "已禁用"}.get(new_status, "")
    return ok(msg=f"插件已{status_text}")


# ==================================================================
# Tab2 对话参数 + 模型列表
# ==================================================================

@router.get("/params", dependencies=[_PERM])
async def get_params(_: None = Depends(check_admin)):
    """读取全局对话参数（含默认值回落）"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        settings = await get_ai_settings(db)
    return ok(settings)


@router.put("/params", dependencies=[_PERM])
async def save_params(request: Request, _: None = Depends(check_admin)):
    """保存全局对话参数（仅接受 AI_SETTING_DEFAULTS 白名单内的键）"""
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="请求体格式错误")

    # 智谱首次激活且系统从未保存过主模型时采用插件默认模型；已有配置必须保留，
    # 避免切换接口时意外改变管理员已经选择的模型。
    if payload.get("active_interface") == "vision_glm" and "default_model" not in payload:
        from core.db.base import async_session_factory
        async with async_session_factory() as db:
            current_default = await ConfigManager().get("ai.default_model", db)
        if current_default is None:
            payload["default_model"] = "glm-5.2"

    for full_key, (_default, desc) in AI_SETTING_DEFAULTS.items():
        short = full_key[len("ai."):]
        if short not in payload:
            continue
        value = payload[short]
        # 布尔开关统一存 "0"/"1"
        if isinstance(value, bool):
            value = "1" if value else "0"
        await svc_set_config(full_key, str(value))
    return ok(msg="参数已保存")


@router.get("/models", dependencies=[_PERM])
async def list_models(interface: str = "", _: None = Depends(check_admin)):
    """透传驱动插件的模型列表（interface 为空时用当前激活/回落接口）"""
    if not interface:
        interface = await svc_get_config("ai.active_interface") or ""
    plugin, config, err = await resolve_llm_plugin(interface, require_configured=True)
    if not plugin:
        reason_code = "config_missing" if "配置" in err else "driver_unavailable"
        return ok({
            "list": [], "interface": interface, "ready": False,
            "reason_code": reason_code, "message": err,
        })
    readiness = get_llm_readiness(plugin, config)
    try:
        models = await plugin.llm_list_models()
    except Exception as exc:
        logger.warning("[AI设置] 读取模型列表失败: %s", exc)
        return ok({
            "list": [], "interface": getattr(plugin, "name", interface),
            "ready": False, "reason_code": "model_list_error",
            "message": "模型列表读取失败",
        })
    if not readiness["ready"] or not models:
        return ok({
            "list": [], "interface": getattr(plugin, "name", interface),
            "ready": False,
            "reason_code": readiness["reason_code"] if not readiness["ready"] else "no_models",
            "message": readiness["message"] if not readiness["ready"] else "当前模型接口没有可用模型",
        })
    return ok({
        "list": models, "interface": getattr(plugin, "name", interface),
        "ready": True, "reason_code": "ready", "message": "模型列表已就绪",
    })


# ==================================================================
# Tab2 技能预设 CRUD
# ==================================================================

class SkillUpsert(BaseModel):
    """技能预设创建/更新请求"""
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: list = []
    audience: str = "admin"
    status: int = 1


@router.get("/skills", dependencies=[_PERM])
async def list_all_skills(_: None = Depends(check_admin)):
    """技能预设全量列表（管理视图，含停用项）"""
    from core.db.base import async_session_factory
    from core.db.ai import AiSkillModel
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(AiSkillModel).order_by(AiSkillModel.id.asc())
        )).scalars().all()
    return ok({"list": [{
        "id": s.id, "name": s.name, "description": s.description,
        "system_prompt": s.system_prompt,
        "tools": json.loads(s.tools) if s.tools else [],
        "audience": s.audience, "status": s.status,
    } for s in rows]})


@router.post("/skills", dependencies=[_PERM])
async def create_skill(data: SkillUpsert, _: None = Depends(check_admin)):
    """创建技能预设"""
    from core.db.base import async_session_factory
    from core.db.ai import AiSkillModel
    if data.audience not in ("admin", "farmer", "both"):
        raise HTTPException(status_code=400, detail="audience 取值必须为 admin/farmer/both")
    async with async_session_factory() as db:
        skill = AiSkillModel(
            name=data.name, description=data.description,
            system_prompt=data.system_prompt,
            tools=json.dumps(data.tools, ensure_ascii=False) if data.tools else None,
            audience=data.audience, status=data.status,
        )
        db.add(skill)
        await db.commit()
        return ok({"id": skill.id}, msg="技能已创建")


@router.put("/skills/{skill_id}", dependencies=[_PERM])
async def update_skill(skill_id: int, data: SkillUpsert, _: None = Depends(check_admin)):
    """更新技能预设"""
    from core.db.base import async_session_factory
    from core.db.ai import AiSkillModel
    if data.audience not in ("admin", "farmer", "both"):
        raise HTTPException(status_code=400, detail="audience 取值必须为 admin/farmer/both")
    async with async_session_factory() as db:
        skill = (await db.execute(
            select(AiSkillModel).where(AiSkillModel.id == skill_id)
        )).scalar_one_or_none()
        if not skill:
            raise HTTPException(status_code=404, detail="技能不存在")
        skill.name = data.name
        skill.description = data.description
        skill.system_prompt = data.system_prompt
        skill.tools = json.dumps(data.tools, ensure_ascii=False) if data.tools else None
        skill.audience = data.audience
        skill.status = data.status
        await db.commit()
    return ok(msg="技能已更新")


@router.delete("/skills/{skill_id}", dependencies=[_PERM])
async def delete_skill(skill_id: int, _: None = Depends(check_admin)):
    """删除技能预设（物理删除；引用该技能的会话回落为无技能）"""
    from core.db.base import async_session_factory
    from core.db.ai import AiSkillModel
    async with async_session_factory() as db:
        skill = (await db.execute(
            select(AiSkillModel).where(AiSkillModel.id == skill_id)
        )).scalar_one_or_none()
        if not skill:
            raise HTTPException(status_code=404, detail="技能不存在")
        await db.delete(skill)
        await db.commit()
    return ok(msg="技能已删除")
