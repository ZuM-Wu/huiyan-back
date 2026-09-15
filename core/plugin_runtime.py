"""插件运行时能力注册与注销，供 PluginManager 组合使用。"""

import logging
from typing import Any

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)


class PluginRuntimeMixin:
    """封装事件、任务、管道、硬件、挂件与 MCP 能力的注册、注销与恢复。"""

    plugins_dir: Any
    _loaded: Any
    _find_plugin_path: Any
    _load_plugin_class: Any
    load_metadata: Any
    _validate_plugin_pages: Any

    def _unregister_runtime_capabilities(self, name: str, *, strict: bool = True) -> list[str]:
        """按 owner 注销运行时能力；停用阶段可逐项隔离失败。"""
        try:
            from core.events import event_registry, pipeline_engine
            from services.task.definitions import task_registry
            from services.widget.widget_engine import widget_engine
            from core.hardware_provider import hardware_provider_registry
        except Exception:
            if strict:
                raise
            logger.exception("插件 '%s' 的运行时能力模块加载失败", name)
            return ["import"]

        actions = (
            ("hardware", lambda: hardware_provider_registry.unregister_owner(name)),
            ("event", lambda: event_registry.unregister_owner(name)),
            ("pipeline", lambda: pipeline_engine.unregister_owner(name)),
            ("task", lambda: task_registry.unregister_owner(name)),
            ("widget", lambda: widget_engine.unregister_owner(name)),
            ("mcp", lambda: self._unregister_mcp_tools_runtime(name)),
        )
        failures: list[str] = []
        for capability, unregister in actions:
            try:
                unregister()
            except Exception:
                if strict:
                    raise
                logger.exception("插件 '%s' 的 %s 能力注销失败", name, capability)
                failures.append(capability)
        return failures

    def _register_runtime_capabilities(self, name: str, instance) -> None:
        """校验并原子注册插件声明的全部运行时能力。"""
        from core.events import event_registry, pipeline_engine
        from services.task.definitions import task_registry

        definitions = instance.get_event_definitions() or []
        subscriptions = instance.get_event_subscriptions() or []
        pipelines = instance.get_pipeline_handlers() or []
        tasks = instance.get_task_definitions() or []
        widgets = instance.get_widgets() or []
        if not all(isinstance(items, list) for items in (definitions, subscriptions, pipelines, tasks, widgets)):
            raise ValueError(f"插件 '{name}' 的能力声明必须返回列表")
        pages = instance.get_pages() or []
        if pages:
            self._validate_plugin_pages(name, pages)
            from core.plugin_page_validator import (
                validate_plugin_manifest_pages,
                validate_plugin_page_sources,
            )
            plugin_root = self.plugins_dir / self._find_plugin_path(name)
            validate_plugin_manifest_pages(plugin_root, self.load_metadata(name), pages)
            validate_plugin_page_sources(plugin_root, pages)
        self._unregister_runtime_capabilities(name)
        from core.platform.plugin_lifecycle import register_plugin_resources
        register_plugin_resources(name, self.load_metadata)
        try:
            from core.hardware_provider import hardware_provider_registry
            hardware = instance.get_hardware_providers()
            if not isinstance(hardware, list):
                raise ValueError("硬件来源声明必须返回列表")
            if hardware:
                hardware_provider_registry.register(
                    name, hardware,
                    self.plugins_dir / self._find_plugin_path(name), self.load_metadata(name),
                )
            for definition in definitions:
                if definition.owner != name:
                    raise ValueError(f"事件定义 owner 必须为插件名: {definition.name}")
                event_registry.register_definition(definition)
            for definition in tasks:
                if definition.owner != name:
                    raise ValueError(f"任务 owner 必须为插件名: {definition.name}")
                task_registry.register(definition)
            for subscription in subscriptions:
                if subscription.owner != name:
                    raise ValueError(f"事件订阅 owner 必须为插件名: {subscription.event_name}")
                event_registry.register_subscription(subscription)
            for declaration in pipelines:
                if declaration.owner != name:
                    raise ValueError(f"管道 owner 必须为插件名: {declaration.name}")
                pipeline_engine.register(declaration)
            self._register_widgets_runtime(name, widgets)
            self._register_mcp_tools_runtime(name, instance)
        except Exception:
            self._unregister_runtime_capabilities(name)
            from core.platform.resource import resource_registry
            resource_registry.invalidate_owner(name)
            raise

    @staticmethod
    def _register_widgets_runtime(name: str, widgets: list) -> None:
        """校验并注册插件挂件，异常由能力编排层统一回滚。"""
        from services.widget.widget_engine import BaseWidget, widget_engine

        for widget in widgets:
            if not isinstance(widget, BaseWidget):
                raise ValueError(f"插件 '{name}' 的挂件声明包含无效对象")
            widget_engine.register(widget, owner=name)

    def restore_capabilities(self, name: str) -> bool:
        """
        恢复插件显式能力（服务重启后或插件重新启用时调用）。
        """
        plugin_cls, meta = self._load_plugin_class(name)
        if not plugin_cls:
            return False
        instance = plugin_cls(None, meta.get("config", {}))
        try:
            self._register_runtime_capabilities(name, instance)
            self._loaded[name] = instance
            logger.info("插件 '%s' 运行时能力已恢复", name)
            return True
        except Exception:
            logger.exception("插件 '%s' 运行时能力恢复失败", name)
            self._unregister_runtime_capabilities(name)
            self._loaded.pop(name, None)
            return False

    @staticmethod
    def _register_mcp_tools_runtime(name: str, instance: "BasePlugin"):
        """
        注册插件声明的 MCP 工具（与其他显式能力在同一点位调用）

        先注销再注册保证幂等；MCP_ENABLED=False 时 registry 内部空操作，
        工具注册失败不阻断插件主流程（只记日志）。
        """
        try:
            from services.mcp.registry import register_tools, unregister_tools
            unregister_tools(name)
            register_tools(name, instance.get_mcp_tools(), permissions=instance.get_permissions())
        except Exception as e:
            logger.warning(f"插件 '{name}' MCP 工具注册失败(不影响插件运行): {e}", exc_info=True)

    @staticmethod
    def _unregister_mcp_tools_runtime(name: str):
        """注销插件的 MCP 工具（禁用/卸载时调用，失败不阻断主流程）"""
        try:
            from services.mcp.registry import unregister_tools
            unregister_tools(name)
        except Exception as e:
            logger.warning(f"插件 '{name}' MCP 工具注销失败: {e}", exc_info=True)

    @staticmethod
    async def _sync_mcp_tool_policies(name: str, db) -> None:
        """在插件生命周期事务中登记该插件当前声明的工具策略。"""
        from services.agentscope.tools import sync_tool_policies
        from services.mcp.registry import list_registered_tool_declarations

        declarations = [
            item for item in list_registered_tool_declarations()
            if item.get("owner") == name
        ]
        await sync_tool_policies(db, declarations)
