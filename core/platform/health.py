"""平台扩展能力健康状态收集。"""

from core.time_utils import china_now


class PlatformHealth:
    """维护主题、插件和任务平台的最近状态。"""

    def __init__(self) -> None:
        self._components: dict[str, dict] = {}

    def mark(self, component: str, status: str, reason: str = "", **details) -> None:
        self._components[component] = {
            "status": status,
            "reason": reason,
            "checked_at": china_now().isoformat(),
            **details,
        }

    def clear(self, component: str) -> None:
        self._components.pop(component, None)

    def snapshot(self) -> dict[str, dict]:
        return {key: dict(value) for key, value in self._components.items()}

    def mark_resource_registry(self, details: dict) -> None:
        """更新资源索引健康摘要，供 /health 复用同一状态源。"""
        self.mark("resource_registry", details.pop("status", "ready"), **details)

    def degraded_components(self) -> list[str]:
        return sorted(
            key for key, value in self._components.items()
            if value.get("status") not in {"ok", "ready", "active"}
        )


platform_health = PlatformHealth()
