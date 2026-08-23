"""站内信管理 API（管理员端）

全站站内信查询、统计、删除。后台不提供"查看自己消息/标记已读"入口：
管理员经管理页直接查看全站站内信（含发给自己的，按 receiver_type=admin 筛选）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok
from core.inbox_service import (
    list_all_inbox,
    get_inbox_stats,
    get_inbox_message,
    bulk_delete_messages,
    delete_inbox_message_admin,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/inbox", tags=["站内信管理"])


class BulkDeleteRequest(BaseModel):
    """批量删除请求"""
    ids: list[int]


@router.get("/list", dependencies=[Depends(require_permission("inbox:list"))])
async def list_inbox(
    request: Request,
    receiver_type: str = Query(None, description="接收者类型:farmer/admin"),
    receiver_id: int = Query(None, description="接收者ID"),
    is_read: int = Query(None, ge=0, le=1, description="已读筛选:0=未读 1=已读"),
    keyword: str = Query(None, description="标题关键词模糊匹配"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """全站站内信列表 — 支持多条件筛选与分页"""
    filters = {}
    if receiver_type:
        filters["receiver_type"] = receiver_type
    if receiver_id is not None:
        filters["receiver_id"] = receiver_id
    if is_read is not None:
        filters["is_read"] = is_read
    if keyword:
        filters["keyword"] = keyword

    result = await list_all_inbox(page=page, limit=limit, filters=filters)
    return ok(result)


@router.get("/stats", dependencies=[Depends(require_permission("inbox:list"))])
async def inbox_stats(request: Request, _: None = Depends(check_admin)):
    """统计 — 总数/未读数/按 receiver_type 分组"""
    result = await get_inbox_stats()
    return ok(result)


@router.get("/{message_id}", dependencies=[Depends(require_permission("inbox:list"))])
async def get_detail(message_id: int, request: Request, _: None = Depends(check_admin)):
    """查看单条站内信详情"""
    msg = await get_inbox_message(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="消息不存在")
    return ok(msg)


@router.delete("/bulk", dependencies=[Depends(require_permission("inbox:delete"))])
async def bulk_delete(data: BulkDeleteRequest, request: Request, _: None = Depends(check_admin)):
    """批量删除（body 传 id 列表）

    注意：此路由须在 DELETE /{message_id} 之前注册，避免 /bulk 被参数路由匹配。
    """
    if not data.ids:
        raise HTTPException(status_code=400, detail="缺少 ids")
    deleted = await bulk_delete_messages(data.ids)
    return ok({"deleted": deleted}, msg=f"已删除 {deleted} 条")


@router.delete("/{message_id}", dependencies=[Depends(require_permission("inbox:delete"))])
async def delete_message(message_id: int, request: Request, _: None = Depends(check_admin)):
    """删除单条"""
    success = await delete_inbox_message_admin(message_id)
    if not success:
        raise HTTPException(status_code=404, detail="消息不存在")
    return ok(msg="已删除")
