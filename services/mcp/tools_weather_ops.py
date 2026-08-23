"""
天气运维 MCP 核心工具（audience=admin）

- weather_refresh_area   单产区强制刷新天气（weather:config，绕过缓存实时拉取）

历史说明: 全量拉取（weather_pull_all）与数据源状态（weather_source_status）
已下线——全量拉取同步遍历全部产区易超时且定时任务已覆盖，
数据源状态属纯配置查看价值偏低。

weather_service 为进程内单例，handler 内延迟 import（便于测试打桩，
与 tools_core.weather_snapshot 同款约定）。
"""
import logging

from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)


async def weather_refresh_area(area_id: int) -> dict:
    """强制刷新指定产区的天气数据（绕过缓存实时拉取）。

    :param area_id: 产区ID
    :return: 刷新后的天气快照（实况/逐时/预报）
    """
    from core.weather_service import weather_service

    data = await weather_service.get_weather(area_id, force=True)
    if data.get("status") == "error" or data.get("error_msg"):
        raise ToolError(f"刷新失败: {data.get('error_msg') or '未知错误'}")
    return {"area_id": area_id, "weather": data}


# 天气运维工具声明列表（tools_core.py 聚合进 CORE_TOOLS）
WEATHER_OPS_TOOLS: list[dict] = [
    {
        "name": "weather_refresh_area",
        "description": "强制刷新单个产区的天气数据（绕过缓存实时拉取）。"
                       "参数 area_id 为产区ID。返回刷新后的天气快照。",
        "handler": weather_refresh_area,
        "audience": "admin",
        "permission_code": "weather:config",
    },
]
