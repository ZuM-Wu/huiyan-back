"""产区扩展契约服务

提供稳定的只读内部服务，供未来插件消费（而非直连表内部）：
- get_area_geo(area_id)         → 供天气插件按产区定位取天气
- list_area_tree(farmer_id)     → 供工单/物联网/监测插件下拉选择绑定目标
- get_plot_sample_base(plot_id) → 供虫情/生长监测摄像头计算抽样样本量

约定：本模块保证产区/地块/批次主键稳定、不复用、软停用而非物理删，
防止未来插件外键悬空。
"""
from typing import Optional, cast

from sqlalchemy import select, func

from core.db.base import async_session_factory
from core.db.production_area import ProductionArea, Plot, PlantingBatch, AreaFarmer


def _fmt_date(value) -> str:
    """统一序列化种植日期，数据库空值返回空字符串。"""
    return value.strftime("%Y-%m-%d") if value else ""


async def get_area_geo(area_id: int) -> Optional[dict]:
    """返回产区地理定位信息，供天气插件按产区取天气。

    参数:
        area_id: 产区ID
    返回:
        {province, city, district, longitude, latitude} 或 None（产区不存在）
    """
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if not area:
            return None
        return {
            "province": area.province,
            "city": area.city,
            "district": area.district,
            "longitude": area.longitude,
            "latitude": area.latitude,
        }


async def list_area_tree(farmer_id: Optional[int] = None) -> list:
    """返回 产区/地块/批次 三级树结构。

    供工单插件下拉选择"异常的产区/地块"、供物联网/监测插件选择绑定目标。

    参数:
        farmer_id: 可选，按农户过滤；为 None 时返回全部
    返回:
        [{id, name, type:'area', children:[{id, name, type:'plot', children:[...]}]}]
    """
    async with async_session_factory() as db:
        area_q = select(ProductionArea).order_by(
            ProductionArea.sort_order, ProductionArea.id
        )
        if farmer_id is not None:
            area_ids = select(AreaFarmer.area_id).where(
                AreaFarmer.farmer_id == farmer_id
            )
            area_q = area_q.where(ProductionArea.id.in_(area_ids))
        areas = (await db.execute(area_q)).scalars().all()

        plots = (await db.execute(
            select(Plot).order_by(Plot.sort_order, Plot.id)
        )).scalars().all()
        batches = (await db.execute(
            select(PlantingBatch).order_by(PlantingBatch.id)
        )).scalars().all()

        # 按父级分组
        plots_by_area: dict[int, list[Plot]] = {}
        for p in plots:
            plots_by_area.setdefault(cast(int, p.area_id), []).append(p)
        batches_by_plot: dict[int, list[PlantingBatch]] = {}
        for b in batches:
            batches_by_plot.setdefault(cast(int, b.plot_id), []).append(b)

        tree: list[dict] = []
        for a in areas:
            area_node: dict = {
                "id": a.id, "name": a.name, "type": "area",
                "status": a.status, "children": [],
            }
            for p in plots_by_area.get(cast(int, a.id), []):
                plot_node: dict = {
                    "id": p.id, "name": p.name, "type": "plot",
                    "status": p.status, "children": [],
                }
                for b in batches_by_plot.get(cast(int, p.id), []):
                    plot_node["children"].append({
                        "id": b.id,
                        "name": b.batch_no or b.crop_name,
                        "type": "batch",
                        "status": b.status,
                    })
                area_node["children"].append(plot_node)
            tree.append(area_node)
        return tree


async def get_plot_sample_base(plot_id: int) -> dict:
    """汇总地块下所有批次的种植株数，供虫情/生长监测计算抽样样本量。

    参数:
        plot_id: 地块ID
    返回:
        {plot_id, batch_count, total_plant_count}
    """
    async with async_session_factory() as db:
        row = (await db.execute(
            select(
                func.count(PlantingBatch.id),
                func.coalesce(func.sum(PlantingBatch.plant_count), 0),
            ).where(PlantingBatch.plot_id == plot_id)
        )).first()
        batch_count = row[0] if row else 0
        total = int(row[1]) if row and row[1] else 0
        return {
            "plot_id": plot_id,
            "batch_count": batch_count,
            "total_plant_count": total,
        }


