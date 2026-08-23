"""
天气增强 MCP 核心工具

- batch_gdd       批次积温统计（活动/有效积温，复用 weather_gdd.get_accumulated_temp）
- weather_daily   逐日天气历史（复用 weather_gdd.list_daily，限量返回）
- weather_alerts  生效气象预警（与 api/farmer/weather.py 同查询口径）

三个工具 audience=both：管理员需 weather:view 权限（RBAC 中间件把关），
农户须绑定目标产区（handler 内数据级校验）。
"""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select, desc
from fastmcp.exceptions import ToolError

from core.db.base import async_session_factory
from core.db.weather import WeatherAlert
from services.mcp.tool_utils import _current_claims, ensure_farmer_bound

logger = logging.getLogger(__name__)

# 逐日历史单次返回行数上限（限量返回约定，避免向 AI 倾倒全表）
DAILY_MAX_ROWS = 366
# 不传起始日期时的默认回溯天数
DAILY_DEFAULT_DAYS = 30


def _parse_date(value: str, field: str) -> date | None:
    """解析 YYYY-MM-DD 日期字符串（空串返回 None，非法格式抛 ToolError）"""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ToolError(f"{field} 日期格式非法（应为 YYYY-MM-DD）: {value}")


async def batch_gdd(area_id: int, batch_id: int) -> dict:
    """查询种植批次的积温统计（活动/有效积温，自定植日起算）。

    :param area_id: 产区ID
    :param batch_id: 种植批次ID（可通过 core_area_tree 工具获取）
    :return: 含作物名/批次号/种植日期/活动积温/有效积温/统计天数/缺失天数
    """
    claims = _current_claims()
    await ensure_farmer_bound(claims, area_id)

    from core import weather_gdd
    result = await weather_gdd.get_accumulated_temp(area_id, batch_id)
    if result.get("status") == "error":
        raise ToolError(result.get("msg", "积温统计失败"))
    return result


async def weather_daily(area_id: int, start: str = "", end: str = "") -> list[dict]:
    """查询产区的逐日天气历史。

    :param area_id: 产区ID
    :param start: 起始日期 YYYY-MM-DD（不传默认最近 30 天）
    :param end: 结束日期 YYYY-MM-DD（不传默认到今天）
    :return: 逐日列表，每项含 date/temp_max/temp_min/temp_avg/humidity/
             precip/wind_scale/text_day，最多返回 366 条
    """
    claims = _current_claims()
    await ensure_farmer_bound(claims, area_id)

    start_date = _parse_date(start, "start")
    end_date = _parse_date(end, "end")
    # 默认窗口: 最近 30 天（避免无范围时拉取全部历史）
    if start_date is None:
        start_date = date.today() - timedelta(days=DAILY_DEFAULT_DAYS)

    from core import weather_gdd
    rows = await weather_gdd.list_daily(area_id, start_date, end_date)
    # 超上限时保留最近的数据（列表按日期升序，截尾部）
    if len(rows) > DAILY_MAX_ROWS:
        rows = rows[-DAILY_MAX_ROWS:]
    return rows


async def weather_alerts(area_id: int) -> list[dict]:
    """查询产区当前生效的气象灾害预警。

    :param area_id: 产区ID
    :return: 预警列表（最多 20 条，按开始时间倒序），每项含
             alert_id/type/level/title/text/start_time/end_time
    """
    claims = _current_claims()
    await ensure_farmer_bound(claims, area_id)

    async with async_session_factory() as db:
        # 生效预警（end_time 未过期），与农户端天气接口同口径
        alerts = (await db.execute(
            select(WeatherAlert).where(
                WeatherAlert.area_id == area_id,
                WeatherAlert.end_time >= datetime.now(),
            ).order_by(desc(WeatherAlert.start_time)).limit(20)
        )).scalars().all()

    return [
        {
            "alert_id": a.alert_id,
            "type": a.alert_type,
            "level": a.level,
            "title": a.title,
            "text": a.text or "",
            "start_time": str(a.start_time) if a.start_time else "",
            "end_time": str(a.end_time) if a.end_time else "",
        }
        for a in alerts
    ]


# 天气工具声明列表（tools_core.py 聚合进 CORE_TOOLS）
WEATHER_TOOLS: list[dict] = [
    {
        "name": "batch_gdd",
        "description": "查询种植批次的积温统计（活动/有效积温，自定植日起算，缺失日跳过）。"
                       "参数 area_id 为产区ID、batch_id 为种植批次ID（可用 core_area_tree 查询）。"
                       "返回作物名/批次号/种植日期/活动积温/有效积温/统计与缺失天数。",
        "handler": batch_gdd,
        "audience": "both",
        "permission_code": "weather:view",
    },
    {
        "name": "weather_daily",
        "description": "查询产区逐日天气历史。参数 area_id 为产区ID，start/end 为可选日期范围"
                       "（YYYY-MM-DD，不传 start 默认最近30天）。返回逐日列表（最多366条），"
                       "每项含日期/最高最低均温/湿度/降水/风力/天气现象。",
        "handler": weather_daily,
        "audience": "both",
        "permission_code": "weather:view",
    },
    {
        "name": "weather_alerts",
        "description": "查询产区当前生效的气象灾害预警。参数 area_id 为产区ID。"
                       "返回预警列表（最多20条），每项含预警类型/等级/标题/详情/起止时间。",
        "handler": weather_alerts,
        "audience": "both",
        "permission_code": "weather:view",
    },
]
