# -*- coding: utf-8 -*-
"""
App管理插件路由聚合入口

core/api_router.py 从本模块自动发现 router（管理端）与 farmer_router（App公开）。
实际端点定义拆分在 router_admin.py / router_public.py，此处仅 re-export。
"""
from plugins.addon.app_manage.router_admin import router
from plugins.addon.app_manage.router_public import farmer_router

__all__ = ["farmer_router", "router"]
