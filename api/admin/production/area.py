"""产区管理 API（管理员端）— 产区 CRUD + 状态切换 + 三级树"""
import hashlib
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, func, or_, delete, update

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.config_service import get_config as svc_get_config
from core.response import ok
from core.log.active_log import active_log
from core.events import event_bus
from core.production_area_service import list_area_tree
from schemas.production_area import AreaCreate, AreaUpdate, AreaStatusUpdate, AreaFarmersBind

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/production-area", tags=["产区管理"])

# 静态地图图片进程内缓存：{ "area_id:boundary_hash": png_bytes }，避免重复消耗高德配额
_static_map_cache: dict = {}
# 静态地图尺寸与样式（蓝色填充多边形，与前端地图色一致）
_STATIC_MAP_SIZE = "400*180"
_STATIC_MAP_PATH_STYLE = "3,0x0052D9,1,0x0052D9,0.35"


def _boundary_to_location(boundary: str) -> str:
    """GeoJSON Polygon 字符串 → staticmap location 串 "lng,lat;lng,lat;..."（非法/空返回 ""）

    顶点过多时等间隔降采样至 ≤ 40 个，避免 staticmap URL 超长被高德拒绝。
    """
    try:
        geo = json.loads(boundary)
    except (ValueError, TypeError):
        return ""
    if not isinstance(geo, dict) or geo.get("type") != "Polygon":
        return ""
    ring = (geo.get("coordinates") or [None])[0]
    if not ring or len(ring) < 3:
        return ""
    max_pts = 40
    if len(ring) > max_pts:
        step = len(ring) / max_pts
        sampled = [ring[int(i * step)] for i in range(max_pts)]
        ring = sampled
    parts = []
    for pt in ring:
        try:
            parts.append(f"{float(pt[0]):.6f},{float(pt[1]):.6f}")
        except (TypeError, ValueError, IndexError):
            continue
    return ";".join(parts) if len(parts) >= 3 else ""


def _area_to_dict(a, farmer_count: int = 0) -> dict:
    """产区 ORM → dict（farmer_count=绑定农户数，绑定来源为 hy_area_farmer 关联表）"""
    return {
        "id": a.id, "farmer_count": farmer_count,
        "name": a.name, "code": a.code,
        "province": a.province, "city": a.city, "district": a.district,
        "address": a.address, "longitude": a.longitude, "latitude": a.latitude,
        "boundary": a.boundary or "", "area_size": a.area_size,
        "crop_category": a.crop_category, "status": a.status,
        "sort_order": a.sort_order, "description": a.description,
        "create_time": str(a.create_time) if a.create_time else None,
        "update_time": str(a.update_time) if a.update_time else None,
    }


@router.get("/tree", dependencies=[Depends(require_permission("area:list"))])
async def get_tree(farmer_id: int = Query(None, description="按农户过滤"),
                   _: None = Depends(check_admin)):
    """三级树数据源（产区/地块/批次）"""
    tree = await list_area_tree(farmer_id)
    return ok(tree)


