"""
通知日志 API（管理员端）
提供发送日志查询、详情、统计与清理能力
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok
from core.notice_query_service import (
    list_notice_logs,
    get_notice_log,
    notice_log_stats,
    cleanup_notice_logs,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/notice/logs", tags=["通知日志"])


@router.get("/list", dependencies=[Depends(require_permission("notice:list"))])
async def list_logs(
    action_key: str = Query(None, description="动作标识筛选"),
    channel: str = Query(None, description="渠道筛选:sms/email"),
    status: int = Query(None, ge=0, le=1, description="状态筛选:0=失败 1=成功"),
    recipient: str = Query(None, description="接收者筛选（手机/邮箱模糊匹配）"),
    start_time: str = Query(None, description="开始时间 YYYY-MM-DD"),
    end_time: str = Query(None, description="结束时间 YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """通知日志列表 — 支持多条件筛选与分页"""
    filters = {}
    if action_key:
        filters["action_key"] = action_key
    if channel:
        filters["channel"] = channel
    if status is not None:
        filters["status"] = status
    if recipient:
        filters["recipient"] = recipient
    if start_time:
        try:
            datetime.strptime(start_time, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="开始时间格式错误，应为 YYYY-MM-DD")
        filters["start_time"] = start_time
    if end_time:
        try:
            datetime.strptime(end_time, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="结束时间格式错误，应为 YYYY-MM-DD")
        filters["end_time"] = end_time

    result = await list_notice_logs(page=page, limit=limit, filters=filters)
    return ok(result)


@router.get("/stats", dependencies=[Depends(require_permission("notice:list"))])
async def log_stats(
    days: int = Query(7, ge=1, le=90, description="统计最近 N 天"),
    _: None = Depends(check_admin),
):
    """通知发送统计 — 按渠道汇总成功/失败次数"""
    result = await notice_log_stats(days)
    return ok(result)


@router.get("/{log_id}", dependencies=[Depends(require_permission("notice:list"))])
async def get_log(
    log_id: int,
    _: None = Depends(check_admin),
):
    """获取单条日志详情（含完整发送上下文 extra）"""
    log = await get_notice_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")
    return ok(log)


@router.delete("/cleanup", dependencies=[Depends(require_permission("notice:delete"))])
async def cleanup_logs(
    before_days: int = Query(90, ge=7, description="删除 N 天之前的日志"),
    _: None = Depends(check_admin),
):
    """清理历史日志 — 删除 N 天之前的记录（默认 90 天）"""
    deleted = await cleanup_notice_logs(before_days)
    logger.info(f"[通知日志] 清理 {before_days} 天前日志，删除 {deleted} 条")
    return ok({"deleted": deleted})
