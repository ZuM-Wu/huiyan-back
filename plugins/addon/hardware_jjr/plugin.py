"""JJR 硬件接入插件：声明协议、详情资源与独立管理页面。"""

from core.plugin_manager import BasePlugin
from core.hardware_types import HardwareDetailView, HardwareProvider
from .adapter import JjrAdapter


class Plugin(BasePlugin):
    name = "hardware_jjr"
    title = "jjr物联网设备管理"
    version = "1.0.2"

    async def install(self) -> bool:
        """凭据由配置页或停机迁移写入，安装不覆盖任何既有配置。"""
        return True

    async def uninstall(self) -> bool:
        """设备保护及配置删除由公共生命周期执行。"""
        return True

    def get_config_schema(self) -> list[dict]:
        return [
            {"key": "base_url", "label": "平台地址", "type": "input", "required": True,
             "placeholder": "https://farmbot-jjr.jjr.vip"},
            {"key": "owner_token", "label": "平台访问凭据", "type": "password", "required": False, "help": "留空保持原凭据"},
        ]

    def get_hardware_providers(self) -> list[HardwareProvider]:
        return [HardwareProvider(
            provider_id=self.name, title=self.title, factory=JjrAdapter,
            device_types=({"value": "growth", "label": "植物生长记录仪"},
                          {"value": "soil", "label": "土壤墒情仪"},
                          {"value": "root", "label": "根系记录仪"}),
            detail_view=HardwareDetailView("hardware_jjr.detail", ("hardware_jjr.style",)),
        )]

    def get_routers(self):
        from .router_admin import router
        return [router]

    def get_pages(self):
        """独立管理页由框架登记导航；公共硬件页仍保留跨来源汇总。"""
        return [{
            "key": "plugin_" + self.name, "title": self.title,
            "path": "/admin/plugin/" + self.name + "/index",
            "icon": "control-platform", "nav_type": "admin", "audience": "admin",
            "template": "index.html", "styles": [], "scripts": ["index.js"],
            "permission": "hardware:list", "api_base": "/api/admin/v1/plugins/" + self.name,
        }]
