"""种植批次管理 API（管理员端）— 批次 CRUD，支持 area_id/plot_id 筛选"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, or_

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.log.active_log import active_log
from core.response import ok
from core.events import event_bus
from schemas.production_area import BatchCreate, BatchUpdate, BatchStatusUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/planting-batch", tags=["种植批次"])


def _parse_date(value: str):
    """解析 YYYY-MM-DD 字符串为 datetime，空串或非法返回 None"""
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _fmt_date(dt) -> str:
    """datetime → YYYY-MM-DD 字符串（None 返回空串）"""
    return dt.strftime("%Y-%m-%d") if dt else ""


def _batch_to_dict(b) -> dict:
    """批次 ORM → dict"""
    return {
        "id": b.id, "plot_id": b.plot_id, "area_id": b.area_id,
        "farmer_id": b.farmer_id, "batch_no": b.batch_no,
        "crop_name": b.crop_name, "crop_variety": b.crop_variety,
        "season": b.season, "plant_date": _fmt_date(b.plant_date),
        "expected_harvest_date": _fmt_date(b.expected_harvest_date),
        "actual_harvest_date": _fmt_date(b.actual_harvest_date),
        "plant_count": b.plant_count, "status": b.status,
        "description": b.description,
        "create_time": str(b.create_time) if b.create_time else None,
        "update_time": str(b.update_time) if b.update_time else None,
    }


@router.get("/list", dependencies=[Depends(require_permission("batch:list"))])
async def list_batches(
    keywords: str = Query("", description="搜索关键词"),
    area_id: int = Query(None, description="所属产区筛选"),
    plot_id: int = Query(None, description="所属地块筛选"),
    status: int = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """批次列表 — 支持产区/地块/状态筛选、搜索、分页"""
    from core.db.base import async_session_factory
    from core.db.production_area import Plot, PlantingBatch
    async with async_session_factory() as db:
        q = select(PlantingBatch, Plot.name).outerjoin(
            Plot, Plot.id == PlantingBatch.plot_id
        )
        if keywords:
            q = q.where(or_(
                PlantingBatch.batch_no.contains(keywords),
                PlantingBatch.crop_name.contains(keywords),
            ))
        if area_id is not None:
            q = q.where(PlantingBatch.area_id == area_id)
        if plot_id is not None:
            q = q.where(PlantingBatch.plot_id == plot_id)
        if status is not None:
            q = q.where(PlantingBatch.status == status)

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            q.order_by(PlantingBatch.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).all()
        result_list = []
        for b, plot_name in rows:
            item = _batch_to_dict(b)
            item["plot_name"] = plot_name or ""
            result_list.append(item)
        return ok({"total": total, "page": page, "limit": limit, "list": result_list})


@router.get("/{batch_id}", dependencies=[Depends(require_permission("batch:list"))])
async def get_batch(batch_id: int, _: None = Depends(check_admin)):
    """批次详情"""
    from core.db.base import async_session_factory
    from core.db.production_area import PlantingBatch
    async with async_session_factory() as db:
        b = (await db.execute(
            select(PlantingBatch).where(PlantingBatch.id == batch_id)
        )).scalar_one_or_none()
        if not b:
            raise HTTPException(status_code=404, detail="批次不存在")
        return ok(_batch_to_dict(b))


@router.post("", dependencies=[Depends(require_permission("batch:create"))])
async def create_batch(data: BatchCreate, request: Request, _: None = Depends(check_admin)):
    """新增批次（area_id/farmer_id 从所属地块继承）"""
    from core.db.base import async_session_factory
    from core.db.production_area import Plot, PlantingBatch
    async with async_session_factory() as db:
        plot = (await db.execute(
            select(Plot).where(Plot.id == data.plot_id)
        )).scalar_one_or_none()
        if not plot:
            raise HTTPException(status_code=400, detail="所属地块不存在")

        batch = PlantingBatch(
            plot_id=data.plot_id, area_id=plot.area_id, farmer_id=plot.farmer_id,
            batch_no=data.batch_no, crop_name=data.crop_name,
            crop_variety=data.crop_variety, season=data.season,
            plant_date=_parse_date(data.plant_date),
            expected_harvest_date=_parse_date(data.expected_harvest_date),
            actual_harvest_date=_parse_date(data.actual_harvest_date),
            plant_count=data.plant_count, description=data.description, status=1,
        )
        db.add(batch)
        await db.commit()
        await db.refresh(batch)
        await active_log(f"新增种植批次: {batch.batch_no or batch.crop_name}", "batch",
                         rel_id=batch.id, request=request, db=db)
        return ok({"id": batch.id}, msg="批次已创建")


@router.put("/{batch_id}", dependencies=[Depends(require_permission("batch:update"))])
async def update_batch(batch_id: int, data: BatchUpdate, request: Request, _: None = Depends(check_admin)):
    """编辑批次（不改所属地块）"""
    from core.db.base import async_session_factory
    from core.db.production_area import PlantingBatch
    async with async_session_factory() as db:
        batch = (await db.execute(
            select(PlantingBatch).where(PlantingBatch.id == batch_id)
        )).scalar_one_or_none()
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        batch.batch_no = data.batch_no
        batch.crop_name = data.crop_name
        batch.crop_variety = data.crop_variety
        batch.season = data.season
        batch.plant_date = _parse_date(data.plant_date)
        batch.expected_harvest_date = _parse_date(data.expected_harvest_date)
        batch.actual_harvest_date = _parse_date(data.actual_harvest_date)
        batch.plant_count = data.plant_count
        batch.description = data.description
        await db.commit()
        await active_log(f"编辑种植批次: {batch.batch_no or batch.crop_name}", "batch",
                         rel_id=batch_id, request=request, db=db)
        return ok(msg="批次已更新")


@router.put("/{batch_id}/status", dependencies=[Depends(require_permission("batch:status"))])
async def update_batch_status(batch_id: int, data: BatchStatusUpdate, request: Request, _: None = Depends(check_admin)):
    """批次状态切换"""
    from core.db.base import async_session_factory
    from core.db.production_area import PlantingBatch
    async with async_session_factory() as db:
        batch = (await db.execute(
            select(PlantingBatch).where(PlantingBatch.id == batch_id)
        )).scalar_one_or_none()
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        # 提前取出 commit 后仍需使用的字段，避免 ORM 过期
        old_status = batch.status
        farmer_id = batch.farmer_id
        batch_name = batch.batch_no or batch.crop_name
        batch.status = data.status
        await event_bus.publish_durable("batch.status_changed", {
            "batch_id": batch_id, "farmer_id": farmer_id,
            "old_status": str(old_status), "new_status": str(data.status),
            "batch_name": batch_name,
        }, db)
        await db.commit()
        await active_log(f"切换批次状态: {batch_name}", "batch",
                         rel_id=batch_id, request=request, db=db)
        return ok(msg="批次状态已更新")


@router.delete("/{batch_id}", dependencies=[Depends(require_permission("batch:delete"))])
async def delete_batch(batch_id: int, request: Request, _: None = Depends(check_admin)):
    """删除批次"""
    from core.db.base import async_session_factory
    from core.db.production_area import PlantingBatch
    async with async_session_factory() as db:
        batch = (await db.execute(
            select(PlantingBatch).where(PlantingBatch.id == batch_id)
        )).scalar_one_or_none()
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        await db.delete(batch)
        await db.commit()
        await active_log(f"删除种植批次: {batch.batch_no or batch.crop_name}", "batch",
                         rel_id=batch_id, request=request, db=db)
        return ok(msg="批次已删除")
