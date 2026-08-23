"""地块管理 API（管理员端）— 地块 CRUD，支持 area_id 筛选"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, or_

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.log.active_log import active_log
from core.response import ok
from core.events import event_bus
from schemas.production_area import PlotCreate, PlotUpdate, AreaStatusUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/plot", tags=["地块管理"])


def _plot_to_dict(p, area_name: str = "") -> dict:
    """地块 ORM → dict"""
    return {
        "id": p.id, "area_id": p.area_id, "area_name": area_name,
        "farmer_id": p.farmer_id, "name": p.name, "code": p.code,
        "longitude": p.longitude, "latitude": p.latitude,
        "boundary": p.boundary or "", "area_size": p.area_size,
        "soil_type": p.soil_type, "status": p.status,
        "sort_order": p.sort_order, "description": p.description,
        "create_time": str(p.create_time) if p.create_time else None,
        "update_time": str(p.update_time) if p.update_time else None,
    }


@router.get("/list", dependencies=[Depends(require_permission("plot:list"))])
async def list_plots(
    keywords: str = Query("", description="搜索关键词"),
    area_id: int = Query(None, description="所属产区筛选"),
    status: int = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """地块列表 — 支持产区/状态筛选、搜索、分页（下拉全量加载走 /options 无分页端点）"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea, Plot
    async with async_session_factory() as db:
        q = select(Plot, ProductionArea.name).outerjoin(
            ProductionArea, ProductionArea.id == Plot.area_id
        )
        if keywords:
            q = q.where(or_(Plot.name.contains(keywords), Plot.code.contains(keywords)))
        if area_id is not None:
            q = q.where(Plot.area_id == area_id)
        if status is not None:
            q = q.where(Plot.status == status)

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            q.order_by(Plot.sort_order, Plot.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).all()
        return ok({
            "total": total, "page": page, "limit": limit,
            "list": [_plot_to_dict(p, aname or "") for p, aname in rows],
        })


@router.get("/options", dependencies=[Depends(require_permission("plot:list"))])
async def plot_options(
    area_id: int = Query(None, description="所属产区筛选"),
    _: None = Depends(check_admin),
):
    """地块下拉选项 — 返回全量启用地块的 [{id, name}]（无分页，可按产区筛选）"""
    from core.db.base import async_session_factory
    from core.db.production_area import Plot
    async with async_session_factory() as db:
        q = select(Plot.id, Plot.name).where(Plot.status == 1)
        if area_id is not None:
            q = q.where(Plot.area_id == area_id)
        rows = (await db.execute(
            q.order_by(Plot.sort_order, Plot.id.desc())
        )).all()
        return ok([{"id": rid, "name": name} for rid, name in rows])


@router.get("/{plot_id}", dependencies=[Depends(require_permission("plot:list"))])
async def get_plot(plot_id: int, _: None = Depends(check_admin)):
    """地块详情"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea, Plot
    async with async_session_factory() as db:
        row = (await db.execute(
            select(Plot, ProductionArea.name).outerjoin(
                ProductionArea, ProductionArea.id == Plot.area_id
            ).where(Plot.id == plot_id)
        )).first()
        if not row:
            raise HTTPException(status_code=404, detail="地块不存在")
        p, aname = row
        return ok(_plot_to_dict(p, aname or ""))


@router.post("", dependencies=[Depends(require_permission("plot:create"))])
async def create_plot(data: PlotCreate, request: Request, _: None = Depends(check_admin)):
    """新增地块（农户绑定改走产区↔农户关联表，地块不再冗余 farmer_id，写 0 退役）"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea, Plot
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == data.area_id)
        )).scalar_one_or_none()
        if not area:
            raise HTTPException(status_code=400, detail="所属产区不存在")

        plot = Plot(
            area_id=data.area_id, farmer_id=0,
            name=data.name, code=data.code,
            longitude=data.longitude, latitude=data.latitude,
            boundary=data.boundary, area_size=data.area_size,
            soil_type=data.soil_type, sort_order=data.sort_order,
            description=data.description, status=1,
        )
        db.add(plot)
        await db.flush()
        await event_bus.publish_durable("plot.created", {
            "plot_id": plot.id, "area_id": plot.area_id, "plot_name": plot.name,
        }, db)
        await db.commit()
        await db.refresh(plot)
        await active_log(f"新增地块: {plot.name}", "plot", rel_id=plot.id,
                         request=request, db=db)
        return ok({"id": plot.id}, msg="地块已创建")


@router.put("/{plot_id}", dependencies=[Depends(require_permission("plot:update"))])
async def update_plot(plot_id: int, data: PlotUpdate, request: Request, _: None = Depends(check_admin)):
    """编辑地块（不改所属产区）"""
    from core.db.base import async_session_factory
    from core.db.production_area import Plot
    async with async_session_factory() as db:
        plot = (await db.execute(
            select(Plot).where(Plot.id == plot_id)
        )).scalar_one_or_none()
        if not plot:
            raise HTTPException(status_code=404, detail="地块不存在")
        for field, value in data.model_dump().items():
            setattr(plot, field, value)
        await db.commit()
        await active_log(f"编辑地块: {plot.name}", "plot", rel_id=plot_id,
                         request=request, db=db)
        return ok(msg="地块已更新")


@router.put("/{plot_id}/status", dependencies=[Depends(require_permission("plot:update"))])
async def update_plot_status(plot_id: int, data: AreaStatusUpdate, request: Request, _: None = Depends(check_admin)):
    """地块状态切换（软停用优先）"""
    from core.db.base import async_session_factory
    from core.db.production_area import Plot
    async with async_session_factory() as db:
        plot = (await db.execute(
            select(Plot).where(Plot.id == plot_id)
        )).scalar_one_or_none()
        if not plot:
            raise HTTPException(status_code=404, detail="地块不存在")
        plot.status = data.status
        await db.commit()
        text = "启用" if data.status == 1 else "停用"
        await active_log(f"{text}地块: {plot.name}", "plot", rel_id=plot_id,
                         request=request, db=db)
        return ok(msg=f"地块已{text}")


@router.delete("/{plot_id}", dependencies=[Depends(require_permission("plot:delete"))])
async def delete_plot(plot_id: int, request: Request, _: None = Depends(check_admin)):
    """删除地块（仅当无下级批次时允许，保护未来插件外键）"""
    from core.db.base import async_session_factory
    from core.db.production_area import Plot, PlantingBatch
    async with async_session_factory() as db:
        plot = (await db.execute(
            select(Plot).where(Plot.id == plot_id)
        )).scalar_one_or_none()
        if not plot:
            raise HTTPException(status_code=404, detail="地块不存在")
        batch_count = (await db.execute(
            select(func.count()).select_from(PlantingBatch)
            .where(PlantingBatch.plot_id == plot_id)
        )).scalar() or 0
        if batch_count > 0:
            raise HTTPException(status_code=400, detail="该地块下仍有种植批次，请先删除批次或改为停用")
        await db.delete(plot)
        await db.commit()
        await active_log(f"删除地块: {plot.name}", "plot", rel_id=plot_id,
                         request=request, db=db)
        return ok(msg="地块已删除")
