"""统一物联平台只读客户端与中国时间归一化。"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from core.hardware_types import HardwareError
from core.time_utils import CHINA_TIMEZONE

UTC_TIMEZONE = timezone(timedelta(0))


FarmbotClientError = HardwareError


def _parse_external_time(value: Any, *, assume_utc: bool) -> str:
    """把平台无时区时间转换为带 +08:00 的 ISO 8601 字符串。"""
    if value in (None, ""):
        return ""
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC_TIMEZONE if assume_utc else CHINA_TIMEZONE)
    return parsed.astimezone(CHINA_TIMEZONE).isoformat(timespec="seconds")


def normalize_history_query_time(value: datetime | None) -> str | None:
    """将管理端历史筛选时间统一为中国时区 ISO 8601。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=CHINA_TIMEZONE)
    return value.astimezone(CHINA_TIMEZONE).isoformat(timespec="seconds")


class FarmbotClient:
    """仅由后端使用的物联平台 API 客户端。"""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        config: dict | None = None,
        total_timeout: float = 20.0,
    ):
        self.config = config or {}
        self._transport = transport
        self._total_timeout = total_timeout

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
    ) -> Any:
        """统一发送物联平台请求并校验其业务响应信封。"""
        token = str(self.config.get("owner_token") or "").strip()
        if not token:
            raise FarmbotClientError("物联平台访问凭据未配置", 503)
        timeout = httpx.Timeout(15.0, connect=5.0, read=15.0, write=5.0, pool=5.0)
        try:
            async with asyncio.timeout(self._total_timeout):
                async with httpx.AsyncClient(
                    base_url=str(self.config.get("base_url") or "https://farmbot-jjr.jjr.vip").rstrip("/"),
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=timeout,
                    transport=self._transport,
                ) as client:
                    response = await client.request(
                        method,
                        path,
                        params=params or None,
                    )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise FarmbotClientError("物联平台请求超时", 504) from exc
        except httpx.HTTPError as exc:
            raise FarmbotClientError("物联平台连接失败") from exc
        if response.status_code in (401, 403):
            raise FarmbotClientError("物联平台鉴权失败", 502)
        if response.status_code >= 400:
            raise FarmbotClientError(f"物联平台返回错误状态 {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise FarmbotClientError("物联平台返回了无效数据") from exc
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise FarmbotClientError("物联平台返回业务错误")
        return payload.get("data")

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params)

    async def _post(self, path: str) -> Any:
        return await self._request("POST", path)

    async def list_devices(self, device_type: str = "") -> list[dict]:
        """获取设备列表；未筛选时不发送 type 参数。"""
        params = {"type": device_type} if device_type else None
        data = await self._get("/api/v1/owner-open/external-devices", params)
        if not isinstance(data, list):
            raise FarmbotClientError("物联平台设备列表格式错误")
        if any(not isinstance(item, dict) for item in data):
            raise FarmbotClientError("物联平台设备列表含非法条目")
        return data

    async def list_identifiers(self, device_name: str) -> list[dict]:
        """获取设备当前数据标识，并把更新时间从 UTC 转为中国时间。"""
        path = f"/api/v1/owner-open/external-devices/{quote(device_name, safe='')}/identifiers"
        data = await self._get(path)
        if not isinstance(data, list):
            raise FarmbotClientError("物联平台数据标识格式错误")
        result = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["lastUpdateTime"] = _parse_external_time(
                raw.get("lastUpdateTime"), assume_utc=True
            )
            result.append(item)
        return result

    async def get_properties(self, device_name: str) -> dict[str, Any]:
        """实时读取设备属性字典，供标识元数据覆盖最新值。"""
        path = (
            "/api/v1/owner-open/external-devices/"
            f"{quote(device_name, safe='')}/properties"
        )
        data = await self._get(path)
        if not isinstance(data, dict):
            raise FarmbotClientError("物联平台实时属性格式错误")
        return data

    async def take_photo(self, device_name: str) -> Any:
        """向植物生长记录仪下发立即拍照指令。"""
        path = (
            "/api/v1/owner-open/external-devices/"
            f"{quote(device_name, safe='')}/take-photo"
        )
        return await self._post(path)

    async def get_history(
        self,
        device_name: str,
        *,
        identifier: str = "",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        size: int = 20,
    ) -> dict:
        """获取设备历史数据，并保持分页结构。"""
        params: dict[str, Any] = {"page": page, "size": size}
        if identifier:
            params["identifier"] = identifier
        normalized_start = normalize_history_query_time(start_time)
        normalized_end = normalize_history_query_time(end_time)
        if normalized_start:
            params["start_time"] = normalized_start
        if normalized_end:
            params["end_time"] = normalized_end
        path = (
            "/api/v1/owner-open/external-devices/"
            f"{quote(device_name, safe='')}/properties/history"
        )
        data = await self._get(path, params)
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise FarmbotClientError("物联平台历史数据格式错误")
        items = []
        for raw in data["list"]:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["time"] = _parse_external_time(raw.get("time"), assume_utc=False)
            items.append(item)
        return {
            "list": items,
            "total": int(data.get("total") or 0),
            "page": int(data.get("page") or page),
            "size": int(data.get("size") or size),
        }

