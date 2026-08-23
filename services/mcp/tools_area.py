"""
产区管理 MCP 核心工具

- area_tree  产区/地块/批次三级树（audience=both；管理员需 area:list 权限，
             农户仅返回本人绑定的产区子树）

数据级权限说明: 农户绑定唯一来源为 hy_area_farmer 关联表，
不可使用 list_area_tree(farmer_id) 的旧列过滤（ProductionArea.farmer_id 已退役）。
"""
import logging

from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.production_area import AreaFarmer
from services.mcp.tool_utils import _current_claims

logger = logging.getLogger(__name__)


async def area_tree() -> list[dict]:
    """查询产区/地块/批次三级树结构。

    :return: 树形列表，每项含 id / name / type（area/plot/batch）/ children；
             管理员返回全部产区，农户仅返回本人绑定的产区
    """
    claims = _current_claims()

    from core.production_area_service import list_area_tree
    # 全量树（list_area_tree 的 farmer_id 参数走已退役旧列，此处不使用）
    tree = await list_area_tree(None)

    # 农户按 hy_area_farmer 绑定关系过滤顶层产区节点
    if claims.get("user_type") == "farmer":
        async with async_session_factory() as db:
            bound_ids = set((await db.execute(
                select(AreaFarmer.area_id).where(
                    AreaFarmer.farmer_id == claims["user_id"]
                )
            )).scalars().all())
        tree = [node for node in tree if node.get("id") in bound_ids]

    return tree


# 产区工具声明列表（tools_core.py 聚合进 CORE_TOOLS）
AREA_TOOLS: list[dict] = [
    {
        "name": "area_tree",
        "description": "查询产区/地块/种植批次三级树结构，无参数。"
                       "返回树形列表，每项含 id/name/type(area|plot|batch)/children；"
                       "管理员返回全部产区，农户仅返回本人绑定的产区。",
        "handler": area_tree,
        "audience": "both",
        "permission_code": "area:list",
    },
]
