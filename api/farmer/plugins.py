# -*- coding: utf-8 -*-
"""
农户端插件状态 API
返回当前已启用插件名称列表，供客户端预加载缓存、控制功能入口显隐。
"""
from fastapi import APIRouter
from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.plugin import PluginModel
from core.response import ok

router = APIRouter(prefix="/api/v1/plugins", tags=["插件状态"])


@router.get("/enabled")
async def list_enabled_plugins():
    """返回当前已启用插件名称列表（公共接口，无需登录）

    客户端启动时预加载缓存，根据返回的插件名列表决定功能入口显隐。
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(PluginModel.name).where(PluginModel.status == 1)
        )
        names = result.scalars().all()
    return ok({"plugins": names})
