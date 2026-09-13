# -*- coding: utf-8 -*-
"""
天气核心服务（单例，对标 core/notice_sender.py 的"核心+渠道插件"模式）

职责:
- 数据源解析: 产区绑定源(hy_weather_area_binding) -> 全局默认源(weather_source)
- 插件加载: 照抄 notice_worker.resolve_plugin 的 DB 查询 + importlib 动态加载模式
- 拉取落库: 快照 upsert + 逐日历史滚动极值 + 官方预警去重入库 + 写缓存
- 读取服务: 缓存 -> 快照表 -> force/miss 实时拉取（用户请求永不直穿第三方 API）
- 农业指标: 活动/有效积温计算（按种植批次定植日期起算）

缓存说明: cache_manager 为纯文件缓存，已支持 expire 过期信封；
本服务缓存 value 另内嵌 fetched_at 时间戳由服务层按拉取频率精确判过期（双保险）。
"""
import importlib
import json
import logging
from datetime import datetime, date, timedelta, timezone
from core.time_utils import china_now

from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.plugin import PluginModel
from core.db.production_area import ProductionArea
from core.db.weather import WeatherData, WeatherDaily, WeatherAreaBinding, WeatherAlert
from core.config_manager import ConfigManager
from core.cache.cache_manager import cache_manager

logger = logging.getLogger(__name__)

# 拉取频率下限（分钟），保护第三方 API 配额
MIN_INTERVAL_MINUTES = 10
# 积温基点温度默认值（℃）
DEFAULT_GDD_BASE_TEMP = 10.0
# 数据保留期（天）: (配置键, 默认值, 下限)；daily 下限 365 防误配丢积温数据
RETENTION_RULES = {
    "daily_retention_days": ("weather_daily_retention_days", 730, 365),
    "alert_retention_days": ("weather_alert_retention_days", 90, 30),
}


def _cache_key(area_id: int) -> str:
    """产区天气缓存键"""
    return f"hy:weather:{area_id}"


def _parse_json(raw: str | None, default):
    """安全解析 JSON 字段（快照表存储的 realtime/hourly/forecast）"""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _parse_alert_time(raw: str):
    """解析和风预警时间

    新接口 weatheralert/v1/current 返回 UTC（Z 后缀，如 2026-07-31T06:24Z），
    旧接口 /v7/warning/now 返回 +08:00 带时区；统一转为本地时间(UTC+8)后存 naive。
    Python3.10 fromisoformat 不支持 Z，手动替换为 +00:00 再解析。
    """
    if not raw:
        return None
    s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    return dt


