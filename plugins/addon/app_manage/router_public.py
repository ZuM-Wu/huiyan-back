# -*- coding: utf-8 -*-
"""
App管理插件 — App公开路由（前缀 /api/v1/app_manage，导出名 farmer_router）

规范说明（与常规农户端路由的差异，已在接口文档中显式记录）：
- 本路由不挂任何鉴权依赖 —— App 启动阶段（登录前）即需调用检查更新/
  开屏广告/公告，必须免登录（先例：api/farmer/auth.py 登录注册接口）
- 均为只读 GET + 白名单字段 + 服务端缓存（TTL 默认 300 秒）兜底防刷
- APK/广告图经 /upload/app_manage/ 公开静态路径直接下载（支持断点续传）
"""
from fastapi import APIRouter, Query

from core.db.base import async_session_factory
from plugins.addon.app_manage.services.ad_service import AdService
from plugins.addon.app_manage.services.apk_service import ApkService
from plugins.addon.app_manage.services.notice_service import NoticeService
from core.response import ok

# 免登录公开路由：不挂 Depends(check_farmer)，App 登录前即可调用
farmer_router = APIRouter(
    prefix="/api/v1/app_manage",
    tags=["App管理插件（App公开）"],
)


@farmer_router.get("/update/check")
async def check_update(
    version_code: int = Query(..., ge=0, description="App当前版本号"),
):
    """
    检查更新 — 与最新发布版本(status=1 中 version_code 最大者)比较。
    有更新返回版本详情与下载地址；无更新或无发布版本返回 has_update=false。
    """
    async with async_session_factory() as db:
        latest = await ApkService.get_latest_published(db)
    if not latest or latest["version_code"] <= version_code:
        return ok({"has_update": False})
    return ok({"has_update": True, **latest})


@farmer_router.get("/ad")
async def get_splash_ad():
    """
    开屏广告 — 仅当启用且当前时间在投放时间窗内才返回广告，否则 ad=null。
    App端以 cache_key 比对本地缓存，一致则直接用本地素材（广告缓存机制）。
    """
    async with async_session_factory() as db:
        ad = await AdService.get_active_ad(db)
    return ok({"ad": ad})


@farmer_router.get("/notices")
async def list_active_notices():
    """生效公告列表 — enabled=1 且在生效时间窗内，按 sort_order 升序，最多 20 条"""
    async with async_session_factory() as db:
        notices = await NoticeService.list_active(db)
    return ok({"list": notices})
