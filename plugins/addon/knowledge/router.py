# -*- coding: utf-8 -*-
"""
农业知识库插件路由入口

core/api_router.py 仅从 plugins.addon.{name}.router 模块读取
`router`（管理员端）与 `farmer_router`（农户端）两个属性，
本文件只做 re-export，端点实现按端拆分在 router_admin / router_farmer。
"""
from plugins.addon.knowledge.router_admin import router
from plugins.addon.knowledge.router_farmer import farmer_router

__all__ = ["farmer_router", "router"]