class WeatherService:
    """天气核心服务（全局单例 weather_service）"""

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------
    async def get_settings(self, db) -> dict:
        """读取天气全局设置（开关/默认源/频率/积温基点/数据保留期）"""
        cm = ConfigManager()
        interval = int(await cm.get("weather_interval_minutes", db) or 15)
        settings = {
            "enabled": str(await cm.get("weather_enabled", db) or "0") == "1",
            "source": str(await cm.get("weather_source", db) or ""),
            "interval_minutes": max(interval, MIN_INTERVAL_MINUTES),
            "gdd_base_temp": float(
                await cm.get("weather_gdd_base_temp", db) or DEFAULT_GDD_BASE_TEMP
            ),
        }
        for field, (key, default, floor) in RETENTION_RULES.items():
            try:
                value = int(await cm.get(key, db) or default)
            except (TypeError, ValueError):
                value = default
            settings[field] = max(value, floor)
        return settings

    # ------------------------------------------------------------------
    # 数据源解析与插件加载
    # ------------------------------------------------------------------
    async def resolve_source(self, area_id: int, db) -> str:
        """解析产区数据源: 产区绑定优先，无绑定回落全局默认源（不做自动主备切换）"""
        binding = (await db.execute(
            select(WeatherAreaBinding).where(WeatherAreaBinding.area_id == area_id)
        )).scalar_one_or_none()
        if binding and binding.source:
            return binding.source
        return str(await ConfigManager().get("weather_source", db) or "")

    async def load_plugin(self, source: str, db):
        """
        加载已启用的天气数据源插件实例

        返回: (插件实例, 错误信息)；失败时实例为 None
        """
        if not source:
            return None, "未配置天气数据源（请在天气服务页设置默认源或产区绑定）"

        record = (await db.execute(
            select(PluginModel).where(
                PluginModel.name == source,
                PluginModel.module == "weather",
                PluginModel.status == 1,
            )
        )).scalars().first()
        if not record:
            return None, f"数据源插件未安装或未启用：{source}"

        config = await ConfigManager().get_plugin_config(record.name, db)
        # 高德插件 key 留空时回落复用产区管理已配置的 Web 服务 Key
        if record.name == "weather_amap" and not config.get("key"):
            fallback = await ConfigManager().get("amap_web_service_key", db)
            if fallback:
                config["_fallback_key"] = fallback

        try:
            plugin_module = importlib.import_module(f"plugins.weather.{record.name}.plugin")
            plugin_cls = getattr(plugin_module, "Plugin", None)
            if not plugin_cls:
                return None, f"插件 {record.name} 缺少 Plugin 类"
            return plugin_cls(None, config), ""
        except Exception as e:
            logger.error(f"[天气服务] 插件加载失败: {record.name}, {e}")
            return None, f"插件加载失败：{e}"

    # ------------------------------------------------------------------
    # 读取服务（管理端/农户端 API 统一入口）
    # ------------------------------------------------------------------
    async def get_weather(self, area_id: int, force: bool = False) -> dict:
        """
        查产区天气: 缓存 -> 快照表 -> 仍无或 force 时实时拉取

        返回: {status, source, granularity, realtime, hourly, forecast,
               fetch_time, error_msg}；无数据时 status="empty"
        """
        async with async_session_factory() as db:
            settings = await self.get_settings(db)

        # 1. 缓存优先（内嵌 fetched_at 判过期，兼容文件缓存无 TTL）
        if not force:
            cached = await cache_manager.get(_cache_key(area_id))
            if cached and cached.get("fetched_at"):
                age = (china_now()
                       - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
                if age < settings["interval_minutes"] * 60:
                    return cached["data"]

        async with async_session_factory() as db:
            # 2. 快照表兜底（无论多旧都返回，前端按 fetch_time 提示时效）
            if not force:
                snapshot = (await db.execute(
                    select(WeatherData).where(WeatherData.area_id == area_id)
                )).scalar_one_or_none()
                if snapshot and snapshot.fetch_time:
                    return self._snapshot_to_dict(snapshot)

            # 3. 实时拉取（首次查询或管理员强制刷新）
            area = (await db.execute(
                select(ProductionArea).where(ProductionArea.id == area_id)
            )).scalar_one_or_none()
            if not area:
                return {"status": "empty", "error_msg": "产区不存在"}
            result = await self.fetch_and_store(area, db)
            await db.commit()
            return result

    @staticmethod
    def _snapshot_to_dict(snapshot: WeatherData) -> dict:
        """快照 ORM 行转 API 响应结构"""
        return {
            "status": "success" if snapshot.fetch_time else "empty",
            "source": snapshot.source,
            "granularity": "district" if snapshot.source == "weather_amap" else "point",
            "realtime": _parse_json(snapshot.realtime, {}),
            "hourly": _parse_json(snapshot.hourly, []),
            "forecast": _parse_json(snapshot.forecast, []),
            "fetch_time": str(snapshot.fetch_time) if snapshot.fetch_time else "",
            "error_msg": snapshot.error_msg or "",
        }

    # ------------------------------------------------------------------
    # 拉取与落库
    # ------------------------------------------------------------------
    async def fetch_and_store(self, area, db, dto: dict | None = None) -> dict:
        """
        拉取单产区天气并落库（快照 + 逐日历史 + 预警 + 缓存）

        参数:
            area: ProductionArea 实例
            db:   外部会话（调用方负责 commit）
            dto:  可选，坐标网格去重时复用同格产区已拉取的 DTO
        返回: API 响应结构（同 _snapshot_to_dict）
        """
        snapshot = (await db.execute(
            select(WeatherData).where(WeatherData.area_id == area.id)
        )).scalar_one_or_none()

        if dto is None:
            source = await self.resolve_source(area.id, db)
            plugin, err = await self.load_plugin(source, db)
            if not plugin:
                return await self._store_error(db, snapshot, area.id, err)
            loc = {
                "longitude": area.longitude, "latitude": area.latitude,
                "adcode": snapshot.adcode if snapshot else "",
                "province": area.province, "city": area.city,
                "district": area.district,
            }
            try:
                dto = await plugin.fetch_weather(loc)
            except Exception as e:
                logger.error(f"[天气服务] 产区 {area.id} 拉取异常: {e}")
                dto = {"status": "error", "source": source, "msg": f"拉取异常: {e}"}

        if dto.get("status") != "success":
            return await self._store_error(db, snapshot, area.id,
                                           dto.get("msg", "未知错误"))

        # ---- 快照 upsert（每产区一行） ----
        now = china_now()
        if not snapshot:
            snapshot = WeatherData(area_id=area.id)
            db.add(snapshot)
        snapshot.source = dto.get("source", "")
        if dto.get("adcode"):
            snapshot.adcode = dto["adcode"]
        snapshot.realtime = json.dumps(dto.get("realtime", {}), ensure_ascii=False)
        snapshot.hourly = json.dumps(dto.get("hourly", []), ensure_ascii=False)
        snapshot.forecast = json.dumps(dto.get("forecast", []), ensure_ascii=False)
        snapshot.fetch_time = now
        snapshot.error_msg = ""

        # ---- 逐日历史滚动更新 + 预警入库 ----
        await self._update_daily(db, area.id, dto)
        await self._store_alerts(db, area.id, dto)

        # ---- 写缓存（value 内嵌 fetched_at 判过期） ----
        result = self._snapshot_to_dict(snapshot)
        settings = await self.get_settings(db)
        await cache_manager.set(
            _cache_key(area.id),
            {"fetched_at": now.isoformat(), "data": result},
            expire=settings["interval_minutes"] * 60,
        )
        return result

    async def list_active_alerts(self, area_id: int, limit: int = 20) -> list[dict]:
        """返回产区当前未过期的天气预警 DTO。"""
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(WeatherAlert).where(
                    WeatherAlert.area_id == area_id,
                    WeatherAlert.end_time >= china_now(),
                ).order_by(WeatherAlert.start_time.desc()).limit(limit)
            )).scalars().all()
        return [{
            "alert_id": row.alert_id, "type": row.alert_type, "level": row.level,
            "title": row.title, "text": row.text,
            "start_time": str(row.start_time) if row.start_time else "",
            "end_time": str(row.end_time) if row.end_time else "",
        } for row in rows]

    async def _store_error(self, db, snapshot, area_id: int, msg: str) -> dict:
        """拉取失败: 仅记 error_msg，保留旧快照数据（行为可预期，不自动切源）"""
        msg = (msg or "未知错误")[:250]
        if not snapshot:
            snapshot = WeatherData(area_id=area_id, error_msg=msg)
            db.add(snapshot)
        else:
            snapshot.error_msg = msg
        logger.warning(f"[天气服务] 产区 {area_id} 拉取失败: {msg}")
        result = self._snapshot_to_dict(snapshot)
        result["status"] = "error" if not snapshot.fetch_time else result["status"]
        return result

    async def _update_daily(self, db, area_id: int, dto: dict):
        """逐日历史滚动更新: 实况温度滚动取当日极值（免费接口无历史回补）"""
        realtime = dto.get("realtime", {}) or {}
        temp = realtime.get("temp")
        if temp is None:
            return
        today = date.today()
        row = (await db.execute(
            select(WeatherDaily).where(
                WeatherDaily.area_id == area_id, WeatherDaily.date == today
            )
        )).scalar_one_or_none()
        if not row:
            row = WeatherDaily(area_id=area_id, date=today,
                               temp_max=temp, temp_min=temp)
            db.add(row)
        else:
            if row.temp_max is None or temp > row.temp_max:
                row.temp_max = temp
            if row.temp_min is None or temp < row.temp_min:
                row.temp_min = temp
        # 日内实时估算日均温，日终 cron 定格
        row.temp_avg = round((row.temp_max + row.temp_min) / 2, 1)
        if realtime.get("humidity") is not None:
            row.humidity = realtime["humidity"]
        # 天气现象/降水取当日预报项
        for item in dto.get("forecast", []) or []:
            if str(item.get("date", "")) == str(today):
                row.text_day = item.get("text_day", "") or row.text_day
                if item.get("precip") is not None:
                    row.precip = item["precip"]
                break
        if realtime.get("wind_scale"):
            row.wind_scale = str(realtime["wind_scale"])

    async def _store_alerts(self, db, area_id: int, dto: dict):
        """官方灾害预警入库（第三方预警 ID UNIQUE 去重，入库即推送通知）

        新预警入库后立即调用 _dispatch_alert 推送农户+管理员双动作通知，
        成功置 notified=1；推送失败不阻断入库，notified 保持 0 由定时补推兜底。
        """
        for alert in dto.get("alerts", []) or []:
            alert_id = str(alert.get("alert_id") or "").strip()
            if not alert_id:
                continue
            existing = (await db.execute(
                select(WeatherAlert).where(WeatherAlert.alert_id == alert_id)
            )).scalar_one_or_none()
            if existing:
                continue
            new_alert = WeatherAlert(
                area_id=area_id, alert_id=alert_id,
                source=dto.get("source", ""),
                alert_type=str(alert.get("type", ""))[:64],
                level=str(alert.get("level", ""))[:32],
                title=str(alert.get("title", ""))[:256],
                text=alert.get("text", ""),
                start_time=_parse_alert_time(alert.get("start_time")),
                end_time=_parse_alert_time(alert.get("end_time")),
            )
            db.add(new_alert)
            await db.flush()  # 取 new_alert.id，便于日志关联
            logger.info(f"[天气服务] 产区 {area_id} 新增预警: {alert.get('title', '')}")
            # 立即推送气象预警通知（农户+管理员双动作，失败不阻断入库，待定时补推）
            try:
                await self._dispatch_alert(db, area_id, alert, alert_orm=new_alert)
            except Exception as e:
                logger.warning(
                    f"[天气服务] 产区 {area_id} 预警通知推送失败（待定时补推）: {e}")

    async def _dispatch_alert(self, db, area_id: int, alert_fields: dict, alert_orm=None):
        """构造预警变量并推送通知（农户+管理员双动作），成功置 alert_orm.notified=1

        立即触发（_store_alerts）与定时补推（weather_worker）共用此 helper。

        参数:
            db: 已开启的会话（调用方负责 commit）
            area_id: 产区ID
            alert_fields: 预警字段 dict（title/level/text/start_time/end_time，
                         来自插件 DTO 或 ORM 转换）
            alert_orm: WeatherAlert ORM 实例（可选，置 notified=1 去重标记）
        """
        from core.events import event_bus
        area_obj = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        area_name = area_obj.name if area_obj else str(area_id)
        # Outbox 与 notified 标记使用同一事务，通知编排由可靠订阅者处理。
        await event_bus.publish_durable("weather.alert", {
            "area_id": area_id, "area_name": area_name,
            "alert_fields": alert_fields,
        }, db)
        if alert_orm is not None:
            alert_orm.notified = 1
            logger.info(
            f"[天气服务] 产区 {area_id} 预警通知已推送: {alert_fields.get('title', '')}")

    async def backfill_recent_daily(self) -> dict:
        """补齐中国时间近十日缺行；汇总和逐日结果只计已提交的数据。"""
        from core.weather_backfill import _backfill_recent_daily
        return await _backfill_recent_daily(self, async_session_factory)

    # ------------------------------------------------------------------
    # 全量拉取（定时任务入口）
    # ------------------------------------------------------------------
    async def pull_all(self) -> dict:
        """
        遍历启用产区拉取天气

        同源产区按坐标四舍五入 2 位小数（约 1km 网格）分组去重，
        同格共享一次 API 调用结果；串行执行 + per-area 异常隔离。
        返回: {total, success, failed, skipped}
        """
        stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        async with async_session_factory() as db:
            areas = (await db.execute(
                select(ProductionArea).where(ProductionArea.status == 1)
            )).scalars().all()
            stats["total"] = len(areas)

            # 网格键 -> 已拉取 DTO（仅 success 复用，失败各自重试）
            grid_cache: dict = {}
            for area in areas:
                if not area.longitude or not area.latitude:
                    stats["skipped"] += 1
                    logger.info(f"[天气服务] 产区 {area.id} 未设置位置，跳过拉取")
                    continue
                try:
                    source = await self.resolve_source(area.id, db)
                    grid = f"{source}:{round(area.longitude, 2)}:{round(area.latitude, 2)}"
                    shared_dto = grid_cache.get(grid)
                    result = await self.fetch_and_store(area, db, dto=shared_dto or None)
                    await db.commit()
                    if result.get("status") == "success" and not result.get("error_msg"):
                        stats["success"] += 1
                    else:
                        stats["failed"] += 1
                    # 缓存本格成功 DTO 供同格产区复用（重新读取快照构造）
                    if shared_dto is None and result.get("status") == "success":
                        grid_cache[grid] = self._result_to_dto(result)
                except Exception as e:
                    await db.rollback()
                    stats["failed"] += 1
                    logger.error(f"[天气服务] 产区 {area.id} 拉取异常隔离: {e}")
        logger.info(f"[天气服务] 全量拉取完成: {stats}")
        return stats

    @staticmethod
    def _result_to_dto(result: dict) -> dict:
        """API 响应结构还原为插件 DTO（供同网格产区复用落库）"""
        return {
            "status": "success", "msg": "",
            "source": result.get("source", ""),
            "granularity": result.get("granularity", ""),
            "realtime": result.get("realtime", {}),
            "hourly": result.get("hourly", []),
            "forecast": result.get("forecast", []),
            # 预警已随首个产区入库，同格复用时不重复携带
            "alerts": [],
        }

    # ------------------------------------------------------------------
    # 积温与日终定格（实现见 core/weather_gdd.py，保持本文件体积可控）
    # ------------------------------------------------------------------


# 全局单例
weather_service = WeatherService()

