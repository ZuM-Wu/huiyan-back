"""
农户管理操作 MCP 核心工具（audience=admin）

- farmer_set_status    启用/禁用农户（需 farmer:status 权限，对齐
                       api/admin/farmer.py 的 /{id}/status 接口）
- area_bind_farmer     产区增量绑定单个农户（需 area:update 权限）
- area_unbind_farmer   产区解绑单个农户（需 area:update 权限）

绑定/解绑刻意采用「增量单农户」语义：后台 bind_area_farmers 接口是整集替换
（先清空再写入），AI 侧若误用整集语义可能清空产区全部绑定，故 MCP 只暴露
单农户粒度的绑/解操作。写操作 commit 后统一写 active_log（文案带 MCP 前缀
便于审计区分，request=None 自动落 system 归因）。
"""
import logging

from sqlalchemy import select, update
from fastmcp.exceptions import ToolError

from core.db.base import async_session_factory
from core.db.farmer import Farmer
from core.db.production_area import ProductionArea, AreaFarmer

logger = logging.getLogger(__name__)


async def farmer_set_status(farmer_id: int, status: int) -> dict:
    """启用或禁用指定农户账号。

    :param farmer_id: 农户ID（由 core_farmer_list 工具获取）
    :param status: 目标状态，0=禁用 1=启用
    :return: 操作结果，含 farmer_id/username/status/msg
    """
    if status not in (0, 1):
        raise ToolError("status 仅支持 0=禁用 或 1=启用")

    from core.log.active_log import active_log

    async with async_session_factory() as db:
        # 查询顺序约定: 1.农户存在性（handler 内单条查询，便于测试打桩）
        username = (await db.execute(
            select(Farmer.username).where(Farmer.id == farmer_id)
        )).scalar_one_or_none()
        if username is None:
            raise ToolError(f"农户不存在: {farmer_id}")

        await db.execute(
            update(Farmer).where(Farmer.id == farmer_id).values(status=status)
        )
        await db.commit()

        text = "启用" if status == 1 else "禁用"
        await active_log(f"MCP切换农户状态为{text}: {username}",
                         "farmer_status", rel_id=farmer_id, db=db)
        return {"farmer_id": farmer_id, "username": username,
                "status": status, "msg": f"农户已{text}"}


async def area_bind_farmer(area_id: int, farmer_id: int) -> dict:
    """将单个农户增量绑定到指定产区（已绑定则幂等返回，不影响其他绑定）。

    :param area_id: 产区ID
    :param farmer_id: 农户ID
    :return: 操作结果，含 area_id/farmer_id/msg
    """
    from core.log.active_log import active_log

    async with async_session_factory() as db:
        # 查询顺序约定: 1.产区存在 -> 2.农户存在 -> 3.绑定行是否已存在
        area_name = (await db.execute(
            select(ProductionArea.name).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if area_name is None:
            raise ToolError(f"产区不存在: {area_id}")

        username = (await db.execute(
            select(Farmer.username).where(Farmer.id == farmer_id)
        )).scalar_one_or_none()
        if username is None:
            raise ToolError(f"农户不存在: {farmer_id}")

        bound = (await db.execute(
            select(AreaFarmer.id).where(
                AreaFarmer.area_id == area_id,
                AreaFarmer.farmer_id == farmer_id,
            ).limit(1)
        )).scalar_one_or_none()
        if bound is not None:
            # 幂等: 重复绑定不报错、不重复插入
            return {"area_id": area_id, "farmer_id": farmer_id,
                    "msg": "该农户已绑定此产区"}

        db.add(AreaFarmer(area_id=area_id, farmer_id=farmer_id))
        await db.commit()
        await active_log(f"MCP产区绑定农户: {area_name} <- {username}",
                         "area", rel_id=area_id, db=db)
        return {"area_id": area_id, "farmer_id": farmer_id, "msg": "绑定成功"}


async def area_unbind_farmer(area_id: int, farmer_id: int) -> dict:
    """将单个农户从指定产区解绑（仅删除绑定关系行，不删除任何实体数据）。

    :param area_id: 产区ID
    :param farmer_id: 农户ID
    :return: 操作结果，含 area_id/farmer_id/msg
    """
    from core.log.active_log import active_log

    async with async_session_factory() as db:
        # 查询顺序约定: 1.绑定行是否存在（不存在直接幂等返回）
        row = (await db.execute(
            select(AreaFarmer).where(
                AreaFarmer.area_id == area_id,
                AreaFarmer.farmer_id == farmer_id,
            ).limit(1)
        )).scalar_one_or_none()
        if row is None:
            return {"area_id": area_id, "farmer_id": farmer_id,
                    "msg": "该农户未绑定此产区"}

        await db.delete(row)
        await db.commit()
        await active_log(f"MCP产区解绑农户: area_id={area_id}, farmer_id={farmer_id}",
                         "area", rel_id=area_id, db=db)
        return {"area_id": area_id, "farmer_id": farmer_id, "msg": "解绑成功"}


# 农户管理操作工具声明列表（tools_core.py 聚合进 CORE_TOOLS）
FARMER_OPS_TOOLS: list[dict] = [
    {
        "name": "farmer_set_status",
        "description": "启用或禁用农户账号。参数 farmer_id 为农户ID"
                       "（由 core_farmer_list 获取），status 目标状态（0=禁用 1=启用）。",
        "handler": farmer_set_status,
        "audience": "admin",
        "permission_code": "farmer:status",
    },
    {
        "name": "area_bind_farmer",
        "description": "将单个农户增量绑定到产区（仅新增这一条绑定关系，不影响该产区"
                       "已有的其他农户绑定；已绑定则幂等返回）。参数 area_id 产区ID，"
                       "farmer_id 农户ID。",
        "handler": area_bind_farmer,
        "audience": "admin",
        "permission_code": "area:update",
    },
    {
        "name": "area_unbind_farmer",
        "description": "将单个农户从产区解绑（仅删除这一条绑定关系，不删除产区或农户"
                       "任何数据；未绑定则幂等返回）。参数 area_id 产区ID，farmer_id 农户ID。",
        "handler": area_unbind_farmer,
        "audience": "admin",
        "permission_code": "area:update",
    },
]
