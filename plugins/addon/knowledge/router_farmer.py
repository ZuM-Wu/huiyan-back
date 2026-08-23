# -*- coding: utf-8 -*-
"""
农业知识库插件 — 农户端路由（前缀 /api/v1/knowledge）

- 路由级 Depends(check_farmer)：农户登录校验，farmer_id 取 request.state.user_id
- 仅暴露启用分类与知识条目
- 详情浏览计数 SQL 端原子自增
- 提交勘误受插件配置 knowledge.allow_correction 开关控制（关闭时 403），记 active_log
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_farmer
from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok
from plugins.addon.knowledge.schemas import CorrectionSubmit
from plugins.addon.knowledge.services.category_service import CategoryService
from plugins.addon.knowledge.services.correction_service import CorrectionService
from plugins.addon.knowledge.services.entry_service import EntryService

PLUGIN_NAME = "knowledge"

farmer_router = APIRouter(
    prefix="/api/v1/knowledge",
    tags=["农业知识库插件（农户端）"],
    dependencies=[Depends(check_farmer)],
)

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


@farmer_router.get("/categories")
async def farmer_list_categories():
    """农户端 — 启用分类树（status=1）"""
    async with async_session_factory() as db:
        return ok({"list": await CategoryService.list_tree(db, only_enabled=True)})


@farmer_router.get("/entries")
async def farmer_list_entries(
    keyword: str = Query("", description="标题/摘要关键词"),
    category_id: int = Query(0, ge=0, description="分类ID，0=全部"),
    crop: str = Query("", description="适用作物关键词"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(0, ge=0, le=100, description="每页条数，0=用插件配置默认值"),
):
    """农户端 — 知识条目分页列表"""
    async with async_session_factory() as db:
        limit = await _resolve_limit(db, limit)
        return ok(await EntryService.list_entries(
            db, keyword=keyword, category_id=category_id, crop=crop,
            page=page, limit=limit,
        ))


@farmer_router.get("/entries/{entry_id}")
async def farmer_get_entry(entry_id: int):
    """农户端 — 条目详情（含 images/cause/solution），浏览计数原子自增"""
    async with async_session_factory() as db:
        row = await EntryService.get_entry(db, entry_id)
        if not row:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        detail = EntryService.to_detail(row)
        names = await CategoryService.name_map(db)
        # 浏览计数 SQL 端原子自增（独立提交，不阻塞详情返回）
        await EntryService.increment_view(db, entry_id)
    detail["category_name"] = names.get(detail["category_id"], "")
    return ok(detail)


@farmer_router.post("/entries/{entry_id}/correction")
async def farmer_submit_correction(entry_id: int, data: CorrectionSubmit, request: Request):
    """农户端 — 提交勘误建议（受配置 knowledge.allow_correction 开关控制）"""
    farmer_id = request.state.user_id
    async with async_session_factory() as db:
        cfg = await _config_manager.get_plugin_config(PLUGIN_NAME, db)
        if str(cfg.get("allow_correction", "1")) != "1":
            raise HTTPException(status_code=403, detail="勘误提交功能已关闭")
        row = await EntryService.get_entry(db, entry_id)
        if not row:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        correction_id = await CorrectionService.submit(db, entry_id, farmer_id, data.content)
        await active_log(f"农户提交知识勘误: 条目{entry_id}", "knowledge_correction_submit",
                         rel_id=correction_id, request=request, db=db)
    return ok({"id": correction_id}, msg="提交成功，感谢您的反馈")
