"""农户端首页挂件只读接口。"""
from fastapi import APIRouter, Depends

from core.auth.middleware_chain import check_farmer
from core.response import ok
from services.widget.widget_engine import widget_engine

router = APIRouter(prefix="/api/v1/widget", tags=["农户端仪表盘挂件"])


@router.get("/dashboard", dependencies=[Depends(check_farmer)])
async def widget_dashboard():
    """返回面向农户端的启用挂件数据，不提供配置写入能力。"""
    return ok(await widget_engine.get_farmer_dashboard())
