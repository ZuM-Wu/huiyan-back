"""
农户端菜单与系统 API
- GET /api/v1/menu/tree       获取农户端菜单树（侧边栏用）
- GET /api/v1/system/info     获取系统概要信息（只读、脱敏）
- GET /api/v1/log/list        获取当前农户的操作日志
"""
import logging
import time
import sys
import platform

from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy import select, func

from core.db.base import async_session_factory
from core.auth.middleware_chain import check_farmer
from core.menu_service import get_farmer_menus
from core.config_service import get_config
from core.response import ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["农户菜单与系统"])

# 应用启动时间
_start_time = time.time()


def _get_version(module_name: str) -> str:
    """安全获取已安装包的版本号"""
    try:
        mod = __import__(module_name)
        return getattr(mod, "__version__", "未知")
    except ImportError:
        return "未安装"


@router.get("/menu/tree")
async def farmer_menu_tree(request: Request, _: None = Depends(check_farmer)):
    """获取前台菜单树（农户端侧边栏用，不做权限过滤）"""
    tree = await get_farmer_menus(request.state.user_id)
    return ok({"list": tree})


@router.get("/system/info")
async def farmer_system_info(_: None = Depends(check_farmer)):
    """获取系统概要信息（只读、脱敏，农户端可查看）"""
    from core.config import settings

    uptime = int(time.time() - _start_time)
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # 数据库连通性检测（用 config_service 读取配置作为探针）
    db_status = "connected"
    table_count = 0
    try:
        val = await get_config("site_name")
        if val is not None:
            table_count = 1
    except Exception as e:
        db_status = f"error: {str(e)[:80]}"

    return ok({
        "app_version": settings.app_version,
        "debug_mode": settings.APP_DEBUG,
        "python_version": py_version,
        "fastapi_version": _get_version("fastapi"),
        "sqlalchemy_version": _get_version("sqlalchemy"),
        "os_info": f"{platform.system()} {platform.release()}",
        "database_status": db_status,
        "table_count": table_count,
        "uptime_seconds": uptime,
    })


@router.get("/log/list")
async def farmer_log_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=15, ge=1, le=100),
    keyword: str = Query(default=""),
    log_type: str = Query(default=""),
    _: None = Depends(check_farmer),
):
    """获取当前农户的操作日志（仅返回与当前农户相关的日志）"""
    from core.db.system_log import SystemLog

    farmer_id = request.state.user_id

    async with async_session_factory() as db:
        # 仅查询当前农户的日志
        query = select(SystemLog).where(SystemLog.user_id == farmer_id)

        # 按日志类型过滤
        if log_type:
            query = query.where(SystemLog.type == log_type)

        # 按关键词搜索描述
        if keyword:
            query = query.where(SystemLog.description.contains(keyword))

        # 获取总数
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # 分页查询
        query = query.order_by(SystemLog.id.desc()).offset((page - 1) * limit).limit(limit)
        result = await db.execute(query)
        logs = result.scalars().all()

        return ok({
            "list": [
                {
                    "id": log.id,
                    "type": log.type or "",
                    "description": log.description or "",
                    "user_name": log.user_name or "",
                    "ip": log.ip or "",
                    "create_time": str(log.create_time) if log.create_time else "",
                }
                for log in logs
            ],
            "total": total,
        })
