"""天气历史回补私有实现：逐日请求隔离、产区原子提交、提交后统计。"""
import asyncio
import math
from datetime import date, timedelta

from sqlalchemy import select

from core.db.production_area import ProductionArea
from core.db.weather import WeatherDaily
from core.time_utils import china_now

_backfill_lock = asyncio.Lock()


def _result(area_id, source, status, reason, target=None):
    return {"area_id": area_id, "date": str(target) if target else None,
            "source": source, "status": status, "reason": reason}


def _numeric(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("历史天气数值无效")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("历史天气数值无效") from None
    if not math.isfinite(number):
        raise ValueError("历史天气数值非有限值")
    return number


def _candidate(dto, target):
    """整批校验后才生成候选行，避免半条数据已经 add 后解析另一条失败。"""
    if not isinstance(dto, dict):
        raise ValueError("历史天气返回结构无效")
    if dto.get("status") != "success":
        raise ValueError(str(dto.get("msg") or "历史天气接口返回失败")[:400])
    rows = dto.get("daily")
    if not isinstance(rows, list) or not rows:
        raise ValueError("历史天气接口返回空数据或无效结构")
    candidate = None
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("历史天气记录结构无效")
        try:
            item_date = date.fromisoformat(str(item.get("date") or ""))
        except ValueError:
            raise ValueError("历史天气日期无效") from None
        if item_date != target:
            raise ValueError("历史天气日期越界或与目标日期不符")
        values = {key: _numeric(item.get(key)) for key in
                  ("temp_max", "temp_min", "temp_avg", "humidity", "precip")}
        high, low = values["temp_max"], values["temp_min"]
        if high is not None and low is not None:
            if high < low:
                raise ValueError("历史天气最高温低于最低温")
            if values["temp_avg"] is None:
                values["temp_avg"] = (high + low) / 2
        if values["temp_avg"] is None:
            raise ValueError("历史天气缺少有效日温")
        values.update(date=item_date, wind_scale=str(item.get("wind_scale") or "")[:16],
                      text_day=str(item.get("text_day") or "")[:64])
        if candidate is not None and candidate != values:
            raise ValueError("历史天气返回冲突的重复日期")
        candidate = values
    return candidate


async def _collect(plugin, area, source, missing):
    candidates, results = [], []
    for target in missing:
        try:
            dto = await plugin.fetch_historical_weather(
                {"longitude": area.longitude, "latitude": area.latitude}, str(target))
            values = _candidate(dto, target)
            candidates.append(WeatherDaily(area_id=area.id, **values))
            results.append(_result(area.id, source, "pending", "等待产区事务提交", target))
        except ValueError as exc:
            results.append(_result(area.id, source, "failed", str(exc), target))
        except Exception as exc:
            results.append(_result(area.id, source, "failed",
                                   f"历史天气请求异常（{type(exc).__name__}）", target))
    return candidates, results


async def _process_area(service, db, area_id, today, results):
    area = (await db.execute(select(ProductionArea).where(
        ProductionArea.id == area_id, ProductionArea.status == 1))).scalar_one_or_none()
    if area is None:
        results.append(_result(area_id, "", "skipped", "产区已停用或不存在"))
        return
    source = await service.resolve_source(area_id, db)
    if not source or source != "weather_qweather":
        reason = "未配置天气数据源" if not source else "当前数据源不支持历史回补"
        results.append(_result(area_id, source, "skipped", reason))
        return
    if area.longitude is None or area.latitude is None:
        results.append(_result(area_id, source, "skipped", "产区未设置经纬度"))
        return
    plugin, error = await service.load_plugin(source, db)
    if not plugin or not callable(getattr(plugin, "fetch_historical_weather", None)):
        results.append(_result(area_id, source, "failed", error or "历史天气插件不可用"))
        return
    start = max(area.create_time.date(), today - timedelta(days=10))
    existing = set((await db.execute(select(WeatherDaily.date).where(
        WeatherDaily.area_id == area_id, WeatherDaily.date >= start,
        WeatherDaily.date < today))).scalars().all())
    missing = [start + timedelta(days=i) for i in range((today - start).days)
               if start + timedelta(days=i) not in existing]
    candidates, daily_results = await _collect(plugin, area, source, missing)
    results.extend(daily_results)
    if not candidates:
        return
    # 唯一约束是并发写入的最终保护；失败回滚整个产区，不覆盖已存在行。
    db.add_all(candidates)
    await db.commit()
    for result in results:
        if result["status"] == "pending":
            result.update(status="filled", reason="历史天气已补齐并提交")


async def _run_area(service, session_factory, area_id, today):
    results = []
    async with session_factory() as db:
        try:
            await _process_area(service, db, area_id, today, results)
        except Exception as exc:
            await db.rollback()
            reason = f"产区回补事务失败并已回滚（{type(exc).__name__}）"
            pending = [result for result in results if result["status"] == "pending"]
            for result in pending:
                result.update(status="failed", reason=reason)
            if not pending:
                results.append(_result(area_id, "", "failed", reason))
    return results


async def _backfill_recent_daily(service, session_factory):
    async with _backfill_lock:
        today = china_now().date()
        async with session_factory() as db:
            area_ids = (await db.execute(select(ProductionArea.id).where(
                ProductionArea.status == 1))).scalars().all()
        results = []
        for area_id in area_ids:
            results.extend(await _run_area(service, session_factory, area_id, today))
        return {"total": len(area_ids),
                "filled": sum(row["status"] == "filled" for row in results),
                "failed": sum(row["status"] == "failed" for row in results),
                "skipped": sum(row["status"] == "skipped" for row in results),
                "results": results,
                "errors": [row for row in results if row["status"] in ("failed", "skipped")]}
