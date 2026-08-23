"""系统日志 API"""
from fastapi import APIRouter, Depends, Query, Request

from core.auth.middleware_chain import check_admin
from core.response import ok
from core.system_log_service import list_logs as svc_list_logs

router = APIRouter(prefix="/api/admin/v1/log", tags=["系统日志"])


@router.get("/list")
async def list_logs(
    request: Request,
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
    log_type: str = Query("", description="日志类型，支持逗号分隔多类型"),
    keyword: str = Query("", description="搜索关键词"),
    _: None = Depends(check_admin)
):
    """获取操作日志列表（分页），log_type 支持逗号分隔多类型"""
    result = await svc_list_logs(
        page=page,
        limit=limit,
        keywords=keyword,
        log_type=log_type,
    )
    return ok(result)
