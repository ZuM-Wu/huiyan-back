"""
CRUD 权限种子模块
从 core/seed.py 拆分：注册核心模块的 CRUD 权限码（作为页面权限的子节点）。
所有函数均设计为幂等操作，可安全重复执行。

拆分约定：本模块种子内容变更后，同样需递增 core/seed.py 的 SEED_VERSION。
"""

import logging

from sqlalchemy import select, delete

from core.db.menu import Menu

logger = logging.getLogger(__name__)


async def seed_crud_permissions(db):
    """
    注册核心模块的 CRUD 权限码（作为页面权限的子节点）。
    幂等设计：以 code 为唯一键，已存在则跳过。

    权限码格式: {module}:{action}
    - module: admin / farmer / role / permission
    - action: list / create / update / delete / status
    """
    from core.db.permission import Permission, RolePermissionLink

    # 查找页面权限的 ID 作为 parent_id
    async def _page_perm_id(code: str) -> int:
        row = (await db.execute(
            select(Permission.id).where(Permission.code == code)
        )).first()
        return row[0] if row else 0

    admin_list_pid = await _page_perm_id("page:admin_list")
    farmer_pid = await _page_perm_id("page:farmer")
    cert_pid = await _page_perm_id("page:certification")
    area_pid = await _page_perm_id("page:production_area")
    # 地块管理已并入产区详情页，无独立菜单/page 权限；地块 CRUD 权限挂到产区列表页下
    plot_pid = area_pid
    batch_pid = await _page_perm_id("page:planting_batch")
    notice_pid = await _page_perm_id("page:notice")
    weather_pid = await _page_perm_id("page:weather")
    ai_chat_pid = await _page_perm_id("page:ai_chat")
    ai_setting_pid = await _page_perm_id("page:ai_setting")
    inbox_pid = await _page_perm_id("page:inbox")

    # CRUD 权限码定义: (code, title, parent_id, sort_order)
    crud_perms = [
        # ---- 管理员管理 ----
        ("admin:list",       "管理员-查看",   admin_list_pid, 0),
        ("admin:create",     "管理员-新增",   admin_list_pid, 1),
        ("admin:update",     "管理员-编辑",   admin_list_pid, 2),
        ("admin:delete",     "管理员-删除",   admin_list_pid, 3),
        ("admin:status",     "管理员-状态切换", admin_list_pid, 4),
        # ---- 角色管理（同属用户管理页面） ----
        ("role:list",        "角色-查看",     admin_list_pid, 10),
        ("role:create",      "角色-新增",     admin_list_pid, 11),
        ("role:update",      "角色-编辑",     admin_list_pid, 12),
        ("role:delete",      "角色-删除",     admin_list_pid, 13),
        # ---- 农户管理 ----
        ("farmer:list",      "农户-查看",     farmer_pid, 0),
        ("farmer:create",    "农户-新增",     farmer_pid, 1),
        ("farmer:update",    "农户-编辑",     farmer_pid, 2),
        ("farmer:delete",    "农户-删除",     farmer_pid, 3),
        ("farmer:status",    "农户-状态切换", farmer_pid, 4),
        # ---- 实名认证 ----
        ("cert:list",        "实名认证-查看",   cert_pid, 0),
        ("cert:review",      "实名认证-审批",   cert_pid, 1),
        ("cert:config",      "实名认证-配置",   cert_pid, 2),
        ("cert:channel",     "实名认证-接口管理", cert_pid, 3),
        # ---- 权限管理 ----
        ("permission:list",    "权限-查看",   0, 0),
        ("permission:create",  "权限-新增",   0, 1),
        ("permission:update",  "权限-编辑",   0, 2),
        ("permission:delete",  "权限-删除",   0, 3),
        # ---- 产区管理 ----
        ("area:list",        "产区-查看",     area_pid, 0),
        ("area:create",      "产区-新增",     area_pid, 1),
        ("area:update",      "产区-编辑",     area_pid, 2),
        ("area:delete",      "产区-删除",     area_pid, 3),
        ("area:status",      "产区-状态切换", area_pid, 4),
        ("area:map_config",  "产区-地图设置", area_pid, 5),
        # ---- 地块管理 ----
        ("plot:list",        "地块-查看",     plot_pid, 0),
        ("plot:create",      "地块-新增",     plot_pid, 1),
        ("plot:update",      "地块-编辑",     plot_pid, 2),
        ("plot:delete",      "地块-删除",     plot_pid, 3),
        # ---- 种植批次 ----
        ("batch:list",       "批次-查看",     batch_pid, 0),
        ("batch:create",     "批次-新增",     batch_pid, 1),
        ("batch:update",     "批次-编辑",     batch_pid, 2),
        ("batch:delete",     "批次-删除",     batch_pid, 3),
        ("batch:status",     "批次-状态切换", batch_pid, 4),
        # ---- 通知管理 ----
        ("notice:list",     "通知-查看",      notice_pid, 0),
        ("notice:create",   "通知-新增",      notice_pid, 1),
        ("notice:update",   "通知-编辑",      notice_pid, 2),
        ("notice:delete",   "通知-删除",      notice_pid, 3),
        ("notice:test",     "通知-测试发送",   notice_pid, 4),
        ("notice:config",   "通知-接口管理",   notice_pid, 5),
        ("notice:send",     "通知-发送设置",   notice_pid, 6),
        # ---- 天气服务 ----
        ("weather:view",    "天气-查看",      weather_pid, 0),
        ("weather:config",  "天气-配置管理",   weather_pid, 1),
        # ---- AI 助手 ----
        ("ai:chat",         "AI-对话",        ai_chat_pid, 0),
        ("ai:setting",      "AI-设置",        ai_setting_pid, 0),
        # ---- 站内信管理 ----
        ("inbox:list",      "站内信-查看",    inbox_pid, 0),
        ("inbox:delete",    "站内信-删除",    inbox_pid, 1),
    ]

    # 平台级敏感写操作：页面权限保留为父节点，存量角色按父页面权限继承。
    sensitive_perms = [
        ("plugin:install", "插件-安装", "page:plugin_list", 1),
        ("plugin:uninstall", "插件-卸载", "page:plugin_list", 2),
        ("plugin:status", "插件-启停", "page:plugin_list", 3),
        ("plugin:upgrade", "插件-升级", "page:plugin_list", 4),
        ("plugin:config", "插件-配置", "page:plugin_list", 5),
        ("cache:clear", "缓存-清理", "page:cache", 1),
        ("config:update", "系统配置-修改", "page:system", 1),
        ("menu:create", "菜单-新增", "page:navigation", 1),
        ("menu:update", "菜单-编辑", "page:navigation", 2),
        ("menu:delete", "菜单-删除", "page:navigation", 3),
        ("menu:reorder", "菜单-排序", "page:navigation", 4),
        ("site:update", "官网配置-修改", "page:navigation", 5),
        ("task_monitor:retry", "任务监控-重试", "page:task_monitor", 1),
        ("task_monitor:handle", "任务监控-标记处理", "page:task_monitor", 2),
        ("task_monitor:ignore", "任务监控-忽略", "page:task_monitor", 3),
        ("task_queue:config", "任务队列-配置", "page:task_monitor", 4),
        ("task_queue:retry", "任务队列-重试", "page:task_monitor", 5),
        ("task_queue:cancel", "任务队列-取消", "page:task_monitor", 6),
        ("task_queue:resume", "任务队列-恢复暂停", "page:task_monitor", 7),
        ("task_queue:replay", "任务队列-事件重放", "page:task_monitor", 8),
        ("oss:test", "对象存储-连通检测", "page:system", 1),
        ("oss:switch", "对象存储-切换", "page:system", 2),
        ("theme:activate", "主题-启用", "page:theme", 1),
        ("upload:settings", "上传-配置", "page:system", 3),
    ]
    for code, title, page_code, sort_order in sensitive_perms:
        parent_id = await _page_perm_id(page_code)
        crud_perms.append((code, title, parent_id, sort_order))

    inserted = 0
    for code, title, parent_id, sort_order in crud_perms:
        existing = (await db.execute(
            select(Permission).where(Permission.code == code)
        )).scalar_one_or_none()
        if existing:
            # 幂等更新标题和父级
            existing.title = title
            existing.parent_id = parent_id
            existing.sort_order = sort_order
            existing.plugin = ""
        else:
            perm = Permission(
                title=title, code=code, url="",
                parent_id=parent_id, sort_order=sort_order,
                plugin="", description="系统CRUD权限"
            )
            db.add(perm)
            inserted += 1

    logger.info("[CRUD权限种子] 新增 %d 个权限码（幂等更新已有）", inserted)

    # 新增操作权限按父页面权限自动继承，避免升级后存量角色突然失去既有能力。
    for code, _title, page_code, _sort_order in sensitive_perms:
        parent_row = (await db.execute(
            select(Permission.id).where(Permission.code == page_code)
        )).first()
        action_row = (await db.execute(
            select(Permission.id).where(Permission.code == code)
        )).first()
        if not parent_row or not action_row:
            continue
        role_ids = (await db.execute(
            select(RolePermissionLink.role_id).where(
                RolePermissionLink.permission_id == parent_row[0]
            )
        )).scalars().all()
        existing_links = set((await db.execute(
            select(RolePermissionLink.role_id).where(
                RolePermissionLink.permission_id == action_row[0],
                RolePermissionLink.role_id.in_(role_ids or [-1]),
            )
        )).scalars().all())
        for role_id in role_ids:
            if role_id not in existing_links:
                db.add(RolePermissionLink(role_id=role_id, permission_id=action_row[0]))

    # 清理已移除的 API规则 相关数据（hy_api_rule 机制已废弃，权限由路由级 require_permission 控制）
    stale_rule_codes = ['page:api_rules', 'rule:list', 'rule:create', 'rule:update', 'rule:delete', 'permission:rule']
    stale_rule_perms = (await db.execute(
        select(Permission.id).where(Permission.code.in_(stale_rule_codes))
    )).scalars().all()
    if stale_rule_perms:
        await db.execute(
            delete(RolePermissionLink).where(RolePermissionLink.permission_id.in_(stale_rule_perms))
        )
        await db.execute(delete(Permission).where(Permission.id.in_(stale_rule_perms)))
        logger.info("[CRUD权限种子] 清理已废弃 API规则权限节点 %d 个", len(stale_rule_perms))

    # 清理已移除的 API规则 菜单记录
    stale_rule_menus = (await db.execute(
        select(Menu.id).where(Menu.name.in_(['api_rules', 'permission_rule']))
    )).scalars().all()
    if stale_rule_menus:
        await db.execute(delete(Menu).where(Menu.id.in_(stale_rule_menus)))
        logger.info("[CRUD权限种子] 清理已废弃 API规则菜单 %d 条", len(stale_rule_menus))
