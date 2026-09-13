"""慧眼自有物联网平台插件。"""

from core.plugin_manager import BasePlugin
from core.hardware_types import HardwareProvider
from .adapter import HuiyanAdapter


class Plugin(BasePlugin):
    name = "hardware_huiyan"
    title = "慧眼物联网设备管理"
    version = "1.0.3"

    async def install(self) -> bool:
        if not self.db:
            return False
        await self._run_sql_file("install.sql")
        return True

    async def uninstall(self) -> bool:
        if self.db:
            await self._run_sql_file("uninstall.sql")
        return True

    def get_routers(self):
        from .platform import router
        from .router_admin import router as admin_router
        return [router, admin_router]

    def get_config_schema(self):
        return []

    def get_hardware_providers(self):
        return [HardwareProvider(
            provider_id=self.name, title=self.title, factory=HuiyanAdapter,
            device_types=({"value": "monitor", "label": "监测设备"},
                          {"value": "growth", "label": "植物生长记录仪"},
                          {"value": "soil", "label": "土壤墒情仪"}),
        )]

    async def _run_sql_file(self, filename: str):
        from pathlib import Path
        sql = (Path(__file__).parent / "migrations" / filename).read_text(encoding="utf-8")
        await self._exec_sql(sql)

    def get_pages(self):
        """独立管理页由框架登记导航；公共硬件页仍保留跨来源汇总。"""
        return [{
            "key": "plugin_" + self.name, "title": self.title,
            "path": "/admin/plugin/" + self.name + "/index",
            "icon": "control-platform", "nav_type": "admin", "audience": "admin",
            "template": "index.html", "styles": [], "scripts": ["index.js"],
            "permission": "hardware:list", "api_base": "/api/admin/v1/plugins/" + self.name,
        }]
    async def repair_database(self, report: dict) -> dict | bool:
        return await self._repair_from_install_sql()