@router.get("/list", dependencies=[Depends(require_permission("area:list"))])
async def list_areas(
    keywords: str = Query("", description="搜索关键词"),
    farmer_id: int = Query(None, description="归属农户筛选"),
    status: int = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """产区列表 — 支持搜索、绑定农户/状态筛选、分页（下拉全量加载走 /options 无分页端点）"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea, AreaFarmer
    async with async_session_factory() as db:
        q = select(ProductionArea)
        if keywords:
            q = q.where(or_(
                ProductionArea.name.contains(keywords),
                ProductionArea.code.contains(keywords),
            ))
        if farmer_id is not None:
            # 按绑定农户筛选：产区需在关联表中含该农户
            q = q.where(ProductionArea.id.in_(
                select(AreaFarmer.area_id).where(AreaFarmer.farmer_id == farmer_id)
            ))
        if status is not None:
            q = q.where(ProductionArea.status == status)

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            q.order_by(ProductionArea.sort_order, ProductionArea.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()
        # 统计每个产区的绑定农户数（一次分组查询）
        ids = [a.id for a in rows]
        count_map = {}
        if ids:
            crows = (await db.execute(
                select(AreaFarmer.area_id, func.count())
                .where(AreaFarmer.area_id.in_(ids))
                .group_by(AreaFarmer.area_id)
            )).all()
            count_map = {aid: cnt for aid, cnt in crows}
        return ok({
            "total": total, "page": page, "limit": limit,
            "list": [_area_to_dict(a, count_map.get(a.id, 0)) for a in rows],
        })


@router.get("/options", dependencies=[Depends(require_permission("area:list"))])
async def area_options(_: None = Depends(check_admin)):
    """产区下拉选项 — 返回全量启用产区的 [{id, name}]（无分页，供下拉框全量加载，避免分页 limit 上限）"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProductionArea.id, ProductionArea.name)
            .where(ProductionArea.status == 1)
            .order_by(ProductionArea.sort_order, ProductionArea.id.desc())
        )).all()
        return ok([{"id": rid, "name": name} for rid, name in rows])


@router.get("/bindings", dependencies=[Depends(require_permission("area:list"))])
async def list_bindings(_: None = Depends(check_admin)):
    """全部产区的绑定农户映射 {area_id: [farmer_id,...]}，供农户绑定页初始化多选"""
    from core.db.base import async_session_factory
    from core.db.production_area import AreaFarmer
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(AreaFarmer.area_id, AreaFarmer.farmer_id)
        )).all()
    mapping: dict = {}
    for a_id, f_id in rows:
        mapping.setdefault(a_id, []).append(f_id)
    return ok(mapping)


