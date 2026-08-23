"""插件页面清单解析与模板访问控制。"""

from core.plugin_manager import PluginManager


def resolve_plugin_page(plugin_name: str, page: str, audience: str) -> dict | None:
    """只返回插件显式声明且路径与受众匹配的页面。"""
    manager = PluginManager()
    plugin_cls, meta = manager._load_plugin_class(plugin_name)
    if not plugin_cls:
        return None
    instance = plugin_cls(None, meta.get("config", {}))
    route_root = "admin" if audience == "admin" else "farmer"
    expected_path = f"/{route_root}/plugin/{plugin_name}/{page}"
    for declaration in instance.get_pages() or []:
        if declaration.get("path", "").rstrip("/") != expected_path.rstrip("/"):
            continue
        declared_audience = declaration.get("audience") or (
            "admin" if declaration.get("nav_type", "admin") == "admin" else "farmer"
        )
        if declared_audience != audience:
            return None
        template = declaration.get("template") or f"{page}.html"
        if "/" in template or "\\" in template or ".." in template:
            return None
        return {
            **declaration,
            "template": f"plugins/{plugin_name}/{template}",
            "styles": list(declaration.get("styles") or []),
            "scripts": list(declaration.get("scripts") or []),
            "permission": declaration.get("permission", ""),
            "api_base": declaration.get("api_base") or (
                f"/api/admin/v1/plugins/{plugin_name}"
                if audience == "admin" else f"/api/v1/plugins/{plugin_name}"
            ),
        }
    return None
