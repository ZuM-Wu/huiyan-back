"""插件加载辅助，供 PluginManager 组合使用。"""

import importlib
import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# 12 类插件目录名
PLUGIN_MODULES = [
    "addon", "gateway", "sms", "mail", "captcha", "certification",
    "oauth", "oss", "server", "widget", "weather", "llm",
]


class PluginLoaderMixin:
    """封装插件路径解析、元数据读取与模块缓存清理，保持 PluginManager 行为不变。"""

    plugins_dir: Any

    def _find_plugin_path(self, name: str) -> str:
        """在 12 个类型子目录中查找插件目录"""
        for module_name in PLUGIN_MODULES:
            plugin_dir = self.plugins_dir / module_name / name
            if plugin_dir.is_dir() and (plugin_dir / "plugin.json").exists():
                return f"{module_name}/{name}"
        return name

    def load_metadata(self, name: str) -> dict:
        """
        读取插件的 plugin.json 元数据
        """
        rel_path = self._find_plugin_path(name)
        path = self.plugins_dir / rel_path / "plugin.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_plugin_class(self, name: str):
        """
        动态加载插件类与元数据（安装、卸载和能力恢复公共辅助）

        返回: (plugin_cls, meta) 元组；加载失败返回 (None, {})
        """
        try:
            rel_path = self._find_plugin_path(name)
            module_name = rel_path.split("/")[0]
            module = importlib.import_module(f"plugins.{module_name}.{name}.plugin")
            plugin_cls = getattr(module, "Plugin", None)
            if not plugin_cls:
                logger.error(f"插件 '{name}' 缺少 Plugin 类")
                return None, {}
            return plugin_cls, self.load_metadata(name)
        except Exception as e:
            logger.warning(f"加载插件 '{name}' 类失败: {e}")
            return None, {}


    def _invalidate_plugin_modules(self, name: str) -> None:
        """清理插件模块缓存，同时保留已注册到 SQLAlchemy 的 ORM 模型。"""
        rel_path = self._find_plugin_path(name)
        module_name = rel_path.split("/")[0]
        prefix = f"plugins.{module_name}.{name}"
        models_prefix = f"{prefix}.models"
        for loaded_name in list(sys.modules):
            is_plugin_module = (
                loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
            )
            is_models_module = (
                loaded_name == models_prefix
                or loaded_name.startswith(f"{models_prefix}.")
            )
            if is_plugin_module and not is_models_module:
                sys.modules.pop(loaded_name, None)
        importlib.invalidate_caches()

