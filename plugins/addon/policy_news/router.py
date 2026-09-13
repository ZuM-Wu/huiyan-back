"""农业政策资讯插件管理员端和农户端路由。"""
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth.middleware_chain import check_admin, check_farmer
from core.auth.rbac import require_permission
from core.log.active_log import active_log
from core.response import ok
from services.task.task_monitor import task_monitor

from .service import clear_policies, get_latest, get_status, refresh_policies

logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/api/admin/v1/plugins/policy_news",
    tags=["农业政策资讯"],
    dependencies=[Depends(check_admin)],
)
farmer_router = APIRouter(
    prefix="/api/v1/plugins/policy_news",
    tags=["农业政策资讯"],
    dependencies=[Depends(check_farmer)],
)


@admin_router.get("/status", dependencies=[Depends(require_permission("policy_news:list"))])
async def policy_status():
    """查看两个官方来源最近抓取状态和历史政策。"""
    return ok(await get_status())


@admin_router.post("/refresh", dependencies=[Depends(require_permission("policy_news:refresh"))])
async def policy_refresh(request: Request):
    """立即抓取一次政策列表。"""
    started = time.perf_counter()
    status = "success"
    error_message = ""
    try:
        result = await refresh_policies()
    except RuntimeError as exc:
        status = "failed"
        error_message = str(exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
        raise
    finally:
        try:
            await task_monitor.record_task_result(
                "policy_news.refresh", status, error_message,
                int((time.perf_counter() - started) * 1000),
                "手动刷新农业政策资讯", "policy_news",
            )
        except Exception as exc:
            logger.warning("手动刷新任务日志写入失败: %s", exc)
    await active_log("手动刷新农业政策资讯", "policy_news_refresh", request=request)
    return ok(result, msg="政策资讯刷新完成")


@admin_router.delete("/clear", dependencies=[Depends(require_permission("policy_news:clear"))])
async def policy_clear(request: Request):
    """清理政策历史和来源抓取状态。"""
    await clear_policies()
    await active_log("清理农业政策资讯", "policy_news_clear", request=request)
    return ok(msg="政策资讯已清理")


@farmer_router.get("/latest")
async def farmer_latest_policies():
    """返回农户端可展示的最新政策安全字段。"""
    items = await get_latest()
    public_items = [
        {
            "title": item["title"],
            "source": item["source"],
            "published_at": item["published_at"],
            "url": item["url"],
        }
        for item in items
    ]
    return ok({"items": public_items})
