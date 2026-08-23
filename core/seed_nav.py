"""
系统页面注册种子数据
写入 hy_nav 表的页面注册层，幂等更新
"""
import logging

from sqlalchemy import select, delete

logger = logging.getLogger(__name__)


async def seed_nav(db):
    """
    写入系统预设页面到 hy_nav 表（页面注册层）

    幂等：已存在的 key+nav_type 不重复插入。
    插件页面由 PluginManager 安装时自动注册，此处仅写入系统预设页。
    """
    from core.db.nav import Nav

    # 后台系统页面
    admin_pages = [
        ("dashboard",   "控制台",     "/admin/dashboard",   "dashboard", "控制台"),
        ("plugin",      "应用列表",   "/admin/plugin",      "app",       "应用管理"),
        ("admin",       "管理员列表", "/admin/user",        "user",      "用户管理"),
        ("farmer",      "农户列表",   "/admin/farmer",      "user",      "用户管理"),
        ("certification","实名认证",  "/admin/certification","user-circle","用户管理"),
        ("log",         "系统日志",   "/admin/log",         "file",      "内容管理"),
        ("task_monitor","任务",       "/admin/task_monitor","chart",     "内容管理"),
        ("system",      "基本设置",   "/admin/system",      "setting",   "系统设置"),
        ("cache",       "缓存管理",   "/admin/cache",       "file",      "系统设置"),
        ("navigation",  "导航管理",   "/admin/navigation",  "menu",      "系统设置"),
        ("theme",       "主题设置",   "/admin/theme",       "palette",   "系统设置"),
        ("production-area", "产区列表", "/admin/production-area", "location", "产区管理"),
        ("planting-batch", "种植批次", "/admin/planting-batch", "list",   "产区管理"),
        ("area-binding", "农户绑定", "/admin/area-binding", "usergroup", "产区管理"),
        ("weather",      "天气服务", "/admin/weather",      "cloudy-day", "产区管理"),
        ("notice-sms",   "短信通知", "/admin/notice-sms",   "chat",       "通知管理"),
        ("notice-email", "邮件通知", "/admin/notice-email", "mail",       "通知管理"),
        ("notice-send",  "发送设置", "/admin/notice-send",  "setting",    "通知管理"),
        ("notice-log",   "通知日志", "/admin/notice-log",   "file",       "通知管理"),
        ("ai-chat",      "AI 对话",  "/admin/ai-chat",      "chat",       "AI 助手"),
        ("ai-setting",   "AI 设置",  "/admin/ai-setting",   "setting",    "AI 助手"),
    ]

    # 前台（农户端）系统页面
    frontend_pages = [
        ("home",    "首页",     "/farmer/home",    "home",         "农户端"),
        ("profile", "个人中心", "/farmer/profile", "user-setting", "农户端"),
        ("log",     "系统日志", "/farmer/log",     "file",         "系统管理"),
        ("cache",   "缓存管理", "/farmer/cache",   "file",         "系统管理"),
        ("system",  "系统信息", "/farmer/system",  "setting",      "系统管理"),
        ("production-area", "我的产区", "/farmer/production-area", "location", "农户端"),
        ("ai",      "AI 助手", "/farmer/ai",      "chat",         "农户端"),
    ]

    inserted = 0

    for key, title, path, icon, group in admin_pages:
        existing = (await db.execute(
            select(Nav).where(Nav.key == key, Nav.nav_type == "admin")
        )).scalar_one_or_none()
        if existing:
            existing.title = title
            existing.path = path
            existing.icon = icon
            existing.group_name = group
        else:
            db.add(Nav(
                key=key, title=title, path=path, icon=icon,
                nav_type="admin", source="system", plugin="",
                group_name=group, page_type="system",
            ))
            inserted += 1

    for key, title, path, icon, group in frontend_pages:
        existing = (await db.execute(
            select(Nav).where(Nav.key == key, Nav.nav_type == "frontend")
        )).scalar_one_or_none()
        if existing:
            existing.title = title
            existing.path = path
            existing.icon = icon
            existing.group_name = group
        else:
            db.add(Nav(
                key=key, title=title, path=path, icon=icon,
                nav_type="frontend", source="system", plugin="",
                group_name=group, page_type="system",
            ))
            inserted += 1

    # 清理已移除的 API规则页注册（用户反馈该页无实际用途，从导航中移除）
    stale_rule = (await db.execute(
        delete(Nav).where(Nav.key == "rule", Nav.nav_type == "admin", Nav.source == "system")
    )).rowcount
    if stale_rule:
        logger.info("[Nav种子] 清理已移除API规则页注册 %d 条", stale_rule)

    # 清理已移除的地块管理页注册（地块已并入产区详情页，不再作为独立页面注册）
    stale_nav = (await db.execute(
        delete(Nav).where(Nav.key == "plot", Nav.nav_type == "admin", Nav.source == "system")
    )).rowcount
    if stale_nav:
        logger.info("[Nav种子] 清理已移除地块页注册 %d 条", stale_nav)

    # 清理预警记录页注册（已并入天气服务页 Tab，不再作为独立页面注册）
    stale_weather_alert = (await db.execute(
        delete(Nav).where(Nav.key == "weather-alert", Nav.nav_type == "admin", Nav.source == "system")
    )).rowcount
    if stale_weather_alert:
        logger.info("[Nav种子] 清理已并入 Tab 的预警记录页注册 %d 条", stale_weather_alert)

    # 清理旧版通知页注册（已重构为 notice-sms / notice-email / notice-send 结构）
    stale_notice = (await db.execute(
        delete(Nav).where(
            Nav.key.in_([
                "notice-actions", "notice-sms-templates",
                "notice-email-templates", "notice-test"
            ]),
            Nav.nav_type == "admin", Nav.source == "system"
        )
    )).rowcount
    if stale_notice:
        logger.info("[Nav种子] 清理旧版通知页注册 %d 条", stale_notice)

    # 清理非 addon 插件在 hy_nav 中的残留页面注册（sms/mail 等服务型插件不应注册页面）
    from core.plugin_manager import PLUGIN_MODULES
    non_addon_modules = [m for m in PLUGIN_MODULES if m != "addon"]
    # 查找 source="plugin" 且 plugin 字段对应非 addon 模块的插件名
    from core.db.plugin import PluginModel
    non_addon_names_result = await db.execute(
        select(PluginModel.name).where(PluginModel.module.in_(non_addon_modules))
    )
    non_addon_names = [r[0] for r in non_addon_names_result.fetchall()]
    if non_addon_names:
        stale_plugin_nav = (await db.execute(
            delete(Nav).where(
                Nav.source == "plugin",
                Nav.plugin.in_(non_addon_names)
            )
        )).rowcount
        if stale_plugin_nav:
            logger.info("[Nav种子] 清理非 addon 插件页面注册 %d 条", stale_plugin_nav)

    # 清理推送插件已废弃的多页面注册（已合并为单页模式）
    stale_push = (await db.execute(
        delete(Nav).where(
            Nav.key.in_(["plugin_push_add", "plugin_push_detail"]),
            Nav.source == "plugin"
        )
    )).rowcount
    if stale_push:
        logger.info("[Nav种子] 清理推送插件旧页面注册 %d 条", stale_push)

    # 清理残留重复注册（非 system 来源但 key 与系统页面重复的条目，可能来自旧版插件或手动插入）
    system_admin_keys = [p[0] for p in admin_pages]
    system_frontend_keys = [p[0] for p in frontend_pages]
    stale_dup = (await db.execute(
        delete(Nav).where(
            Nav.source != "system",
            Nav.key.in_(system_admin_keys + system_frontend_keys)
        )
    )).rowcount
    if stale_dup:
        logger.info("[Nav种子] 清理非系统来源重复页面注册 %d 条", stale_dup)

    logger.info("[Nav种子] 新增 %d 个系统页面注册（幂等更新已有）", inserted)
