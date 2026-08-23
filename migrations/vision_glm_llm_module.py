# -*- coding: utf-8 -*-
"""将已安装的 vision_glm 从 addon 迁移为 LLM 驱动并清理旧页面入口。"""
import logging

from sqlalchemy import text
from core.plugin_manager import compare_versions

logger = logging.getLogger(__name__)

PLUGIN_NAME = "vision_glm"
LEGACY_PATH = "/admin/plugin/vision_glm/vision_glm"
TARGET_VERSION = "1.1.0"


async def probe(db) -> bool:
    """模块归类正确且版本不低于基线、旧入口已清理时视为完成。"""
    plugin_result = await db.execute(text(
        "SELECT module, version FROM hy_plugin WHERE name=:name"
    ), {"name": PLUGIN_NAME})
    plugin = plugin_result.first()
    if plugin is None:
        plugin_ready = True
    else:
        module, version = plugin[0], plugin[1]
        try:
            plugin_ready = (
                module == "llm"
                and compare_versions(str(version or ""), TARGET_VERSION) >= 0
            )
        except ValueError:
            plugin_ready = False
    nav_count = (await db.execute(text(
        "SELECT COUNT(*) FROM hy_nav WHERE plugin=:name OR path=:path"
    ), {"name": PLUGIN_NAME, "path": LEGACY_PATH})).scalar() or 0
    menu_count = (await db.execute(text(
        "SELECT COUNT(*) FROM hy_menu WHERE plugin=:name OR path=:path"
    ), {"name": PLUGIN_NAME, "path": LEGACY_PATH})).scalar() or 0
    return plugin_ready and nav_count == 0 and menu_count == 0


async def apply(db) -> None:
    """归类为 LLM 并清理旧入口，不覆盖已经更高的插件版本。"""
    plugin_result = await db.execute(text(
        "SELECT version FROM hy_plugin WHERE name=:name"
    ), {"name": PLUGIN_NAME})
    plugin = plugin_result.first()
    if plugin is not None:
        current_version = str(plugin[0] or "")
        try:
            target_version = (
                TARGET_VERSION
                if compare_versions(current_version, TARGET_VERSION) < 0
                else current_version
            )
        except ValueError:
            target_version = TARGET_VERSION
        await db.execute(text(
            "UPDATE hy_plugin SET module='llm', version=:version WHERE name=:name"
        ), {"name": PLUGIN_NAME, "version": target_version})
    nav_result = await db.execute(text(
        "DELETE FROM hy_nav WHERE plugin=:name OR path=:path"
    ), {"name": PLUGIN_NAME, "path": LEGACY_PATH})
    menu_result = await db.execute(text(
        "DELETE FROM hy_menu WHERE plugin=:name OR path=:path"
    ), {"name": PLUGIN_NAME, "path": LEGACY_PATH})
    logger.info(
        "[迁移] vision_glm 已转为 LLM 驱动，清理导航 %d 条、菜单 %d 条",
        nav_result.rowcount,
        menu_result.rowcount,
    )
