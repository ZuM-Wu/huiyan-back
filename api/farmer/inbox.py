"""农户站内信 API

农户查看自己的站内信、未读数、标记已读、删除。
数据隔离强制 receiver_id == 当前农户 AND receiver_type == "farmer"，
越权访问返回 404（不暴露他人消息存在）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from core.auth.middleware_chain import check_farmer
from core.inbox_service import (
    list_inbox_messages, get_unread_count, get_farmer_message_detail,
    mark_read, mark_all_read, delete_message,
)
from core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/inbox", tags=["农户站内信"])

RECEIVER_TYPE = "farmer"


@router.get("/list")
async def list_inbox(
    request: Request,
    is_read: int = Query(None, ge=0, le=1, description="已读筛选:0=未读 1=已读"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: None = Depends(check_farmer),
):
    """查当前农户站内信（分页，按 create_time desc）"""
    farmer_id = request.state.user_id
    data = await list_inbox_messages(
        farmer_id, RECEIVER_TYPE, page=page, limit=limit, is_read=is_read
    )
    return ok(data)


@router.get("/unread-count")
async def unread_count(request: Request, _: None = Depends(check_farmer)):
    """未读数（顶栏铃铛轮询用，返回 {count}）"""
    farmer_id = request.state.user_id
    count = await get_unread_count(farmer_id, RECEIVER_TYPE)
    return ok({"count": count})


@router.get("/{message_id}")
async def get_detail(message_id: int, request: Request, _: None = Depends(check_farmer)):
    """查看单条站内信详情（校验归属，越权返 404）"""
    farmer_id = request.state.user_id
    data = await get_farmer_message_detail(farmer_id, message_id)
    if not data:
        raise HTTPException(status_code=404, detail="消息不存在")
    return ok(data)


@router.put("/read-all")
async def read_all(request: Request, _: None = Depends(check_farmer)):
    """全部已读（UPDATE WHERE receiver_id=user AND is_read=0）"""
    farmer_id = request.state.user_id
    updated = await mark_all_read(farmer_id, RECEIVER_TYPE)
    return ok({"updated": updated}, msg=f"已标记 {updated} 条为已读")


@router.put("/{message_id}/read")
async def read_message(message_id: int, request: Request, _: None = Depends(check_farmer)):
    """标记单条已读（校验归属，越权返 404）"""
    farmer_id = request.state.user_id
    success = await mark_read(message_id, farmer_id, RECEIVER_TYPE)
    if not success:
        raise HTTPException(status_code=404, detail="消息不存在")
    return ok(msg="已标记已读")


@router.delete("/{message_id}")
async def del_message(message_id: int, request: Request, _: None = Depends(check_farmer)):
    """删除单条（校验归属，越权返 404）"""
    farmer_id = request.state.user_id
    success = await delete_message(message_id, farmer_id, RECEIVER_TYPE)
    if not success:
        raise HTTPException(status_code=404, detail="消息不存在")
    return ok(msg="已删除")