async def get_area_name(area_id: int) -> str:
    """返回产区名称，供 file_download 等插件按产区ID获取可读名称。

    参数:
        area_id: 产区ID
    返回:
        产区名称字符串，产区不存在时返回空字符串
    """
    async with async_session_factory() as db:
        name = (await db.execute(
            select(ProductionArea.name).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        return name or ""


async def get_farmer_ids_by_area(area_id: int) -> list[int]:
    """返回指定产区绑定的所有农户ID列表。

    参数:
        area_id: 产区ID
    返回:
        [farmer_id, ...] — 农户ID列表（无绑定时返回空列表）
    """
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(AreaFarmer.farmer_id).where(AreaFarmer.area_id == area_id)
        )).all()
    return [r[0] for r in rows]


async def get_area_ids_by_farmer(farmer_id: int) -> list[int]:
    """返回指定农户绑定的所有产区ID列表。

    参数:
        farmer_id: 农户ID
    返回:
        [area_id, ...] — 产区ID列表（无绑定时返回空列表）
    """
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(AreaFarmer.area_id).where(AreaFarmer.farmer_id == farmer_id)
        )).all()
    return [r[0] for r in rows]


async def list_area_brief() -> list:
    """返回产区简要列表（id/name/status），供下拉选择。

    返回:
        [{"id": int, "name": str, "status": int}, ...]
    """
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProductionArea.id, ProductionArea.name, ProductionArea.status)
            .order_by(ProductionArea.sort_order, ProductionArea.id)
        )).all()
    return [
        {"id": row[0], "name": row[1] or "", "status": row[2]}
        for row in rows
    ]


async def list_farmer_area_page(
    farmer_id: int, keywords: str = "", page: int = 1, limit: int = 10,
) -> dict:
    """查询农户绑定的产区分页数据。"""
    area_ids = await get_area_ids_by_farmer(farmer_id)
    async with async_session_factory() as db:
        query = select(ProductionArea).where(ProductionArea.id.in_(area_ids))
        if keywords:
            query = query.where(ProductionArea.name.contains(keywords))
        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            query.order_by(ProductionArea.sort_order, ProductionArea.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": [{
            "id": a.id, "name": a.name, "code": a.code,
            "province": a.province, "city": a.city, "district": a.district,
            "address": a.address, "longitude": a.longitude, "latitude": a.latitude,
            "boundary": a.boundary or "", "area_size": a.area_size,
            "crop_category": a.crop_category, "status": a.status,
            "description": a.description,
        } for a in rows],
    }


async def list_farmer_plots(farmer_id: int, area_id: int) -> dict | None:
    """查询农户绑定产区下的地块。"""
    area_ids = await get_area_ids_by_farmer(farmer_id)
    if area_id not in area_ids:
        return None
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        rows = (await db.execute(
            select(Plot).where(Plot.area_id == area_id)
            .order_by(Plot.sort_order, Plot.id.desc())
        )).scalars().all()
    if not area:
        return None
    return {
        "area_name": area.name,
        "list": [{
            "id": p.id, "area_id": p.area_id, "name": p.name, "code": p.code,
            "longitude": p.longitude, "latitude": p.latitude,
            "boundary": p.boundary or "", "area_size": p.area_size,
            "soil_type": p.soil_type, "status": p.status,
            "description": p.description,
        } for p in rows],
    }


async def list_farmer_batches(farmer_id: int, plot_id: int) -> dict | None:
    """查询农户可见地块下的种植批次。"""
    area_ids = await get_area_ids_by_farmer(farmer_id)
    async with async_session_factory() as db:
        plot = (await db.execute(
            select(Plot).where(Plot.id == plot_id, Plot.area_id.in_(area_ids))
        )).scalar_one_or_none()
        if not plot:
            return None
        rows = (await db.execute(
            select(PlantingBatch).where(PlantingBatch.plot_id == plot_id)
            .order_by(PlantingBatch.id.desc())
        )).scalars().all()

    return {
        "plot_name": plot.name,
        "list": [{
            "id": b.id, "plot_id": b.plot_id, "area_id": b.area_id,
            "batch_no": b.batch_no, "crop_name": b.crop_name,
            "crop_variety": b.crop_variety, "season": b.season,
            "plant_date": _fmt_date(b.plant_date),
            "expected_harvest_date": _fmt_date(b.expected_harvest_date),
            "actual_harvest_date": _fmt_date(b.actual_harvest_date),
            "plant_count": b.plant_count, "status": b.status,
            "description": b.description,
        } for b in rows],
    }


async def batch_belongs_to_area(batch_id: int, area_id: int) -> bool:
    """校验种植批次是否属于指定产区。"""
    async with async_session_factory() as db:
        row = (await db.execute(
            select(PlantingBatch.id).where(
                PlantingBatch.id == batch_id,
                PlantingBatch.area_id == area_id,
            )
        )).first()
    return row is not None
