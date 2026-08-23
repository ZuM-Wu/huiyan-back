"""插件路由 owner gate 公共门面。"""


class RoutePlatform:
    """只编排现有 APIRouterManager，不在平台层维护第二套路由表。"""

    @staticmethod
    def register_startup_snapshot(manager, owner: str) -> dict:
        """读取启动阶段已登记的 owner 路由快照。"""
        if not manager or not owner:
            raise ValueError("路由 manager 和 owner 不能为空")
        return manager.runtime_snapshot(owner)

    @staticmethod
    def set_owner_enabled(manager, owner: str, enabled: bool) -> dict:
        """通过既有门禁启停 owner，不删除或替换 FastAPI 路由。"""
        if enabled:
            manager.mark_enabled(owner)
        else:
            manager.mark_disabled(owner)
        return manager.runtime_snapshot(owner)

    @staticmethod
    def snapshot(manager, owner: str) -> dict:
        return RoutePlatform.register_startup_snapshot(manager, owner)


route_platform = RoutePlatform()
