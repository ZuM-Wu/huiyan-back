# -*- coding: utf-8 -*-
"""
天气服务管理 API（管理员端）

功能分区:
- 数据源插件: 列表(discover+状态+schema+已存配置)/保存配置/测试连通/启停
- 全局设置:   总开关/默认源/拉取频率/积温基点（保存后免重启应用调度）
- 产区绑定:   指定某产区走和风或高德（无绑定回落全局默认源）
- 数据查询:   产区快照/逐日历史/批次积温/强制刷新/手动全量拉取

权限: weather:view 查询类 / weather:config 配置类
"""
import logging
import importlib
from datetime import date
from core.time_utils import china_now

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, update, desc

from core.config_manager import ConfigManager
from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.log.active_log import active_log
from core.config_service import get_config as svc_get_config, set_config as svc_set_config
from core.response import ok
from core.weather_service import weather_service, MIN_INTERVAL_MINUTES, RETENTION_RULES
from core import weather_gdd

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/weather", tags=["天气服务管理"])


class PluginConfigUpsert(BaseModel):
    """插件配置保存请求"""
    config: dict


class PluginStatusUpsert(BaseModel):
    """插件启停请求"""
    status: int


class SettingsUpsert(BaseModel):
    """全局设置保存请求"""
    enabled: bool = False
    source: str = ""
    interval_minutes: int = 15
    gdd_base_temp: float = 10.0
    daily_retention_days: int = 730
    alert_retention_days: int = 90


class BindingUpsert(BaseModel):
    """产区数据源绑定请求（source 为空表示解除绑定回落默认源）"""
    area_id: int
    source: str = ""


# ----------------------------------------------------------------------
# 数据源插件管理
# ----------------------------------------------------------------------
@router.get("/sources", dependencies=[Depends(require_permission("weather:config"))])
async def list_weather_sources(request: Request, _: None = Depends(check_admin)):
    """列出天气数据源插件（discover + 安装状态 + config_schema + 已存配置）"""
    from core.db.base import async_session_factory
    from core.db.plugin import PluginModel
    pm = request.app.state.plugin_manager
    plugins = [p for p in pm.discover() if p["module"] == "weather"]
    plugin_names = [p["name"] for p in plugins]

    async with async_session_factory() as db:
        records = {r.name: r for r in (await db.execute(
            select(PluginModel).where(PluginModel.name.in_(plugin_names))
        )).scalars().all()}
        cm = ConfigManager()
        configs = {}
        for p in plugins:
            configs[p["name"]] = await cm.get_plugin_config(p["name"], db)

    result_list = []
    for p in plugins:
        meta, schema = {}, []
        try:
            meta = pm.load_metadata(p["name"])
            mod = importlib.import_module(f"plugins.weather.{p['name']}.plugin")
            plugin_cls = getattr(mod, "Plugin", None)
            if plugin_cls:
                schema = plugin_cls(None, {}).get_config_schema() or []
        except Exception as e:
            logger.warning("[天气插件] 加载插件配置 schema 失败 %s: %s", p["name"], e)
        record = records.get(p["name"])
        # 合并: schema 默认值 < 已存配置
        default_config = {f["key"]: f["default"] for f in schema if "default" in f}
        result_list.append({
            "name": p["name"],
            "title": meta.get("title", p["name"]),
            "description": meta.get("description", ""),
            "version": meta.get("version", ""),
            "config_schema": schema,
            "config": {**default_config, **configs.get(p["name"], {})},
            "status": record.status if record else 0,
            "installed": record is not None,
        })
    return ok({"list": result_list})


@router.post("/sources/{plugin_name}/config",
             dependencies=[Depends(require_permission("weather:config"))])
