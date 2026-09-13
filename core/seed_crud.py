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


CORE_CRUD_PERMISSIONS = [
    # ---- 管理员管理 ----
    ("admin:list",       "管理员-查看",   'page:admin_list', 0),
    ("admin:create",     "管理员-新增",   'page:admin_list', 1),
    ("admin:update",     "管理员-编辑",   'page:admin_list', 2),
    ("admin:delete",     "管理员-删除",   'page:admin_list', 3),
    ("admin:status",     "管理员-状态切换", 'page:admin_list', 4),
    # ---- 角色管理（同属用户管理页面） ----
    ("role:list",        "角色-查看",     'page:admin_list', 10),
    ("role:create",      "角色-新增",     'page:admin_list', 11),
    ("role:update",      "角色-编辑",     'page:admin_list', 12),
    ("role:delete",      "角色-删除",     'page:admin_list', 13),
    # ---- 农户管理 ----
    ("farmer:list",      "农户-查看",     'page:farmer', 0),
    ("farmer:create",    "农户-新增",     'page:farmer', 1),
    ("farmer:update",    "农户-编辑",     'page:farmer', 2),
    ("farmer:delete",    "农户-删除",     'page:farmer', 3),
    ("farmer:status",    "农户-状态切换", 'page:farmer', 4),
    # ---- 实名认证 ----
    ("cert:list",        "实名认证-查看",   'page:certification', 0),
    ("cert:review",      "实名认证-审批",   'page:certification', 1),
    ("cert:config",      "实名认证-配置",   'page:certification', 2),
    ("cert:channel",     "实名认证-接口管理", 'page:certification', 3),
    # ---- 权限管理 ----
    ("permission:list",    "权限-查看",   "", 0),
    ("permission:create",  "权限-新增",   "", 1),
    ("permission:update",  "权限-编辑",   "", 2),
    ("permission:delete",  "权限-删除",   "", 3),
    # ---- 产区管理 ----
    ("area:list",        "产区-查看",     'page:production_area', 0),
    ("area:create",      "产区-新增",     'page:production_area', 1),
    ("area:update",      "产区-编辑",     'page:production_area', 2),
    ("area:delete",      "产区-删除",     'page:production_area', 3),
    ("area:status",      "产区-状态切换", 'page:production_area', 4),
    ("area:map_config",  "产区-地图设置", 'page:production_area', 5),
    # ---- 地块管理 ----
    ("plot:list",        "地块-查看",     'page:production_area', 0),
    ("plot:create",      "地块-新增",     'page:production_area', 1),
    ("plot:update",      "地块-编辑",     'page:production_area', 2),
    ("plot:delete",      "地块-删除",     'page:production_area', 3),
    # ---- 种植批次 ----
    ("batch:list",       "批次-查看",     'page:planting_batch', 0),
    ("batch:create",     "批次-新增",     'page:planting_batch', 1),
    ("batch:update",     "批次-编辑",     'page:planting_batch', 2),
    ("batch:delete",     "批次-删除",     'page:planting_batch', 3),
    ("batch:status",     "批次-状态切换", 'page:planting_batch', 4),
    # ---- 通知管理 ----
    ("notice:list",     "通知-查看",      'page:notice', 0),
    ("notice:create",   "通知-新增",      'page:notice', 1),
    ("notice:update",   "通知-编辑",      'page:notice', 2),
    ("notice:delete",   "通知-删除",      'page:notice', 3),
    ("notice:test",     "通知-测试发送",   'page:notice', 4),
    ("notice:config",   "通知-接口管理",   'page:notice', 5),
    ("notice:send",     "通知-发送设置",   'page:notice', 6),
    # ---- 天气服务 ----
    ("weather:view",    "天气-查看",      'page:weather', 0),
    ("weather:config",  "天气-配置管理",   'page:weather', 1),
    # ---- AI 助手 ----
    ("ai:setting",      "AI-资源工作台",        'page:ai_setting', 0),
    # ---- 站内信管理 ----
    ("inbox:list",      "站内信-查看",    'page:inbox', 0),
    ("inbox:delete",    "站内信-删除",    'page:inbox', 1),
]