@router.get("/{area_id}", dependencies=[Depends(require_permission("area:list"))])
async def get_area(area_id: int, _: None = Depends(check_admin)):
    """产区详情"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea, AreaFarmer
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if not area:
            raise HTTPException(status_code=404, detail="产区不存在")
        count = (await db.execute(
            select(func.count()).select_from(AreaFarmer)
            .where(AreaFarmer.area_id == area_id)
        )).scalar() or 0
        return ok(_area_to_dict(area, count))


@router.get("/{area_id}/static-map", dependencies=[Depends(require_permission("area:list"))])
async def get_area_static_map(area_id: int, _: None = Depends(check_admin)):
    """产区静态地图缩略图代理（绑定页卡片用）

    代理高德 staticmap API：避免前端暴露 Web 服务 Key，也规避 img 无法带 JWT。
    优先用各地块边界，无地块则回退产区边界；未配置 Key / 无任何边界 / 异常 → 204。
    """
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea, Plot
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if not area:
            return Response(status_code=204)
        plot_boundaries = (await db.execute(
            select(Plot.boundary).where(Plot.area_id == area_id)
        )).scalars().all()
    web_key = await svc_get_config("amap_web_service_key") or ""

    if not web_key:
        return Response(status_code=204)

    # 优先地块边界，均无则回退产区边界
    boundaries = [b for b in plot_boundaries if b]
    if not boundaries and area.boundary:
        boundaries = [area.boundary]
    locations = [loc for loc in (_boundary_to_location(b) for b in boundaries) if loc]
    if not locations:
        return Response(status_code=204)

    # 缓存键 = area_id + 全部边界内容 hash（边界变化自动失效）
    digest = hashlib.md5("|".join(locations).encode("utf-8")).hexdigest()
    cache_key = f"{area_id}:{digest}"
    if cache_key in _static_map_cache:
        return Response(content=_static_map_cache[cache_key], media_type="image/png")

    # 拼接 staticmap URL：每个多边形一段 paths，以 | 分隔
    paths = "|".join(f"{_STATIC_MAP_PATH_STYLE}:{loc}" for loc in locations)
    params = {"size": _STATIC_MAP_SIZE, "paths": paths, "key": web_key}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get("https://restapi.amap.com/v3/staticmap", params=params)
        # 高德正常返回图片字节；出错时返回 JSON（content-type 非 image）
        ctype = resp.headers.get("content-type", "")
        if resp.status_code != 200 or not ctype.startswith("image"):
            logger.warning("[静态地图] area=%s 高德返回异常 status=%s ctype=%s", area_id, resp.status_code, ctype)
            return Response(status_code=204)
        img = resp.content
    except Exception as e:  # 降级为占位，不阻断卡片渲染
        logger.warning("[静态地图] area=%s 拉取失败：%s", area_id, e)
        return Response(status_code=204)

    _static_map_cache[cache_key] = img
    return Response(content=img, media_type="image/png")


@router.get("/{area_id}/geo", dependencies=[Depends(require_permission("area:list"))])
async def get_area_geo(area_id: int, _: None = Depends(check_admin)):
    """产区地理数据（农户绑定页只读缩略地图用）

    返回渲染只读缩略图所需的最小字段：产区参考边界 + 中心点 + 全部地块名称与边界；
    无边界/无地块时对应字段为空，前端显示占位。地图 JS API Key 已配置即可用。
    """
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea, Plot
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if not area:
            raise HTTPException(status_code=404, detail="产区不存在")
        plot_rows = (await db.execute(
            select(Plot.id, Plot.name, Plot.boundary).where(Plot.area_id == area_id)
        )).all()
    # 仅保留含有效边界的地块（缩略图只画有边界的多边形）
    plots = [
        {"id": plot_id, "name": name, "boundary": boundary}
        for plot_id, name, boundary in plot_rows if boundary
    ]
    return ok({
        "boundary": area.boundary or "",
        "longitude": area.longitude, "latitude": area.latitude,
        "plots": plots,
    })


@router.post("", dependencies=[Depends(require_permission("area:create"))])
async def create_area(data: AreaCreate, request: Request, _: None = Depends(check_admin)):
    """新增产区（农户绑定统一在农户绑定页多对多管理）"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea
    async with async_session_factory() as db:
        area = ProductionArea(
            name=data.name, code=data.code,
            province=data.province, city=data.city, district=data.district,
            address=data.address, longitude=data.longitude, latitude=data.latitude,
            boundary=data.boundary, area_size=data.area_size,
            crop_category=data.crop_category, sort_order=data.sort_order,
            description=data.description, status=1,
        )
        db.add(area)
        await db.commit()
        await db.refresh(area)
        await active_log(f"新增产区: {area.name}", "area", rel_id=area.id,
                         request=request, db=db)
        return ok({"id": area.id}, msg="产区已创建")


