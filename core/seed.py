"""
种子数据模块
负责写入后台菜单、前台导航、系统配置等初始化数据
所有函数均设计为幂等操作，可安全重复执行
"""

import logging

from sqlalchemy import select
from sqlalchemy import delete, update

from core.db.menu import Menu
from core.db.configuration import ConfigurationModel

logger = logging.getLogger(__name__)

# 种子版本号：与 hy_configuration.seed_version 比对，一致时整体跳过种子流程
# 重要约定：任何人修改本模块/seed_nav/seed_notice 的种子内容后，必须递增此版本号，
# 否则新种子不会在存量环境生效（启动修复/新增项依赖种子流程重新执行）
SEED_VERSION = "19"
# 版本变更记录：
# - v10：菜单/导航/通知/权限码种子当前版本（站内信管理并入通知日志页 Tab、移除独立预警记录页等历史变更已固化）
# - v11：seed_notice 修复天气邮件通知默认启用问题
# - v12：新增平台敏感操作权限，并按现有页面权限为存量角色幂等继承
# - v14：后台默认主题切换为 vue_default，旧 default 主题迁移为 classic
# - v15：恢复 AgentScope 对话入口，使用新聊天页面，不恢复旧 AI 框架
# - v16：新增硬件设备管理页面、导航与细粒度权限
# - v17：新增任务监控日志清理权限
# - v19：新增插件数据库体检系统页面、菜单、权限和扫描配置