async def save_source_config(plugin_name: str, data: PluginConfigUpsert,
                             request: Request, _: None = Depends(check_admin)):
    """保存数据源插件配置（逐键写入 hy_configuration，key 为 {插件名}.{键}）"""
    from core.db.base import async_session_factory
    from core.db.plugin import PluginModel
    pm = request.app.state.plugin_manager
    match = [p for p in pm.discover()
             if p["name"] == plugin_name and p["module"] == "weather"]
    if not match:
        raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 不存在或非天气插件")

    for key, value in data.config.items():
        await svc_set_config(f"{plugin_name}.{key}", str(value))
    # 已注册且处于禁用状态的插件，保存配置后经统一生命周期入口自动启用
    # （钩子恢复 + 路由门禁放行，禁止直改 status 造成 DB 与运行时漂移）
    async with async_session_factory() as db:
        record = (await db.execute(
            select(PluginModel).where(PluginModel.name == plugin_name)
        )).scalar_one_or_none()
        if record and record.status == 2:
            await pm.enable(plugin_name, db,
                            router_manager=request.app.state.router_manager)
    logger.info("[天气管理] 插件 %s 配置已保存", plugin_name)
    await active_log(f"保存天气数据源[{plugin_name}]配置",
                     log_type="weather", request=request)
    return ok(msg="配置已保存")


@router.post("/sources/{plugin_name}/test",
             dependencies=[Depends(require_permission("weather:config"))])
async def test_source_connection(plugin_name: str, request: Request,
                                 _: None = Depends(check_admin)):
    """测试数据源插件连通性（用当前已保存配置真实探活）"""
    from core.db.base import async_session_factory
    pm = request.app.state.plugin_manager
    match = [p for p in pm.discover()
             if p["name"] == plugin_name and p["module"] == "weather"]
    if not match:
        raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 不存在或非天气插件")
    try:
        mod = importlib.import_module(f"plugins.weather.{plugin_name}.plugin")
        plugin_cls = getattr(mod, "Plugin", None)
        if not plugin_cls:
            raise RuntimeError(f"插件 {plugin_name} 缺少 Plugin 类")
        async with async_session_factory() as db:
            config = await ConfigManager().get_plugin_config(plugin_name, db)
            # 高德 key 回落逻辑与拉取链路保持一致
            if plugin_name == "weather_amap" and not config.get("key"):
                fallback = await svc_get_config("amap_web_service_key")
                if fallback:
                    config["_fallback_key"] = fallback
        result = await plugin_cls(None, config).test_connection(config)
        return ok(result)
    except Exception as e:
        logger.error("[天气管理] 插件 %s 测试失败: %s", plugin_name, e, exc_info=True)
        return ok({"success": False, "message": str(e)})


@router.put("/sources/{plugin_name}/status",
            dependencies=[Depends(require_permission("weather:config"))])
async def toggle_source_status(plugin_name: str, data: PluginStatusUpsert,
                               request: Request, _: None = Depends(check_admin)):
    """启用/禁用数据源插件（统一生命周期入口: 1=启用, 2=禁用, 0=重置为已安装未启用）"""
    from core.db.base import async_session_factory
    from core.db.plugin import PluginModel
    if data.status not in (0, 1, 2):
        raise HTTPException(status_code=400, detail="status 取值必须为 0/1/2")
    pm = request.app.state.plugin_manager
    router_mgr = request.app.state.router_manager
    async with async_session_factory() as db:
        record = (await db.execute(
            select(PluginModel).where(PluginModel.name == plugin_name)
        )).scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="插件未注册，请先在应用列表安装")
        # 启停经统一生命周期入口，同步完成钩子与路由门禁处理，避免状态漂移
        if data.status == 1:
            await pm.enable(plugin_name, db, router_manager=router_mgr)
        elif data.status == 2:
            await pm.disable(plugin_name, db, router_manager=router_mgr)
        else:
            # 重置为 0：先经 disable 断开运行时（钩子/路由），再改写为目标状态
            await pm.disable(plugin_name, db, router_manager=router_mgr)
            await db.execute(
                update(PluginModel).where(PluginModel.name == plugin_name).values(status=0)
            )
            await db.commit()
    status_text = {0: "已安装未启用", 1: "已启用", 2: "已禁用"}.get(data.status, "")
    action = {0: "重置", 1: "启用", 2: "停用"}.get(data.status, "变更")
    await active_log(f"{action}天气数据源[{plugin_name}]",
                     log_type="weather", request=request)
    return ok(msg=f"插件{status_text}")


