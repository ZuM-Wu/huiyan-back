"""农业主链 MCP Resources。

Resources 只把已经通过数据范围校验的工具结果序列化为 JSON，不复制领域
查询逻辑。这样 Tools 与 Resources 在产区绑定、返回上限和天气回退语义上始终
保持一致。
"""
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp.exceptions import ResourceError, ToolError

from services.mcp.tools_area import area_tree
from services.mcp.tools_core import weather_snapshot
from services.mcp.tools_weather import batch_gdd, weather_alerts, weather_daily


def _json_text(value: Any) -> str:
    """生成 UTF-8 友好的 JSON 文本，日期等领域值统一转为字符串。"""
    return json.dumps(value, ensure_ascii=False, default=str)


async def _resource_result(
    handler: Callable[..., Awaitable[Any]],
    **kwargs: Any,
) -> str:
    """把工具业务错误转换为 Resource 协议错误，避免错误类型跨能力泄漏。"""
    try:
        return _json_text(await handler(**kwargs))
    except ToolError as exc:
        raise ResourceError(str(exc)) from exc


async def production_areas_resource() -> str:
    """读取当前身份可见的产区、地块和种植批次树。"""
    return await _resource_result(area_tree)


async def area_weather_resource(area_id: int) -> str:
    """读取指定产区的天气快照。"""
    return await _resource_result(weather_snapshot, area_id=area_id)


async def area_weather_alerts_resource(area_id: int) -> str:
    """读取指定产区当前生效的气象预警。"""
    return await _resource_result(weather_alerts, area_id=area_id)


async def area_weather_daily_resource(area_id: int) -> str:
    """读取指定产区默认近 30 天口径的逐日天气历史。"""
    return await _resource_result(weather_daily, area_id=area_id)


async def batch_gdd_resource(area_id: int, batch_id: int) -> str:
    """读取指定产区种植批次的积温统计。"""
    return await _resource_result(batch_gdd, area_id=area_id, batch_id=batch_id)


CORE_RESOURCES: list[dict] = [
    {
        "uri": "huiyan://agriculture/areas",
        "name": "production_areas",
        "description": "当前身份可见的产区、地块和种植批次树。",
        "handler": production_areas_resource,
        "mime_type": "application/json",
        "audience": "both",
        "permission_code": "area:list",
    },
    {
        "uri": "huiyan://agriculture/areas/{area_id}/weather",
        "name": "area_weather",
        "description": "指定产区的天气实况、逐时预报和未来预报快照。",
        "handler": area_weather_resource,
        "mime_type": "application/json",
        "audience": "both",
        "permission_code": "weather:view",
    },
    {
        "uri": "huiyan://agriculture/areas/{area_id}/weather/alerts",
        "name": "area_weather_alerts",
        "description": "指定产区当前生效的气象灾害预警，最多返回 20 条。",
        "handler": area_weather_alerts_resource,
        "mime_type": "application/json",
        "audience": "both",
        "permission_code": "weather:view",
    },
    {
        "uri": "huiyan://agriculture/areas/{area_id}/weather/daily",
        "name": "area_weather_daily",
        "description": "指定产区默认近 30 天口径的逐日天气历史。",
        "handler": area_weather_daily_resource,
        "mime_type": "application/json",
        "audience": "both",
        "permission_code": "weather:view",
    },
    {
        "uri": "huiyan://agriculture/areas/{area_id}/batches/{batch_id}/gdd",
        "name": "batch_gdd",
        "description": "指定产区种植批次的活动积温和有效积温统计。",
        "handler": batch_gdd_resource,
        "mime_type": "application/json",
        "audience": "both",
        "permission_code": "weather:view",
    },
]
