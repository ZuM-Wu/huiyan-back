"""产区管理管理员端 API 子包

聚合产区/地块/批次/地图配置四个 router 供 main.py 导入。
map_config 的 `/map-config` 须在 area 的 `/{area_id}` 之前注册，避免路径被 int 转换拦截。
"""
from fastapi import APIRouter

from api.admin.production.map_config import router as map_config_router
from api.admin.production.area import router as area_router
from api.admin.production.plot import router as plot_router
from api.admin.production.batch import router as batch_router

# 聚合 router（map_config 优先，保证 /production-area/map-config 精确匹配）
router = APIRouter()
router.include_router(map_config_router)
router.include_router(area_router)
router.include_router(plot_router)
router.include_router(batch_router)
