"""插件菜单和页面注册的稳定查询门面。"""

from core.menu_service import list_menus


class MenuPlatform:
    """复用现有 menu_service；插件只提交声明，不能直接写 ORM。"""

    async def list_sidebar(self, surface: str = "admin") -> list[dict]:
        if surface not in {"admin", "frontend"}:
            raise ValueError("菜单端面仅支持 admin/frontend")
        return await list_menus(nav_type=surface)

    @staticmethod
    def page_key(plugin_id: str, page_key: str) -> str:
        if not plugin_id or not page_key or any(part in {"", ".", ".."} for part in (plugin_id, page_key)):
            raise ValueError("插件页面 key 无效")
        return f"plugin_{plugin_id}_{page_key}"


menu_platform = MenuPlatform()
