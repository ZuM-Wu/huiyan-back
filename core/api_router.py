"""
路由管理器
慧眼护农 3.4.1 核心骨架

负责在启动时扫描已启用插件，动态注册其 APIRouter 到 FastAPI 主应用。

启停门禁说明:
FastAPI 不支持运行时移除已挂载的路由，因此插件禁用采用
"内存禁用集合 + 依赖门禁"方案——注册插件路由时附加 gate 依赖，
请求命中禁用集合直接返回 404（等效"路由不存在"），纯内存 O(1) 判断。
"""

import logging

from fastapi import Depends, FastAPI, HTTPException

logger = logging.getLogger(__name__)


class APIRouterManager:
    """
    路由管理器
    启动时自动扫描插件目录，合并所有已启用插件的路由
    """

    def __init__(self, app: FastAPI):
        self.app = app
        self._registered_plugins: set = set()
        self._plugin_routes: dict[str, list] = {}
        # 禁用插件集合：路由已挂载但插件被禁用/卸载时，门禁依赖据此返回 404
        self._disabled: set = set()

    def _make_plugin_gate(self, plugin_name: str):
        """构造插件门禁依赖：插件在禁用集合中时直接 404"""
        async def _gate():
            if plugin_name in self._disabled:
                raise HTTPException(status_code=404, detail="Not Found")
        return _gate

    @staticmethod
    def _load_router_module(plugin_name: str):
        """仅从插件显式 get_routers() 声明加载路由。"""
        from core.plugin_manager import PluginManager

        manager = PluginManager()
        plugin_cls, meta = manager._load_plugin_class(plugin_name)
        if not plugin_cls:
            return tuple()
        routers = plugin_cls(None, meta.get("config", {})).get_routers()
        if not isinstance(routers, list):
            raise ValueError(f"插件 '{plugin_name}' 的 get_routers() 必须返回列表")
        return tuple(routers)

    def _include_plugin_routers(self, plugin_name: str, routers: tuple) -> bool:
        """把已成功导入的路由对象挂载到主应用并登记归属。"""
        if not routers:
            return False
        gate = [Depends(self._make_plugin_gate(plugin_name))]
        for current_router in routers:
            if not current_router:
                continue
            before = len(self.app.router.routes)
            self.app.include_router(current_router, dependencies=gate)
            self._plugin_routes.setdefault(plugin_name, []).extend(
                self.app.router.routes[before:]
            )
            logger.info("已注册插件路由: %s", plugin_name)
        self._registered_plugins.add(plugin_name)
        return True

    def register_plugin_router(self, plugin_name: str):
        """动态加载并注册插件的路由（支持管理员端与农户端）。"""
        if plugin_name in self._registered_plugins:
            return True
        try:
            return self._include_plugin_routers(
                plugin_name, self._load_router_module(plugin_name)
            )
        except ModuleNotFoundError:
            return False
        except Exception as e:
            logger.error(f"插件 '{plugin_name}' 路由注册失败: {e}")
            return False

    def is_registered(self, plugin_name: str) -> bool:
        """检查插件路由是否已注册"""
        return plugin_name in self._registered_plugins

    def unregister_plugin_router(self, plugin_name: str) -> None:
        """移除本管理器记录的插件路由，供升级时原子替换。"""
        routes = self._plugin_routes.pop(plugin_name, [])
        route_ids = {id(route) for route in routes}
        self.app.router.routes[:] = [
            route for route in self.app.router.routes if id(route) not in route_ids
        ]
        self._registered_plugins.discard(plugin_name)

    def refresh_plugin_router(self, plugin_name: str) -> bool:
        """先校验新路由可导入，再原子替换插件当前路由。"""
        had_routes = plugin_name in self._registered_plugins or bool(
            self._plugin_routes.get(plugin_name)
        )
        try:
            routers = self._load_router_module(plugin_name)
        except Exception as exc:
            logger.error("插件 '%s' 新路由导入失败: %s", plugin_name, exc)
            return False
        if not any(routers):
            return not had_routes

        routes_snapshot = list(self.app.router.routes)
        owned_snapshot = list(self._plugin_routes.get(plugin_name, []))
        registered_snapshot = plugin_name in self._registered_plugins
        try:
            self.unregister_plugin_router(plugin_name)
            return self._include_plugin_routers(plugin_name, routers)
        except Exception as exc:
            self.app.router.routes[:] = routes_snapshot
            if owned_snapshot:
                self._plugin_routes[plugin_name] = owned_snapshot
            else:
                self._plugin_routes.pop(plugin_name, None)
            if registered_snapshot:
                self._registered_plugins.add(plugin_name)
            else:
                self._registered_plugins.discard(plugin_name)
            logger.error("插件 '%s' 路由替换失败: %s", plugin_name, exc)
            return False

    def mark_disabled(self, plugin_name: str):
        """将插件加入禁用集合（其已挂载路由立即返回 404）"""
        self._disabled.add(plugin_name)
        logger.info(f"插件路由门禁已禁用: {plugin_name}")

    def mark_enabled(self, plugin_name: str):
        """将插件移出禁用集合（其路由立即恢复可用）"""
        self._disabled.discard(plugin_name)
        logger.info(f"插件路由门禁已放行: {plugin_name}")

    def owner_gate_status(self, plugin_name: str) -> str:
        """返回 owner 门禁状态，供 platform.route 查询。"""
        return "disabled" if plugin_name in self._disabled else "enabled"

    def runtime_snapshot(self, plugin_name: str) -> dict:
        """返回 owner 路由数量和门禁状态，不暴露 FastAPI 路由对象。"""
        return {
            "owner": plugin_name,
            "registered": self.is_registered(plugin_name),
            "gate": self.owner_gate_status(plugin_name),
            "route_count": len(self._plugin_routes.get(plugin_name, [])),
        }