SENSITIVE_PERMISSIONS = [
    ("plugin:install", "插件-安装", "page:plugin_list", 1),
    ("plugin:uninstall", "插件-卸载", "page:plugin_list", 2),
    ("plugin:status", "插件-启停", "page:plugin_list", 3),
    ("plugin:upgrade", "插件-升级", "page:plugin_list", 4),
    ("plugin:config", "插件-配置", "page:plugin_list", 5),
    ("plugin_database:list", "插件数据库-查看", "page:plugin_database", 6),
    ("plugin_database:scan", "插件数据库-扫描", "page:plugin_database", 7),
    ("plugin_database:repair", "插件数据库-修复", "page:plugin_database", 8),
    ("plugin_database:config", "插件数据库-配置", "page:plugin_database", 9),
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
    ("task_monitor:clear_logs", "任务监控-清理日志", "page:task_monitor", 4),
    ("task_queue:config", "任务队列-配置", "page:task_monitor", 4),
    ("task_queue:retry", "任务队列-重试", "page:task_monitor", 5),
    ("task_queue:cancel", "任务队列-取消", "page:task_monitor", 6),
    ("task_queue:resume", "任务队列-恢复暂停", "page:task_monitor", 7),
    ("task_queue:replay", "任务队列-事件重放", "page:task_monitor", 8),
    ("oss:test", "对象存储-连通检测", "page:system", 1),
    ("oss:switch", "对象存储-切换", "page:system", 2),
    ("oss:migrate", "对象存储-迁移公共文件", "page:system", 3),
    ("theme:activate", "主题-启用", "page:theme", 1),
    ("upload:settings", "上传-配置", "page:system", 3),
    ("hardware:list", "硬件-查看", "page:hardware_device", 0),
    ("hardware:sync", "硬件-同步", "page:hardware_device", 1),
    ("hardware:update", "硬件-图片设置", "page:hardware_device", 2),
    ("hardware:bind", "硬件-地块绑定", "page:hardware_device", 3),
    ("hardware:data", "硬件-监测数据", "page:hardware_device", 4),
]

def core_permission_codes() -> set[str]:
    """返回数据库种子共用的核心操作权限码，供能力注册阶段验证声明。"""
    return {str(item[0]) for item in CORE_CRUD_PERMISSIONS + SENSITIVE_PERMISSIONS}


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

    production_group_pid = await _page_perm_id("page:production")
    hardware_pid = await _page_perm_id("page:hardware_device")

    # CRUD 权限码定义: (code, title, parent_id, sort_order)
    sensitive_perms = SENSITIVE_PERMISSIONS
    crud_perms = []
    for code, title, page_code, sort_order in CORE_CRUD_PERMISSIONS + SENSITIVE_PERMISSIONS:
        parent_id = await _page_perm_id(page_code) if page_code else 0
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

    # 新增硬件页继承“产区管理”分组的存量角色授权，再由下方逻辑继承操作权限。
    production_role_ids = (await db.execute(
        select(RolePermissionLink.role_id).where(
            RolePermissionLink.permission_id == production_group_pid
        )
    )).scalars().all()
    hardware_role_ids = set((await db.execute(
        select(RolePermissionLink.role_id).where(
            RolePermissionLink.permission_id == hardware_pid,
            RolePermissionLink.role_id.in_(production_role_ids or [-1]),
        )
    )).scalars().all())
    for role_id in production_role_ids:
        if role_id not in hardware_role_ids:
            db.add(RolePermissionLink(role_id=role_id, permission_id=hardware_pid))
    await db.flush()

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

    stale_ai_chat = (await db.execute(
        select(Permission.id).where(Permission.code.in_([
            "page:ai_chat", "ai:chat"
        ]))
    )).scalars().all()
    if stale_ai_chat:
        await db.execute(delete(RolePermissionLink).where(
            RolePermissionLink.permission_id.in_(stale_ai_chat)
        ))
        await db.execute(delete(Permission).where(Permission.id.in_(stale_ai_chat)))
        logger.info("[CRUD权限种子] 清理旧 AI 对话权限节点 %d 个", len(stale_ai_chat))