async def seed_menus(db):
    """
    写入后台菜单种子数据，并幂等更新已有菜单的标题和可见性。
    包含两步操作：
    1. 批量插入不存在的后台菜单记录
    2. 对已存在的菜单执行幂等更新（标题、可见性）
    """
    # ---- 第一步：批量写入后台菜单 ----
    # 字段顺序: (id, name, title, path, icon, parent_id, sort_order, plugin, visible)
    menus = [
        (1,  "dashboard",         "控制台",     "/admin/dashboard",    "dashboard",  0,   0,  "",  1),
        (2,  "system",            "系统设置",   "",                    "setting",    0,   1,  "",  1),
        (3,  "system_config",     "系统设置",   "/admin/system",      "setting",    2,   0,  "",  1),
        (4,  "cache",             "缓存管理",   "/admin/cache",       "file",       2,   1,  "",  0),
        (5,  "navigation",        "导航管理",   "/admin/navigation",  "menu",       2,   2,  "",  1),
        (17, "theme",             "主题设置",   "/admin/theme",       "palette",    2,   3,  "",  1),
        (37, "plugin_database",   "插件数据库体检", "/admin/plugin-database", "database", 2, 5, "", 1),
        (6,  "plugin",            "应用",       "",                    "app",        0,   2,  "",  1),
        (7,  "plugin_list",       "应用列表",   "/admin/plugin",      "app",        6,   0,  "",  1),
        (8,  "user",              "用户管理",   "",                    "user",       0,   3,  "",  1),
        (9,  "admin_list",        "管理员管理", "/admin/user",        "user",       2,   4,  "",  1),
        (10, "farmer",            "农户列表",   "/admin/farmer",      "user",       8,   1,  "",  1),
        (11, "certification",     "实名认证",   "/admin/certification","user-circle",8,   2,  "",  1),
        (15, "log",               "管理",       "",                    "file",       0,   5,  "",  1),
        (16, "system_log",        "系统日志",   "/admin/log",         "file",       15,  0,  "",  1),
        # ---- 产区管理（核心模块，plugin=""） ----
        (18, "production",         "产区管理",   "",                    "location",   0,   6,  "",  1),
        (19, "production_area",    "产区列表",   "/admin/production-area", "location", 18,  0,  "",  1),
        (36, "hardware_device",    "硬件管理",   "/admin/hardware-device", "control-platform", 18, 1, "", 1),
        (21, "planting_batch",     "种植批次",   "/admin/planting-batch", "list",    18,  2,  "",  1),
        (22, "area_binding",       "农户绑定",   "/admin/area-binding", "usergroup", 18,   3,  "",  1),
        (23, "weather",            "天气服务",   "/admin/weather",      "cloudy-day", 18,  4,  "",  1),
        # 预警记录已并入天气服务页作为 Tab（/admin/weather?tab=alerts），不再独立注册菜单
        # ---- 通知管理（核心模块，plugin=""） ----
        # 对标 ZJMF 系统接口：短信/邮件各独立为接口列表页，发送设置专注动作绑定
        (28, "notice",          "通知管理",   "",                      "notification", 0,   7,  "",  1),
        (29, "notice_sms",       "短信通知",   "/admin/notice-sms",     "chat",       28,  0,  "",  1),
        (30, "notice_email",     "邮件通知",   "/admin/notice-email",   "mail",       28,  1,  "",  1),
        (31, "notice_send",      "发送设置",   "/admin/notice-send",    "setting",    28,  2, "",  1),
        # 站内信管理已并入通知日志页 Tab（/admin/notice-log?tab=inbox），不再独立注册菜单
        # ---- AI 助手（核心模块，plugin=""；ID 33 起） ----
        (33, "ai",              "AI 助手",   "",                      "chat",       0,   8, "",  1),
        (34, "ai_chat",         "AI 对话",   "/admin/ai-chat",        "chat",       33,  0,  "",  1),
        (35, "ai_setting",      "AI 资源工作台",   "/admin/ai-setting",     "setting",    33,  1,  "",  1),
    ]

    inserted = 0
    for menu_id, name, title, path, icon, parent_id, sort_order, plugin, visible in menus:
        # 逐条检查是否已存在，仅插入不存在的记录
        existing = (await db.execute(
            select(Menu).where(Menu.id == menu_id)
        )).scalar_one_or_none()
        if not existing:
            db.add(Menu(
                id=menu_id, name=name, title=title, path=path, icon=icon,
                parent_id=parent_id, sort_order=sort_order, plugin=plugin,
                visible=visible, nav_type='admin', page_type='system'
            ))
            inserted += 1

    logger.info("[后台菜单种子] 新增 %d 条记录（跳过已存在）", inserted)

    # ---- 第二步：幂等更新已有菜单的标题和可见性 ----
    # 字段顺序: (id, title, visible)
    updates = [
        (3, "系统设置", 1),
        (4, "缓存管理", 0),
        (17, "主题设置", 1),
    ]

    for menu_id, title, visible in updates:
        await db.execute(
            update(Menu).where(Menu.id == menu_id).values(title=title, visible=visible)
        )

    logger.info("[后台菜单种子] 幂等更新 %d 条记录", len(updates))

    # 管理员管理归属调整：从“用户管理”移到“系统设置”
    await db.execute(
        update(Menu).where(Menu.id == 9).values(parent_id=2, sort_order=4)
    )

    # 清理已废弃的权限管理菜单（独立权限页面已移除，权限编辑集成在角色弹窗中）
    removed_perm = (await db.execute(
        delete(Menu).where(Menu.id.in_([12, 13]))
    )).rowcount
    if removed_perm:
        logger.info("[后台菜单种子] 清理废弃权限菜单 %d 条", removed_perm)

    # ---- 第三步：清理非系统/已移除页面记录 ----
    # 数据大屏(24)、溯源查询(25)不是系统/插件页面；地块管理(20)已并入产区详情页；
    # 站内信管理(32)已并入通知日志页 Tab，均从数据库删除
    removed = (await db.execute(
        delete(Menu).where(Menu.id.in_([20, 24, 25, 32]))
    )).rowcount
    if removed:
        logger.info("[后台菜单种子] 清理非系统/插件菜单 %d 条", removed)

    # 清理已废弃的模板测试菜单（已合并到短信/邮件模板管理的测试发送功能中）
    removed_notice_test = (await db.execute(
        delete(Menu).where(Menu.name == "notice_test")
    )).rowcount
    if removed_notice_test:
        logger.info("[后台菜单种子] 清理废弃模板测试菜单 %d 条", removed_notice_test)

    # 加固：地块管理页按标识删除（幂等、不依赖 id，兼容运行实例历史 id 漂移）
    removed_plot = (await db.execute(
        delete(Menu).where(Menu.name == "plot")
    )).rowcount
    removed_plot += (await db.execute(
        delete(Menu).where(Menu.path == "/admin/plot")
    )).rowcount
    if removed_plot:
        logger.info("[后台菜单种子] 按标识清理地块管理菜单 %d 条", removed_plot)

    # 清理旧版通知菜单（notice_actions / notice_sms_templates / notice_email_templates 已重构）
    removed_old_notice = (await db.execute(
        delete(Menu).where(Menu.name.in_([
            "notice_actions", "notice_sms_templates", "notice_email_templates"
        ]))
    )).rowcount
    if removed_old_notice:
        logger.info("[后台菜单种子] 清理旧版通知菜单 %d 条", removed_old_notice)

    # AgentScope 对话页只有一个系统入口。历史版本可能留下不同名称或漂移
    # ID 的重复记录，保留 canonical ID=34，按路径清理其余副本。
    removed_ai_duplicates = (await db.execute(
        delete(Menu).where(
            Menu.nav_type == "admin",
            Menu.path == "/admin/ai-chat",
            Menu.id != 34,
        )
    )).rowcount
    if removed_ai_duplicates:
        logger.info("[后台菜单种子] 清理重复 AgentScope 对话菜单 %d 条", removed_ai_duplicates)

    # 说明：前台导航（nav_type='frontend'）由管理员在导航管理中维护并持久化，
    # 不再在此处清理。前台可注册页面仅来源于插件 get_pages() 中声明为 frontend 的页面。