@router.put("/{area_id}", dependencies=[Depends(require_permission("area:update"))])
async def update_area(area_id: int, data: AreaUpdate, request: Request, _: None = Depends(check_admin)):
    """编辑产区（农户绑定统一在农户绑定页多对多管理，不在此处处理）"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if not area:
            raise HTTPException(status_code=404, detail="产区不存在")
        for field, value in data.model_dump().items():
            setattr(area, field, value)
        await db.commit()
        await active_log(f"编辑产区: {area.name}", "area", rel_id=area_id,
                         request=request, db=db)
        return ok(msg="产区已更新")


@router.get("/{area_id}/farmers", dependencies=[Depends(require_permission("area:list"))])
async def list_area_farmers(area_id: int, _: None = Depends(check_admin)):
    """指定产区当前绑定的农户列表 [{id, name}]"""
    from core.db.base import async_session_factory
    from core.db.production_area import AreaFarmer
    from core.db.farmer import Farmer
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Farmer.id, Farmer.username).join(
                AreaFarmer, AreaFarmer.farmer_id == Farmer.id
            ).where(AreaFarmer.area_id == area_id)
        )).all()
        return ok([{"id": fid, "name": name} for fid, name in rows])


@router.put("/{area_id}/farmers", dependencies=[Depends(require_permission("area:update"))])
async def bind_area_farmers(area_id: int, data: AreaFarmersBind, request: Request, _: None = Depends(check_admin)):
    """整集替换产区绑定农户（传入应绑定的全部农户ID，空数组=解绑全部）"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea, AreaFarmer
    from core.db.farmer import Farmer
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if not area:
            raise HTTPException(status_code=404, detail="产区不存在")
        # 去重并校验农户存在
        farmer_ids = sorted(set(fid for fid in data.farmer_ids if fid > 0))
        if farmer_ids:
            valid = (await db.execute(
                select(Farmer.id).where(Farmer.id.in_(farmer_ids))
            )).scalars().all()
            missing = set(farmer_ids) - set(valid)
            if missing:
                raise HTTPException(status_code=400, detail=f"农户不存在: {sorted(missing)}")
        # 查旧绑定集合，用于 commit 后只对新绑定农户发通知
        old_ids = set((await db.execute(
            select(AreaFarmer.farmer_id).where(AreaFarmer.area_id == area_id)
        )).scalars().all())
        area_name = area.name
        # 整集替换：先清空旧绑定，再写入新集合
        await db.execute(delete(AreaFarmer).where(AreaFarmer.area_id == area_id))
        for fid in farmer_ids:
            db.add(AreaFarmer(area_id=area_id, farmer_id=fid))
        new_bound_ids = sorted(set(farmer_ids) - old_ids)
        if new_bound_ids:
            await event_bus.publish_durable("area.farmer_bound", {
                "area_id": area_id, "area_name": area_name,
                "farmer_ids": new_bound_ids, "action": "bind",
            }, db)
        await db.commit()
        text = f"绑定 {len(farmer_ids)} 个农户" if farmer_ids else "解绑全部农户"
        await active_log(f"产区{text}: {area_name}", "area", rel_id=area_id,
                         request=request, db=db)
        return ok(msg="绑定已更新" if farmer_ids else "已解绑全部")


@router.put("/{area_id}/status", dependencies=[Depends(require_permission("area:status"))])
async def update_area_status(area_id: int, data: AreaStatusUpdate, request: Request, _: None = Depends(check_admin)):
    """产区状态切换（软停用优先）"""
    from core.db.base import async_session_factory
    from core.db.production_area import ProductionArea
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if not area:
            raise HTTPException(status_code=404, detail="产区不存在")
        area.status = data.status
        await db.commit()
        text = "启用" if data.status == 1 else "停用"
        await active_log(f"{text}产区: {area.name}", "area", rel_id=area_id,
                         request=request, db=db)
        return ok(msg=f"产区已{text}")


@router.delete("/{area_id}", dependencies=[Depends(require_permission("area:delete"))])
async def delete_area(area_id: int, request: Request, _: None = Depends(check_admin)):
    """删除产区（仅当无下级地块时允许，保护未来插件外键）"""
    from core.db.base import async_session_factory
    from core.db.hardware_device import HardwareDevice
    from core.db.production_area import ProductionArea, Plot
    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if not area:
            raise HTTPException(status_code=404, detail="产区不存在")
        plot_count = (await db.execute(
            select(func.count()).select_from(Plot).where(Plot.area_id == area_id)
        )).scalar() or 0
        if plot_count > 0:
            raise HTTPException(status_code=400, detail="该产区下仍有地块，请先删除地块或改为停用")
        await db.execute(update(HardwareDevice).where(
            HardwareDevice.area_id == area_id
        ).values(area_id=None, plot_id=None, marker_ratio=None))
        await db.delete(area)
        await db.commit()
        await active_log(f"删除产区: {area.name}", "area", rel_id=area_id,
                         request=request, db=db)
        return ok(msg="产区已删除")
