# -*- coding: utf-8 -*-
"""
农业知识库插件 — 管理员端路由（前缀 /api/admin/v1/knowledge）

- 路由级 Depends(check_admin) 登录校验 + 接口级 require_permission 细粒度权限
- 业务逻辑全部委托 services 层，handler 保持简洁
- 写操作记录 active_log 操作日志
- 典型图片复用现有 POST /api/admin/v1/upload/image 接口，本插件不新增上传端点
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok
from plugins.addon.knowledge.schemas import (
    CategoryCreate,
    CategoryUpdate,
    CorrectionHandle,
    EntryBatchCreate,
    EntryCreate,
    EntryUpdate,
)
from plugins.addon.knowledge.services.category_service import CategoryService
from plugins.addon.knowledge.services.correction_service import CorrectionService
from plugins.addon.knowledge.services.entry_service import EntryService

# router 级依赖 check_admin：登录校验先于各接口的 require_permission 权限校验
router = APIRouter(
    prefix="/api/admin/v1/knowledge",
    tags=["农业知识库插件"],
    dependencies=[Depends(check_admin)],
)

PLUGIN_NAME = "knowledge"
_config_manager = ConfigManager()


async def _resolve_limit(db, limit: int) -> int:
    """解析列表每页条数：显式传入（>0）则原样返回，否则取插件配置
    knowledge.list_page_size 作为默认值；越界钳到 1~100，非法值回退 10。
    """
    if limit:
        return limit
    cfg = await _config_manager.get_plugin_config(PLUGIN_NAME, db)
    try:
        size = int(cfg.get("list_page_size") or 10)
    except (TypeError, ValueError):
        size = 10
    return min(max(size, 1), 100)


# ---------------------------------------------------------------------------
# 分类管理（4 个端点）
# ---------------------------------------------------------------------------
@router.get("/categories", dependencies=[Depends(require_permission("knowledge:list"))])
async def list_categories():
    """分类树 — 大类 > 子类两级（含停用分类，供管理端全量维护）"""
    async with async_session_factory() as db:
        return ok({"list": await CategoryService.list_tree(db)})


@router.post("/categories", dependencies=[Depends(require_permission("knowledge:category"))])
async def create_category(data: CategoryCreate, request: Request):
    """新建分类 — parent_id=0 为大类，否则为指定大类下的子类"""
    async with async_session_factory() as db:
        if data.parent_id:
            parent = await CategoryService.get_category(db, data.parent_id)
            if not parent or parent.parent_id != 0:
                raise HTTPException(status_code=400, detail="父分类不存在或非大类，仅支持二级分类")
        category_id = await CategoryService.create_category(
            db, data.name, data.parent_id, data.sort_order
        )
        await active_log(f"新增知识分类: {data.name}", "knowledge_category_create", rel_id=category_id, request=request, db=db)
    return ok({"id": category_id}, msg="创建成功")


@router.put("/categories/{category_id}", dependencies=[Depends(require_permission("knowledge:category"))])
async def update_category(category_id: int, data: CategoryUpdate, request: Request):
    """编辑分类（名称/排序/启停）"""
    async with async_session_factory() as db:
        success = await CategoryService.update_category(
            db, category_id, data.name, data.sort_order, data.status
        )
        if not success:
            raise HTTPException(status_code=404, detail="分类不存在")
        await active_log(f"编辑知识分类: {data.name}", "knowledge_category_update", rel_id=category_id, request=request, db=db)
    return ok(msg="更新成功")


@router.delete("/categories/{category_id}", dependencies=[Depends(require_permission("knowledge:category"))])
async def delete_category(category_id: int, request: Request):
    """删除分类 — 存在子类或被条目引用时拒绝"""
    async with async_session_factory() as db:
        err = await CategoryService.delete_category(db, category_id)
        if err == "not_found":
            raise HTTPException(status_code=404, detail="分类不存在")
        if err == "has_children":
            raise HTTPException(status_code=400, detail="该分类下存在子类，请先删除子类")
        if err == "in_use":
            raise HTTPException(status_code=400, detail="该分类已被知识条目引用，无法删除")
        await active_log(f"删除知识分类: {category_id}", "knowledge_category_delete", rel_id=category_id, request=request, db=db)
    return ok(msg="删除成功")


# ---------------------------------------------------------------------------
# 知识条目管理（6 个端点）
# ---------------------------------------------------------------------------
@router.get("/entries", dependencies=[Depends(require_permission("knowledge:list"))])
async def list_entries(
    keyword: str = Query("", description="标题/摘要关键词"),
    category_id: int = Query(0, ge=0, description="分类ID，0=全部"),
    crop: str = Query("", description="适用作物关键词"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(0, ge=0, le=100, description="每页条数，0=用插件配置默认值"),
):
    """知识条目列表 — 支持关键词/分类/作物过滤与分页"""
    async with async_session_factory() as db:
        limit = await _resolve_limit(db, limit)
        result = await EntryService.list_entries(
            db, keyword=keyword, category_id=category_id, crop=crop,
            page=page, limit=limit,
        )
    return ok(result)


@router.post("/entries/batch", dependencies=[Depends(require_permission("knowledge:create"))])
async def batch_create_entries(data: EntryBatchCreate, request: Request):
    """批量新建知识条目（共享分类/作物 + 多行标题）

    路由顺序关键：本端点必须定义在 /entries/{entry_id} 之前，否则 Starlette
    会把 batch 当作 {entry_id} 字符串匹配，进入 int 校验返回 422。
    """
    async with async_session_factory() as db:
        if not await CategoryService.get_category(db, data.category_id):
            raise HTTPException(status_code=400, detail="所属分类不存在")
        ids = await EntryService.batch_create(
            db, data.category_id, data.titles, data.crop, request.state.user_id
        )
        await active_log(f"批量新增知识条目: {len(ids)}条", "knowledge_batch_create", request=request, db=db)
    return ok({"count": len(ids), "ids": ids}, msg=f"成功新增 {len(ids)} 条")


@router.get("/entries/{entry_id}", dependencies=[Depends(require_permission("knowledge:list"))])
async def get_entry(entry_id: int):
    """知识条目详情（含 images/cause/solution，供编辑弹窗回显）"""
    async with async_session_factory() as db:
        row = await EntryService.get_entry(db, entry_id)
        if not row:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        return ok(EntryService.to_detail(row))


@router.post("/entries", dependencies=[Depends(require_permission("knowledge:create"))])
async def create_entry(data: EntryCreate, request: Request):
    """新建知识条目"""
    async with async_session_factory() as db:
        if not await CategoryService.get_category(db, data.category_id):
            raise HTTPException(status_code=400, detail="所属分类不存在")
        entry_id = await EntryService.create_entry(db, data.model_dump(), request.state.user_id)
        await active_log(f"新增知识条目: {data.title}", "knowledge_create", rel_id=entry_id, request=request, db=db)
    return ok({"id": entry_id}, msg="创建成功")


@router.put("/entries/{entry_id}", dependencies=[Depends(require_permission("knowledge:update"))])
async def update_entry(entry_id: int, data: EntryUpdate, request: Request):
    """编辑知识条目"""
    async with async_session_factory() as db:
        if not await CategoryService.get_category(db, data.category_id):
            raise HTTPException(status_code=400, detail="所属分类不存在")
        success = await EntryService.update_entry(db, entry_id, data.model_dump(), request.state.user_id)
        if not success:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        await active_log(f"编辑知识条目: {data.title}", "knowledge_update", rel_id=entry_id, request=request, db=db)
    return ok(msg="更新成功")


@router.delete("/entries/{entry_id}", dependencies=[Depends(require_permission("knowledge:delete"))])
async def delete_entry(entry_id: int, request: Request):
    """删除知识条目"""
    async with async_session_factory() as db:
        success = await EntryService.delete_entry(db, entry_id)
        if not success:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        await active_log(f"删除知识条目: {entry_id}", "knowledge_delete", rel_id=entry_id, request=request, db=db)
    return ok(msg="删除成功")


# ---------------------------------------------------------------------------
# 勘误审核（2 个端点）
# ---------------------------------------------------------------------------
@router.get("/corrections", dependencies=[Depends(require_permission("knowledge:correction"))])
async def list_corrections(
    status: int = Query(-1, ge=-1, le=2, description="状态 -1全部 0待处理 1已采纳 2已驳回"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
):
    """勘误列表 — 支持按状态过滤与分页，附带关联条目标题"""
    async with async_session_factory() as db:
        return ok(await CorrectionService.list_corrections(
            db, status=None if status == -1 else status, page=page, limit=limit
        ))


@router.put("/corrections/{correction_id}/handle", dependencies=[Depends(require_permission("knowledge:correction"))])
async def handle_correction(correction_id: int, data: CorrectionHandle, request: Request):
    """处理勘误 — 采纳(1)/驳回(2) + 处理备注"""
    async with async_session_factory() as db:
        err = await CorrectionService.handle(
            db, correction_id, data.status, data.admin_note, request.state.user_id
        )
        if err == "not_found":
            raise HTTPException(status_code=404, detail="勘误不存在")
        if err == "handled":
            raise HTTPException(status_code=400, detail="该勘误已处理，请勿重复操作")
        status_text = "采纳" if data.status == 1 else "驳回"
        await active_log(f"处理勘误({status_text}): {correction_id}", "knowledge_correction_handle",
                         rel_id=correction_id, request=request, db=db)
    return ok(msg="处理成功")
