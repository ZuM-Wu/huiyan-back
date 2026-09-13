"""和风时光机私有协议适配：先定位城市，再读取真实历史日值。"""
import math
import re
from datetime import date

import httpx

_SOURCE = "weather_qweather"
_HISTORY_PATH = "/v7/historical/weather"
_GEO_PATH = "/geo/v2/city/lookup"


class _HistoryError(ValueError):
    """只携带受控诊断字段，禁止将响应正文和凭据写入任务日志。"""

    def __init__(self, reason, path=_HISTORY_PATH, http_status=None, code=""):
        self.path, self.http_status, self.code = path, http_status, code
        super().__init__(reason)


def _number(value):
    """无效或非有限值按缺失处理，零度/零降水仍为有效数据。"""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _business_code(data):
    code = data.get("code")
    error = data.get("error")
    if code is None and isinstance(error, dict):
        code = error.get("status") or error.get("type", "").rsplit("/", 1)[-1]
    # 第三方可能回显输入，只接受短枚举码，原始正文一律不输出。
    return str(code) if re.fullmatch(r"[A-Za-z0-9_-]{1,48}", str(code or "")) else "missing"


async def _request(plugin, client, path, params):
    try:
        response = await client.get(
            f"https://{plugin._api_host()}{path}", params={**params, "lang": "zh"},
            headers=plugin._build_headers())
    except httpx.HTTPError as exc:
        raise _HistoryError(f"网络请求失败（{type(exc).__name__}）", path) from None
    try:
        data = response.json()
    except ValueError:
        raise _HistoryError("接口返回非 JSON", path, response.status_code) from None
    if not isinstance(data, dict):
        raise _HistoryError("接口返回结构无效", path, response.status_code)
    code = _business_code(data)
    if response.status_code != 200 or code != "200":
        reason = "接口请求失败，请核查参数、账号和接口权限"
        if response.status_code == 403 or code == "403":
            reason = "接口拒绝访问，请核查账号和时光机/地理查询权限"
        raise _HistoryError(reason, path, response.status_code, code)
    return data


def _normalize(data, target):
    rows = data.get("weatherDaily") or data.get("daily")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        raise _HistoryError("历史天气接口返回空数据或无效结构")
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise _HistoryError("历史天气逐日记录结构无效")
        try:
            row_date = date.fromisoformat(str(row.get("date") or row.get("fxDate") or ""))
        except ValueError:
            raise _HistoryError("历史天气返回无效日期") from None
        if row_date != target:
            raise _HistoryError("历史天气返回日期与请求日期不符")
        high, low = _number(row.get("tempMax")), _number(row.get("tempMin"))
        avg = _number(row.get("tempAvg"))
        if high is not None and low is not None:
            if high < low:
                raise _HistoryError("历史天气最高温低于最低温")
            # 与平台日终定格口径一致；不是用预报或邻日猜测历史值。
            avg = avg if avg is not None else (high + low) / 2
        if avg is None:
            raise _HistoryError("历史天气缺少有效日温，不能用于积温")
        normalized.append({
            "date": target.isoformat(), "temp_max": high, "temp_min": low,
            "temp_avg": avg, "humidity": _number(row.get("humidity")),
            "precip": _number(row.get("precip")),
            "wind_scale": str(row.get("windScaleDay") or "")[:16],
            "text_day": str(row.get("textDay") or "")[:64],
        })
    return normalized


async def _fetch_history(plugin, loc, target_date, timeout):
    """不复用实况坐标参数：历史 API 只接收 GeoAPI 的 LocationID 和 yyyyMMdd。"""
    path = _HISTORY_PATH
    try:
        target = date.fromisoformat(target_date)
        lon, lat = _number(loc.get("longitude")), _number(loc.get("latitude"))
        if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise _HistoryError("产区未设置有效经纬度")
        coordinate = f"{lon:.2f},{lat:.2f}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            # 同一实例处理一个产区的多日缺口，仅缓存成功解析；不落数据库、不缓存错误。
            cache = getattr(plugin, "_history_locations", {})
            location_id = cache.get(coordinate)
            if not location_id:
                path = _GEO_PATH
                geo = await _request(plugin, client, path, {"location": coordinate, "number": 1})
                locations = geo.get("location")
                if not isinstance(locations, list) or not locations or not isinstance(locations[0], dict):
                    raise _HistoryError("GeoAPI 未返回可用 LocationID", path)
                location_id = str(locations[0].get("id") or "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", location_id):
                    raise _HistoryError("GeoAPI 返回无效 LocationID", path)
                plugin._history_locations = {**cache, coordinate: location_id}
            path = _HISTORY_PATH
            data = await _request(plugin, client, path, {
                "location": location_id, "date": target.strftime("%Y%m%d")})
        return {"status": "success", "source": _SOURCE, "daily": _normalize(data, target)}
    except _HistoryError as exc:
        return {"status": "error", "source": _SOURCE, "date": target_date,
                "path": exc.path, "http_status": exc.http_status, "code": exc.code,
                "msg": f"{target_date} {exc.path} HTTP={exc.http_status or '-'} "
                       f"业务码={exc.code or '-'}：{exc}"}
    except Exception as exc:
        # 配置、认证与意外响应错误也不能泄露私钥或带认证参数的 URL。
        return {"status": "error", "source": _SOURCE, "date": target_date,
                "path": path, "http_status": None, "code": "",
                "msg": f"{target_date} {path} 配置或数据解析失败（{type(exc).__name__}）"}
