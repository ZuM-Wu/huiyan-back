"""
系统信息 API — 提供运行时环境、依赖版本、数据库状态等只读信息
"""
import sys
import time
import platform

from fastapi import APIRouter, Depends

from core.config_service import get_config
from core.auth.middleware_chain import check_admin
from core.response import ok

router = APIRouter(prefix="/api/admin/v1/system", tags=["系统信息"])

# 记录应用启动时间（模块级全局变量）
_start_time = time.time()


def _get_version(module_name: str) -> str:
    """安全获取已安装包的版本号"""
    try:
        mod = __import__(module_name)
        return getattr(mod, "__version__", "未知")
    except ImportError:
        return "未安装"


def _mask_db_url(url: str) -> str:
    """脱敏数据库连接字符串（隐藏密码）"""
    if "://" not in url:
        return url
    try:
        # 形如 mysql+aiomysql://root:password@host/db
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            auth, host_db = rest.rsplit("@", 1)
            if ":" in auth:
                user, _ = auth.split(":", 1)
                return f"{scheme}://{user}:****@{host_db}"
        return url
    except Exception:
        return url


@router.get("/info")
async def get_system_info(_: None = Depends(check_admin)):
    """获取系统运行信息（只读）"""

    # 数据库连通性检测（通过配置服务读取探针，成功即表示 DB 可达）
    db_status = "connected"
    table_count = 0
    try:
        await get_config("site_name")
    except Exception as e:
        db_status = f"error: {str(e)[:80]}"

    # 数据库 URL 脱敏
    from core.config import settings
    db_url = _mask_db_url(settings.DATABASE_URL)

    # 运行时长
    uptime = int(time.time() - _start_time)

    # Python 版本（取主版本号）
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    return ok({
        "python_version": py_version,
        "fastapi_version": _get_version("fastapi"),
        "sqlalchemy_version": _get_version("sqlalchemy"),
        "uvicorn_version": _get_version("uvicorn"),
        "database_url": db_url,
        "database_status": db_status,
        "table_count": table_count,
        "os_info": f"{platform.system()} {platform.release()}",
        "app_version": settings.app_version,
        "debug_mode": settings.APP_DEBUG,
        "uptime_seconds": uptime,
    })