async def seed_farmer_menus(db):
    """
    写入农户端菜单种子数据（nav_type='frontend'，农户端即前台）。
    幂等设计：已存在的菜单不覆盖，仅插入缺失项。
    ID 范围：100~199（避免与后台菜单 ID 冲突）。
    兼容处理：若旧版本已用 nav_type='farmer' 写入，自动迁移为 'frontend'。
    """
    # ---- 迁移旧数据：nav_type='farmer' → 'frontend' ----
    migrated = (await db.execute(
        update(Menu).where(Menu.nav_type == 'farmer').values(nav_type='frontend')
    )).rowcount
    if migrated:
        logger.info("[农户菜单种子] 迁移 nav_type='farmer' → 'frontend' %d 条", migrated)

    # 字段顺序: (id, name, title, path, icon, parent_id, sort_order, plugin, visible)
    menus = [
        (100, "home",           "首页",     "/farmer/home",       "home",        0,   0,  "",  1),
        (101, "profile",        "个人中心", "/farmer/profile",    "user-setting", 0,   1,  "",  1),
        (110, "system_manage",  "系统管理", "",                    "setting",     0,   2,  "",  1),
        (111, "log",            "系统日志", "/farmer/log",        "file",        110, 0,  "",  1),
        (112, "cache",          "缓存管理", "/farmer/cache",      "file",        110, 1,  "",  1),
        (113, "system_info",    "系统信息", "/farmer/system",     "setting",     110, 2,  "",  1),
        (120, "production_area", "我的产区", "/farmer/production-area", "location",  0,   2,  "",  1),
        (122, "inbox",           "站内信",     "/farmer/inbox",           "notification", 0, 4, "",  1),
    ]

    inserted = 0
    for menu_id, name, title, path, icon, parent_id, sort_order, plugin, visible in menus:
        existing = (await db.execute(
            select(Menu).where(Menu.id == menu_id)
        )).scalar_one_or_none()
        if not existing:
            db.add(Menu(
                id=menu_id, name=name, title=title, path=path, icon=icon,
                parent_id=parent_id, sort_order=sort_order, plugin=plugin,
                visible=visible, nav_type='frontend', page_type='system'
            ))
            inserted += 1

    logger.info("[农户菜单种子] 新增 %d 条记录（跳过已存在）", inserted)

    # AgentScope 硬切后不再提供农户端旧 AI 对话入口；按名称和路径清理存量菜单，
    # 防止旧版本菜单记录继续出现在农户导航中。
    removed_old_ai = (await db.execute(
        delete(Menu).where(
            Menu.nav_type == "frontend",
            (Menu.name.in_(["ai", "ai_chat"]) | Menu.path.in_([
                "/farmer/ai", "/farmer/ai-chat"
            ])),
        )
    )).rowcount
    if removed_old_ai:
        logger.info("[农户菜单种子] 清理旧 AI 对话入口 %d 条", removed_old_ai)

