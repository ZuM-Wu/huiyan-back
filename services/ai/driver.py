# -*- coding: utf-8 -*-
"""
LLM 驱动插件路由
按 hy_plugin 表（module="llm"）解析已启用的驱动插件实例

设计对标 core/task/notice_worker.py 的 resolve_plugin：
- interface 指定时按名精确匹配（兼容 deepseek -> llm_deepseek 简写）
- interface 为空时自动回落该模块下第一个已启用插件
- 配置从 hy_configuration 按 "{插件名}." 前缀读取，以 (None, config) 实例化
"""
import importlib
import logging
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.plugin import PluginModel
from core.config_manager import ConfigManager

logger = logging.getLogger(__name__)


def get_llm_readiness(plugin: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    """检查 LLM 驱动必填配置，返回统一的就绪状态。"""
    try:
        schema = plugin.get_config_schema() or []
    except Exception as exc:
        logger.warning("[AI驱动路由] 读取插件配置 schema 失败: %s", exc)
        return {
            "ready": False,
            "reason_code": "config_schema_error",
            "message": "无法读取模型驱动配置要求",
            "missing": [],
        }

    missing = []
    for field in schema:
        if not field.get("required"):
            continue
        key = str(field.get("key") or "").strip()
        if not key or not str(config.get(key) or "").strip():
            missing.append(key or "unknown")
    if missing:
        return {
            "ready": False,
            "reason_code": "config_missing",
            "message": "模型接口尚未完成必填配置",
            "missing": missing,
        }
    return {
        "ready": True,
        "reason_code": "ready",
        "message": "模型接口已就绪",
        "missing": [],
    }


def _driver_load_error(record: Any, config: Dict[str, Any], module: str) -> Tuple[Optional[Any], str]:
    """实例化一个插件并返回统一错误，供路由循环复用。"""
    try:
        plugin_module = importlib.import_module(f"plugins.{module}.{record.name}.plugin")
        plugin_cls = getattr(plugin_module, "Plugin", None)
        if not plugin_cls:
            return None, f"插件 {record.name} 缺少 Plugin 类"
        return plugin_cls(None, config), ""
    except Exception as exc:
        logger.error("[AI驱动路由] 插件加载失败: %s, %s", record.name, exc)
        return None, f"插件加载失败：{exc}"


def _should_return_driver(interface: str, require_configured: bool) -> bool:
    """指定接口或非配置门禁模式下，插件错误应立即返回。"""
    return bool(interface or not require_configured)


async def resolve_llm_plugin(
        interface: str = "", require_configured: bool = False,
) -> Tuple[Optional[Any], Dict[str, Any], str]:
    """
    解析已启用的 LLM 驱动插件实例及其配置

    Args:
        interface: 接口标识（如 deepseek/llm_deepseek）；
            为空时自动选择 llm 模块下第一个已启用的插件
        require_configured: 是否只返回必填配置完整的驱动

    Returns:
        (插件实例, 插件配置 dict, 错误信息)；解析失败时实例为 None
    """
    module = "llm"
    async with async_session_factory() as db:
        if interface:
            # 兼容简写：deepseek -> llm_deepseek
            candidates = [interface]
            prefixed = f"{module}_{interface}"
            if prefixed not in candidates:
                candidates.append(prefixed)
            result = await db.execute(
                select(PluginModel).where(
                    PluginModel.name.in_(candidates),
                    PluginModel.module == module,
                    PluginModel.status == 1,
                )
            )
        else:
            # 未指定接口：自动回落到第一个已启用的 llm 插件
            result = await db.execute(
                select(PluginModel).where(
                    PluginModel.module == module,
                    PluginModel.status == 1,
                )
            )
        scalar_result = result.scalars()
        records = scalar_result.all()
        # 兼容旧测试替身只实现 scalars().first() 的最小查询门面。
        if not isinstance(records, (list, tuple)):
            first = scalar_result.first()
            records = [first] if first is not None else []
        if not records:
            if interface:
                return None, {}, f"模型驱动插件未安装或未启用：{interface}"
            return None, {}, "无已启用的大模型驱动插件，请先在 AI 设置中安装并启用"

        # 显式接口只匹配一个候选；自动回落时跳过未完成配置的插件。
        for record in records:
            config = await ConfigManager().get_plugin_config(record.name, db)
            plugin, error = _driver_load_error(record, config, module)
            if not plugin:
                if _should_return_driver(interface, require_configured):
                    return None, {}, error
                continue

            if require_configured:
                readiness = get_llm_readiness(plugin, config)
                if not readiness["ready"]:
                    error = f"插件 {record.name}{readiness['message']}"
                    if interface:
                        return None, {}, error
                    continue
            return plugin, config, ""

    if interface:
        return None, {}, f"模型驱动插件未完成配置：{interface}"
    if require_configured:
        return None, {}, "无已完成配置的大模型驱动插件，请先在 AI 设置中配置并启用"
    return None, {}, "无可用的大模型驱动插件"