# ----------------------------------------------------------------------
# 全局设置
# ----------------------------------------------------------------------
@router.get("/settings", dependencies=[Depends(require_permission("weather:view"))])
async def get_weather_settings(_: None = Depends(check_admin)):
    """读取天气全局设置"""
    from core.db.base import async_session_factory
    async with async_session_factory() as db:
        settings = await weather_service.get_settings(db)
    return ok(settings)


@router.post("/settings", dependencies=[Depends(require_permission("weather:config"))])
async def save_weather_settings(data: SettingsUpsert, request: Request,
                                _: None = Depends(check_admin)):
    """保存全局设置并即时应用调度（免重启生效）"""
    interval = max(int(data.interval_minutes), MIN_INTERVAL_MINUTES)
    # 保留期按 RETENTION_RULES 下限钳位（daily ≥365 防误配丢积温数据）
    retention = {
        field: max(int(getattr(data, field)), floor)
        for field, (_key, _default, floor) in RETENTION_RULES.items()
    }
    await svc_set_config("weather_enabled", "1" if data.enabled else "0")
    await svc_set_config("weather_source", data.source or "")
    await svc_set_config("weather_interval_minutes", str(interval))
    await svc_set_config("weather_gdd_base_temp", str(data.gdd_base_temp))
    await svc_set_config("weather_daily_retention_days",
                         str(retention["daily_retention_days"]))
    await svc_set_config("weather_alert_retention_days",
                         str(retention["alert_retention_days"]))

    # 保存即 reschedule；重启时以 hy_configuration 为唯一事实源重建任务
    from services.task.weather_worker import apply_weather_schedule
    await apply_weather_schedule()
    logger.info("[天气管理] 全局设置已保存并应用调度")
    await active_log(
        f"保存天气全局设置: 开关={'on' if data.enabled else 'off'}, "
        f"默认源={data.source or '无'}, 频率={interval}分钟, "
        f"数据保留={retention['daily_retention_days']}"
        f"/{retention['alert_retention_days']}天",
        log_type="weather", request=request,
    )
    return ok(msg="设置已保存并生效")