async def seed_permissions(db):
    """
    根据已注册的后台页面（hy_menu 中 nav_type='admin' 的系统菜单）生成权限节点，
    使角色管理的“功能权限”目录树可对已注册页面进行访问控制（对标 ZJMF 页面权限）。

    幂等设计：以 code=page:<菜单标识> 为唯一键，已存在则更新标题/路径/层级，不存在则插入。
    同时清理已卸载插件遗留的权限节点（plugin 非空且不在已安装插件列表中）。
    """
    from core.db.permission import Permission, RolePermissionLink
    from core.db.plugin import PluginModel

    # 读取全部后台系统菜单（插件菜单权限由插件安装时单独注册，此处排除）
    result = await db.execute(
        select(Menu).where(Menu.nav_type == 'admin', Menu.plugin == '')
        .order_by(Menu.parent_id, Menu.sort_order)
    )
    menus = result.scalars().all()

    # 第一遍：upsert 每个菜单对应的权限节点，建立 菜单ID -> 权限ID 映射
    menu_to_perm = {}
    for m in menus:
        code = f"page:{m.name}"
        existing = (await db.execute(
            select(Permission).where(Permission.code == code)
        )).scalar_one_or_none()
        if existing:
            existing.title = m.title
            existing.url = m.path or ""
            existing.sort_order = m.sort_order
            existing.plugin = ""
            perm_id = existing.id
        else:
            perm = Permission(
                title=m.title, code=code, url=m.path or "",
                parent_id=0, sort_order=m.sort_order, plugin="",
                description="系统页面权限"
            )
            db.add(perm)
            await db.flush()
            perm_id = perm.id
        menu_to_perm[m.id] = perm_id

    # 第二遍：按菜单父子关系回填权限节点的 parent_id
    for m in menus:
        parent_perm_id = menu_to_perm.get(m.parent_id, 0) if m.parent_id else 0
        await db.execute(
            update(Permission).where(Permission.id == menu_to_perm[m.id])
            .values(parent_id=parent_perm_id)
        )

    logger.info("[权限种子] 已同步 %d 个页面权限节点", len(menus))

    # 清理已卸载插件遗留的权限节点（plugin 非空且不在已安装插件列表中）
    installed = {r[0] for r in (await db.execute(select(PluginModel.name))).all()}
    plugin_perms = (await db.execute(
        select(Permission).where(Permission.plugin != "")
    )).scalars().all()
    orphan_ids = [p.id for p in plugin_perms if p.plugin not in installed]
    if orphan_ids:
        await db.execute(
            delete(RolePermissionLink).where(RolePermissionLink.permission_id.in_(orphan_ids))
        )
        await db.execute(delete(Permission).where(Permission.id.in_(orphan_ids)))
        logger.info("[权限种子] 清理已卸载插件遗留权限 %d 个", len(orphan_ids))

    # 清理系统级 page: 孤儿：菜单已删但 page:<name> 权限节点仍在
    # （page: 命名空间由 seed 独占管理，无对应菜单的节点一律回收）
    valid_codes = {f"page:{m.name}" for m in menus}
    page_perms = (await db.execute(
        select(Permission).where(
            Permission.plugin == "", Permission.code.like("page:%")
        )
    )).scalars().all()
    stale_ids = [p.id for p in page_perms if p.code not in valid_codes]
    if stale_ids:
        await db.execute(
            delete(RolePermissionLink).where(RolePermissionLink.permission_id.in_(stale_ids))
        )
        await db.execute(delete(Permission).where(Permission.id.in_(stale_ids)))
        logger.info("[权限种子] 清理已删菜单遗留的 page 权限 %d 个", len(stale_ids))


