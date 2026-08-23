"""
系统核心 MCP 工具（聚合入口）

本模块定义基础两个工具，并聚合各领域模块的工具声明为 CORE_TOOLS：
- weather_snapshot     产区天气快照（audience=both；管理员需 weather:view 权限，
                       农户需绑定该产区）
- my_production_areas  我绑定的产区列表（audience=farmer）
- tools_area.py        产区三级树（core_area_tree）
- tools_weather.py     积温/逐日历史/气象预警（core_batch_gdd 等）
- tools_admin.py       农户列表/任务监控闭环/系统概览（core_farmer_list、
                       core_task_pending、core_system_overview 等）
- tools_farmer_ops.py  农户管理操作（core_farmer_set_status、产区增量绑/解单农户）
- tools_area_write.py  产区三级写操作（core_area_create 等六个，不含删除）
- tools_weather_ops.py 天气运维（core_weather_refresh_area 单产区强刷）
- tools_notice.py      通知操作（core_notify_farmer 给农户发站内信）

handler 约定（与 registry.py 协议一致）:
- 内部用 async_session_factory() 自建会话
- 用 get_access_token() 读取身份 claims（farmer 工具按 farmer_id 过滤数据）
- 业务性失败抛 ToolError（MCP 客户端可见的错误消息）
- 共享助手统一放在 tool_utils.py（避免领域模块反向 import 本模块成环）
"""
import logging

from sqlalchemy import select
from fastmcp.exceptions import ToolError

from core.db.base import async_session_factory
from core.db.production_area import ProductionArea, AreaFarmer
from services.mcp.tool_utils import _current_claims, _farmer_area_bound
from services.mcp.tools_area import AREA_TOOLS
from services.mcp.tools_weather import WEATHER_TOOLS
from services.mcp.tools_admin import ADMIN_TOOLS
from services.mcp.tools_farmer_ops import FARMER_OPS_TOOLS
from services.mcp.tools_area_write import AREA_WRITE_TOOLS
from services.mcp.tools_weather_ops import WEATHER_OPS_TOOLS
from services.mcp.tools_notice import NOTICE_TOOLS

logger = logging.getLogger(__name__)


async def weather_snapshot(area_id: int) -> dict:
    """查询指定产区的天气快照。

    :param area_id: 产区ID（农户可通过 core_my_production_areas 工具获取自己的产区ID）
    :return: 天气快照数据（实况/逐时/预报），来源与系统天气服务一致
    """
    claims = _current_claims()

    async with async_session_factory() as db:
        # 产区存在性校验
        area = (await db.execute(
            select(ProductionArea.id, ProductionArea.name)
            .where(ProductionArea.id == area_id)
        )).first()
        if area is None:
            raise ToolError(f"产区不存在: {area_id}")

        # 农户仅可查询本人绑定的产区（管理员由权限中间件按 weather:view 把关）
        if claims.get("user_type") == "farmer":
            if not await _farmer_area_bound(db, claims["user_id"], area_id):
                raise ToolError("您未绑定该产区，无权查询其天气")

    from core.weather_service import weather_service
    data = await weather_service.get_weather(area_id)
    return {"area_id": area_id, "area_name": area[1], "weather": data}


async def my_production_areas() -> list[dict]:
    """查询我（当前农户）绑定的产区列表。

    :return: 产区列表，每项含 id / name / code
    """
    claims = _current_claims()
    farmer_id = claims["user_id"]

    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProductionArea.id, ProductionArea.name, ProductionArea.code)
            .where(ProductionArea.id.in_(
                select(AreaFarmer.area_id).where(AreaFarmer.farmer_id == farmer_id)
            ))
            .order_by(ProductionArea.id)
        )).all()

    return [{"id": r[0], "name": r[1], "code": r[2]} for r in rows]


# 核心工具声明列表（register_core_tools 使用；聚合各领域模块声明）
CORE_TOOLS: list[dict] = [
    {
        "name": "weather_snapshot",
        "description": "查询指定产区的天气快照（实况/逐时/预报）。参数 area_id 为产区ID。",
        "handler": weather_snapshot,
        "audience": "both",
        "permission_code": "weather:view",
    },
    {
        "name": "my_production_areas",
        "description": "查询当前农户绑定的产区列表，返回每个产区的 id/name/code。",
        "handler": my_production_areas,
        "audience": "farmer",
        "permission_code": None,
    },
    *AREA_TOOLS,
    *WEATHER_TOOLS,
    *ADMIN_TOOLS,
    *FARMER_OPS_TOOLS,
    *AREA_WRITE_TOOLS,
    *WEATHER_OPS_TOOLS,
    *NOTICE_TOOLS,
]