# ----------------------------------------------------------------------
# 产区数据源绑定
# ----------------------------------------------------------------------
@router.get("/bindings", dependencies=[Depends(require_permission("weather:view"))])
async def list_bindings(_: None = Depends(check_admin)):
    """产区列表 + 绑定源 + 最近拉取状态（管理页产区表格一次取全）"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea
    from core.db.weather import WeatherData, WeatherAreaBinding
    async with async_session_factory() as db:
        areas = (await db.execute(
            select(ProductionArea).where(ProductionArea.status == 1)
            .order_by(ProductionArea.sort_order, ProductionArea.id)
        )).scalars().all()
        bindings = {b.area_id: b.source for b in (await db.execute(
            select(WeatherAreaBinding)
        )).scalars().all()}
        snapshots = {s.area_id: s for s in (await db.execute(
            select(WeatherData)
        )).scalars().all()}

    result_list = []
    for a in areas:
        snap = snapshots.get(a.id)
        result_list.append({
            "area_id": a.id,
            "area_name": a.name,
            "district": f"{a.province}{a.city}{a.district}",
            "has_location": bool(a.longitude and a.latitude),
            "bound_source": bindings.get(a.id, ""),
            "last_source": snap.source if snap else "",
            "fetch_time": str(snap.fetch_time) if snap and snap.fetch_time else "",
            "error_msg": snap.error_msg if snap else "",
        })
    return ok({"list": result_list})


@router.post("/bindings", dependencies=[Depends(require_permission("weather:config"))])
async def save_binding(data: BindingUpsert, request: Request,
                       _: None = Depends(check_admin)):
    """设置/解除产区数据源绑定（source 为空即解除，回落全局默认源）"""
    from core.db.base import async_session_factory
    from core.db.weather import WeatherAreaBinding
    async with async_session_factory() as db:
        binding = (await db.execute(
            select(WeatherAreaBinding).where(WeatherAreaBinding.area_id == data.area_id)
        )).scalar_one_or_none()
        if not data.source:
            if binding:
                await db.delete(binding)
        elif binding:
            binding.source = data.source
        else:
            db.add(WeatherAreaBinding(area_id=data.area_id, source=data.source))
        await db.commit()
    description = (f"产区[{data.area_id}]绑定天气源[{data.source}]" if data.source
                   else f"解除产区[{data.area_id}]天气源绑定")
    await active_log(description, log_type="weather",
                     rel_id=data.area_id, request=request)
    return ok(msg="绑定已更新")


# ----------------------------------------------------------------------
# 数据查询与手动操作
# ----------------------------------------------------------------------
@router.get("/area/{area_id}", dependencies=[Depends(require_permission("weather:view"))])
async def get_area_weather(area_id: int, _: None = Depends(check_admin)):
    """查产区天气快照（实况/逐时/预报）+ 生效预警"""
    from core.db.base import async_session_factory
    from core.db.weather import WeatherAlert
    data = await weather_service.get_weather(area_id)
    async with async_session_factory() as db:
        alerts = (await db.execute(
            select(WeatherAlert).where(
                WeatherAlert.area_id == area_id,
                WeatherAlert.end_time >= china_now(),
            ).order_by(desc(WeatherAlert.start_time)).limit(20)
        )).scalars().all()
    data["alerts"] = [
        {
            "alert_id": a.alert_id, "type": a.alert_type, "level": a.level,
            "title": a.title, "text": a.text,
            "start_time": str(a.start_time) if a.start_time else "",
            "end_time": str(a.end_time) if a.end_time else "",
        }
        for a in alerts
    ]
    return ok(data)


@router.get("/area/{area_id}/daily", dependencies=[Depends(require_permission("weather:view"))])
async def get_area_daily(
    area_id: int,
    start: date = Query(None, description="起始日期 YYYY-MM-DD"),
    end: date = Query(None, description="结束日期 YYYY-MM-DD"),
    _: None = Depends(check_admin),
):
    """逐日天气历史（图表数据，支持日期范围）"""
    rows = await weather_gdd.list_daily(area_id, start, end)
    return ok({"list": rows})


@router.get("/area/{area_id}/gdd", dependencies=[Depends(require_permission("weather:view"))])
async def get_area_gdd(
    area_id: int,
    batch_id: int = Query(..., description="种植批次ID"),
    _: None = Depends(check_admin),
):
    """批次积温统计（活动/有效积温，自定植日起算）"""
    result = await weather_gdd.get_accumulated_temp(area_id, batch_id)
    return ok(result)


@router.post("/area/{area_id}/refresh",
             dependencies=[Depends(require_permission("weather:config"))])
async def refresh_area_weather(area_id: int, _: None = Depends(check_admin)):
    """强制刷新单产区天气（绕过缓存实时拉取）"""
    data = await weather_service.get_weather(area_id, force=True)
    if data.get("status") == "error" or data.get("error_msg"):
        return ok(data, msg=f"刷新失败: {data.get('error_msg') or '未知错误'}")
    return ok(data, msg="刷新成功")


@router.post("/pull", dependencies=[Depends(require_permission("weather:config"))])
async def pull_all_weather(request: Request, _: None = Depends(check_admin)):
    """手动触发全量拉取（遍历启用产区，坐标网格去重省配额）"""
    stats = await weather_service.pull_all()
    await active_log(
        f"手动触发天气全量拉取: 成功{stats['success']}/失败{stats['failed']}"
        f"/跳过{stats['skipped']}",
        log_type="weather", request=request,
    )
    return ok(stats, msg=f"拉取完成: 成功{stats['success']} 失败{stats['failed']} 跳过{stats['skipped']}")