async def seed_configuration(db):
    """
    写入系统配置种子数据（KV 键值对）。
    幂等设计：已存在的 key 不覆盖，仅插入缺失的配置项。
    """
    # 字段顺序: (key, (value, description))
    defaults = {
        "site_name": ("慧眼护农", "网站名称"),
        "site_subtitle": ("智慧农业业务系统", "网站副标题"),
        "site_logo": ("/static/img/logo.png", "网站 Logo URL"),
        "site_favicon": ("/static/img/favicon.png", "网站 Favicon URL"),
        "site_logo_url": ("", "Logo 点击跳转地址（为空则不可点击）"),
        "site_logo_target": ("_self", "Logo 跳转方式（_self=当前页面, _blank=新页面）"),
        "site_keywords": ("慧眼护农,智慧农业,农场管理", "网站 SEO 关键词"),
        "site_description": (
            "慧眼护农-智慧农业业务系统，提供农场管理、设备监控、虫害识别等服务",
            "网站 SEO 描述"
        ),
        "site_domain": ("", "网站域名地址"),
        "site_maintenance": ("0", "维护模式（0=关闭, 1=开启）"),
        "maintenance_message": ("系统维护中，请稍后访问", "维护模式提示语"),
        "record_number": ("", "备案号"),
        "copyright": ("Copyright @ 2025-2026 慧眼护农", "底部版权信息"),
        "start_id_admin": ("1", "管理员起始 ID"),
        "start_id_farmer": ("1", "农户起始 ID"),
        "page_size_default": ("20", "默认列表分页大小"),
        "page_size_max": ("100", "最大分页大小"),
        "login_session_duration": ("7200", "登录会话时长（秒）"),
        "login_retry_limit": ("5", "密码重试次数限制"),
        "login_lock_duration": ("900", "账户锁定时间（秒）"),
        "session_concurrent": ("1", "允许多端同时登录（0=否, 1=是）"),
        "force_password_complexity": ("1", "强制密码复杂度（0=否, 1=是）"),
        "admin_enforce_safe": ("0", "管理员强制安全验证（0=否, 1=是）"),
        "ip_whitelist_enabled": ("0", "IP 白名单开关（0=关闭, 1=开启）"),
        "ip_whitelist": ("", "后台访问白名单 IP（多个以换行分隔）"),
        # ===== 前台（农户）访问设置 =====
        "allow_farmer_register": ("1", "是否允许农户注册（0=否, 1=是）"),
        "farmer_email_suffix_enabled": ("0", "是否限制农户注册邮箱后缀（0=否, 1=是）"),
        "farmer_email_suffixes": ("", "允许的邮箱后缀（多个以逗号分隔，如 @qq.com,@163.com）"),
        "farmer_session_duration": ("7200", "农户登录会话时长（秒）"),
        "farmer_register_phone_required": ("0", "农户注册手机号是否必填（0=选填, 1=必填）"),
        "farmer_register_email_required": ("0", "农户注册邮箱是否必填（0=选填, 1=必填）"),
        "farmer_service_agreement_url": ("", "农户服务协议链接（登录/注册页展示，留空则不显示）"),
        "farmer_privacy_policy_url": ("", "农户隐私协议链接（登录/注册页展示，留空则不显示）"),
        # -- 验证码登录 / 注册方式控制（对标 ZJMF 访问设置） --
        "farmer_allow_phone_register": ("1", "是否允许手机号注册（0=否, 1=是）"),
        "farmer_phone_register_verify": ("1", "手机注册时是否需要验证码（0=否, 1=是）"),
        "farmer_phone_password_login": ("1", "是否开启手机号密码登录（0=否, 1=是）"),
        "farmer_phone_sms_login": ("1", "是否开启手机短信验证码登录（0=否, 1=是）"),
        "farmer_email_code_login": ("1", "是否开启邮箱验证码登录（0=否, 1=是）"),
        "farmer_allow_email_register": ("1", "是否允许邮箱注册（0=否, 1=是）"),
        "farmer_email_register_verify": ("1", "邮箱注册时是否需要验证码（0=否, 1=是）"),
        "farmer_email_password_login": ("1", "是否允许邮箱密码登录（0=否, 1=是）"),
        "farmer_show_register_switch": ("1", "登录注册页面展示跳转按钮（0=否, 1=是）"),
        "farmer_default_login_method": ("password", "首选登录方式（password=密码登录, code=验证码登录）"),
        "farmer_default_password_type": ("phone", "密码登录首选凭证（phone=手机号, email=邮箱）"),
        # -- 验证码参数 --
        "sms_code_expire": ("300", "验证码有效期（秒）"),
        "sms_code_length": ("6", "验证码位数"),
        "sms_code_interval": ("60", "验证码发送间隔（秒）"),
        # ===== 模板主题设置 =====
        "site_theme": ("default", "官网当前启用主题（templates/site/ 下的主题目录名）"),
        "admin_theme": ("vue_default", "后台当前启用主题（templates/admin/ 下的主题目录名）"),
        "farmer_theme": ("default", "农户端当前启用主题（templates/farmer/ 下的主题目录名）"),
        # ===== 官网主题控制器配置 =====
        "site_nav": (
            '[{"label":"首页","href":"/","target":"_self"},'
            '{"label":"功能介绍","href":"#features","target":"_self"},'
            '{"label":"农户入口","href":"/farmer/home","target":"_self"}]',
            "官网顶部导航菜单（JSON 数组）",
        ),
        "site_footer": (
            '[{"title":"产品","links":[{"label":"功能介绍","href":"#features"}]},'
            '{"title":"支持","links":[{"label":"联系我们","href":"mailto:1910442675@qq.com"}]}]',
            "官网页脚分组（JSON 数组）",
        ),
        "site_banner": (
            '{"title":"慧眼护农","subtitle":"基于 AI 的智慧农业病虫害诊断平台","background":""}',
            "官网 Banner（JSON 对象：title / subtitle / background）",
        ),
        "site_intro": (
            "慧眼护农是面向智慧农业的一体化业务系统，提供病虫害识别、智能诊断、"
            "农事建议、设备监控等核心能力，帮助农户降本增效。",
            "官网功能简介文案（纯文本 / Markdown 子集）",
        ),
        # ===== 上传限制配置 =====
        "upload_image_max_size": ("2", "图片上传最大大小（MB）"),
        "upload_image_extensions": ("jpg,jpeg,png,gif,webp", "允许的图片格式（逗号分隔）"),
        "upload_file_max_size": ("10", "通用文件上传最大大小（MB）"),
        "upload_file_extensions": (
            "pdf,doc,docx,xls,xlsx,csv,txt,json,zip,rar",
            "允许的文件格式（逗号分隔）",
        ),
        # ===== 实名认证配置 =====
        "cert_enabled": ("1", "实名认证开关（0=关闭, 1=开启）"),
        "cert_auto_update_name": ("1", "实名通过后自动更新姓名（0=否, 1=是）"),
        "cert_show_id": ("1", "会员中心展示认证ID（0=否, 1=是）"),
        "cert_manual_review": ("0", "人工复审（0=否, 1=是，第三方认证通过后需后台审批）"),
        "cert_notify_user": ("0", "审批通过后通知用户（0=否, 1=是）"),
        "cert_upload_image": ("0", "提交资料时需上传图片（0=否, 1=是）"),
        "cert_phone_match": ("0", "手机一致性校验（0=否, 1=是，注册手机号需与实名手机号一致）"),
        # ===== 产区管理 / 高德地图配置（分组 map，密钥默认空，不入版本库） =====
        "amap_web_key": ("", "高德 Web JS API Key（管理员在产区管理→地图设置填入）"),
        "amap_js_security_code": ("", "高德安全密钥 jscode（管理员在产区管理→地图设置填入）"),
        "amap_web_service_key": ("", "高德 Web 服务 Key（绑定页静态地图缩略图用，与 JS API Key 不同）"),
        "production_area_map_enabled": ("1", "产区地图选址开关（0=关闭仅手动填写, 1=开启地图选址）"),
        # ===== 天气服务配置（数据源插件配置由插件安装时写入，此处仅全局设置） =====
        "weather_enabled": ("0", "天气拉取总开关（0=关闭, 1=开启）"),
        "weather_source": ("", "全局默认天气数据源插件名（weather_qweather/weather_amap）"),
        "weather_interval_minutes": ("15", "天气拉取频率（分钟，下限10分钟保护配额）"),
        "weather_gdd_base_temp": ("10", "积温基点温度（℃，活动/有效积温计算用）"),
        "weather_daily_retention_days": ("730", "逐日天气历史保留天数（积温数据基础，下限365天）"),
        "weather_alert_retention_days": ("90", "过期气象预警保留天数（仅清已失效预警，下限30天）"),
        # ===== 系统日志自动清理（对齐 ZJMF cron_system_log_delete 方案） =====
        "system_log_delete_switch": ("1", "系统日志自动删除开关（0=关闭, 1=开启）"),
        "system_log_delete_days": ("90", "系统日志保留天数（每日3:00自动硬删更早记录）"),
        # ===== 任务队列配置（对齐 ZJMF task_wait 方案，纯 MySQL 无需 Redis） =====
        "task_queue_enabled": ("1", "任务队列总开关（0=关闭, 1=开启）"),
        "task_queue_retry_times": ("3", "任务失败默认最大重试次数"),
        "task_queue_poll_interval": ("3", "Worker 轮询间隔（秒）"),
        "task_queue_batch_size": ("10", "每批处理任务数量"),
        "task_queue_clean_finish": ("1", "完成后自动删除（0=保留, 1=自动删除）"),
        # ===== 插件数据库体检 =====
        "plugin_database_scan_enabled": ("1", "插件数据库体检定时扫描开关（0=关闭, 1=开启）"),
        "plugin_database_scan_interval_hours": ("6", "插件数据库体检周期（小时，仅允许1/6/12/24）"),
        # ===== 对象存储配置 =====
        "oss_method": ("local_oss", "对象存储方式（默认本地存储 local_oss）"),
    }

    inserted = 0
    for key, (value, description) in defaults.items():
        # 逐条检查 key 是否已存在，仅插入缺失项
        existing = (await db.execute(
            select(ConfigurationModel).where(ConfigurationModel.key == key)
        )).scalar_one_or_none()
        if key == "admin_theme" and existing and existing.value == "default":
            # 旧版本 default 指向原管理端主题；该主题现以 classic 标识保留，
            # 存量配置必须只迁移这个历史值，不能覆盖管理员已选择的其他主题。
            existing.value = value
            existing.description = description
            inserted += 1
        elif not existing:
            db.add(ConfigurationModel(key=key, value=value, description=description))
            inserted += 1

    logger.info("[配置种子] 新增 %d 条配置项（跳过已存在）", inserted)

    # 清理已废弃的配置项：后台界面语言
    from sqlalchemy import delete as _delete
    removed_config = (await db.execute(
        _delete(ConfigurationModel).where(ConfigurationModel.key == 'site_language')
    )).rowcount
    if removed_config:
        logger.info("[配置种子] 清理废弃配置项 site_language %d 条", removed_config)

