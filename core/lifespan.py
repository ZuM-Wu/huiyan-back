"""
生命周期管理
12步启动流程
"""

import logging

from sqlalchemy import select, text

from core.db.base import engine, Base, async_session_factory
from core.db.plugin import PluginModel
from core.db.admin import Admin, AdminRole, AdminRoleLink
# 以下导入用于触发 SQLAlchemy Base.metadata 注册（原在 main.py 中）
import core.db.configuration
import core.db.farmer
import core.db.permission
import core.db.system_log     # hy_system_log
import core.db.menu           # hy_menu
import core.db.nav            # hy_nav（页面注册表）
import core.db.widget          # hy_admin_widget（挂件配置表）
import core.db.production_area  # hy_production_area/hy_plot/hy_planting_batch
import core.db.weather           # hy_weather_data/hy_weather_daily/hy_weather_area_binding/hy_weather_alert
import core.db.verify_code       # hy_verify_code（验证码表）
import core.db.task_queue        # hy_task_queue（任务队列表）
import core.db.task_log          # hy_task_log（任务执行日志表）
import core.db.event_outbox      # hy_event_outbox（可靠事件Outbox）
import core.db.file_log          # hy_file_log（文件存储日志表）
import core.db.api_key           # hy_api_key（个人API密钥表，MCP鉴权）
import core.db.ai                # noqa: F401  hy_ai_*（AI对话会话/消息/技能/外部MCP服务器）
from core.config import settings
from core.plugin_manager import PluginManager
from core.api_router import APIRouterManager

logger = logging.getLogger(__name__)

# 内置默认 JWT 密钥集合（与 core/config.py 中的默认值保持一致）
# 非调试模式下检测到仍在使用默认密钥时拒绝启动，防止生产环境令牌可伪造
_INSECURE_JWT_DEFAULTS = {"hy_admin_jwt_secret_2026_v4", "hy_farmer_jwt_secret_2026_v4"}


def _check_jwt_secrets():
    """
    JWT 密钥安全校验

    - APP_DEBUG=False（生产模式）且任一 JWT 密钥仍为内置默认值时，直接抛错拒绝启动
    - APP_DEBUG=True（调试模式）仅打印警告，不影响本地开发
    """
    using_default = (
        settings.JWT_KEY_ADMIN in _INSECURE_JWT_DEFAULTS
        or settings.JWT_KEY_FARMER in _INSECURE_JWT_DEFAULTS
    )
    if not using_default:
        return
    if settings.APP_DEBUG:
        logger.warning("[安全提示] JWT 密钥仍为内置默认值，生产部署前必须在 .env 中修改 JWT_KEY_ADMIN / JWT_KEY_FARMER")
    else:
        raise RuntimeError(
            "检测到生产模式(APP_DEBUG=False)下 JWT 密钥仍为内置默认值，"
            "存在令牌伪造风险，请在 .env 中设置 JWT_KEY_ADMIN / JWT_KEY_FARMER 后重启"
        )


async def _run_field_migrations():
    """幂等字段迁移（每次启动检查并补齐缺失字段）"""
    async with async_session_factory() as db:
        # hy_plugin.author
        result = await db.execute(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_plugin' AND COLUMN_NAME='author'"
        ))
        if not result.scalar():
            await db.execute(text(
                "ALTER TABLE hy_plugin ADD COLUMN author VARCHAR(128) DEFAULT '' "
                "COMMENT '开发者' AFTER version"
            ))
            await db.commit()
            logger.info("[迁移] hy_plugin.author 字段已添加")

        # hy_configuration.group_name
        result = await db.execute(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_configuration' AND COLUMN_NAME='group_name'"
        ))
        if not result.scalar():
            await db.execute(text(
                "ALTER TABLE hy_configuration ADD COLUMN group_name VARCHAR(64) DEFAULT 'basic' "
                "COMMENT '配置分组：basic/security/access/plugin 等' AFTER description"
            ))
            await db.commit()
            logger.info("[迁移] hy_configuration.group_name 字段已添加")

        # hy_admin_widget 表（挂件配置表）
        from migrations.admin_widget import migrate as migrate_admin_widget
        await migrate_admin_widget(db)

        # hy_area_farmer 表数据回填（产区旧单农户绑定 → 多对多关联表）
        from migrations.area_farmer_binding import migrate as migrate_area_farmer
        await migrate_area_farmer(db)

        # hy_farmer phone/email 唯一索引（验证码登录依赖）
        from migrations.farmer_phone_email_unique import migrate as migrate_farmer_unique
        await migrate_farmer_unique(db)

        # hy_certification_record.certify_url 字段（第三方认证链接持久化）
        from migrations.certification_certify_url import migrate as migrate_cert_url
        await migrate_cert_url(db)

        # 清理已废弃的 API规则白名单机制（hy_api_rule / hy_permission_rule_link 表 + 种子数据）
        from migrations.drop_api_rule_tables import migrate as migrate_drop_api_rule
        await migrate_drop_api_rule(db)

        # hy_task_queue 表（任务队列表）
        from migrations.task_queue_table import migrate as migrate_task_queue
        await migrate_task_queue(db)

        # hy_email_template.interface 字段删除（邮件模板不绑定插件）
        from migrations.email_template_drop_interface import migrate as migrate_email_tpl
        await migrate_email_tpl(db)

    # 迁移注册表：执行尚未应用的登记迁移（hy_schema_version 驱动，旧库探测回填）
    from migrations.registry import run_pending
    await run_pending()


def _register_core_notice_hooks(degraded: list[str]):
    """注册核心通知事件订阅者（步骤 9）

    业务动作与通知发送通过显式 Event/Pipeline 契约解耦。
    """
    try:
        from core.notice_hooks import register_notice_hooks
        register_notice_hooks()
        logger.info("[9] 核心通知事件订阅者已注册")
    except Exception as e:
        logging.warning(f"[启动容错] 通知钩子注册失败: {e}")
        degraded.append("notice_hooks")


async def startup(app):  # noqa: PLR0915  12步启动流程语句数超限，属固有结构
    """
    12步启动流程

    1.  加载配置 (config.py 已通过环境变量加载)
    2.  创建数据库引擎 + 建表
    3.  写入种子数据
    4.  初始化缓存管理器（纯文件缓存）
    5.  插件管理器扫描 plugins/
    6.  查询 hy_plugin 表，获取 status=1 的插件
    7.  加载每个插件的 router.py，注册路由
    8.  恢复插件显式声明的 Event、Pipeline、Task 与 MCP 能力
    9.  注册核心通知事件订阅者
    10. 加载每个插件的 auth.py，注册权限节点
    11. 发布 system.startup 瞬时事件
    11.5 启动任务调度器（定时清理缓存和日志）
    12. 输出启动摘要日志
    """
    logger.info("=" * 60)
    logger.info("  慧眼护农 V4 启动中...")
    logger.info("=" * 60)

    # 降级组件追踪列表：各启动步骤 except 时追加组件名，启动结束后挂载到 app.state
    degraded: list[str] = []

    # 事件与内置任务定义必须早于插件能力恢复，插件可靠订阅注册时会校验事件目录。
    from core.events import register_core_events
    from services.task.builtin_definitions import register_builtin_task_definitions
    register_core_events()
    register_builtin_task_definitions()
    from core.platform.task import ensure_platform_tasks
    ensure_platform_tasks()
    from core.platform.health import platform_health
    platform_health.mark("task_queue", "ready", definitions="platform")
    platform_health.mark("resource_registry", "ready")

    # 1. 配置加载（含 JWT 密钥安全校验：生产模式禁用默认密钥）
    _check_jwt_secrets()
    logger.info("[ 1/12] 配置加载完成 (环境: %s)", "dev" if getattr(app, "debug", True) else "prod")

    # 2. 建表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # 2.5 字段迁移（幂等）
    await _run_field_migrations()
    logger.info("[ 2/12] 数据库表已就绪 (MySQL 8.0)")

    # 3. 种子数据（仅实际创建种子管理员时才提示初始密码，避免常规启动日志泄露凭据线索）
    admin_created = await _seed_data()
    if admin_created:
        logger.info("[ 3/12] 种子数据已写入 (已创建管理员 admin，初始密码 123456，请首次登录后修改)")
    else:
        logger.info("[ 3/12] 种子数据已写入 (管理员账号已就绪)")

    # 3.5 载入各模块当前启用主题（从系统配置读取，缓存到内存）
    try:
        from core.theme_manager import theme_manager
        async with async_session_factory() as db:
            await theme_manager.load_active(db)
        logger.info("[3.5 ] 模板主题已载入")
    except Exception as e:
        logging.warning(f"[启动容错] 步骤 3.5 主题载入失败: {e}")
        degraded.append("theme")

    # 4. 初始化缓存管理器（纯文件缓存，带过期信封）
    try:
        from core.cache.cache_manager import cache_manager
        await cache_manager.init()
        app.state.cache = cache_manager
        platform_health.mark("cache", "ready", backend="file")
        logger.info("[ 4/12] 缓存管理器已初始化")
    except Exception as e:
        logging.warning(f"[启动容错] 步骤 4 失败: {e}")
        platform_health.mark("cache", "degraded", str(e))
        degraded.append("cache")

    # 5. 扫描插件目录（PluginManager 单例挂 app.state，供插件管理端点复用）
    try:
        pm = PluginManager()
        discovered = pm.discover()
        type_count = len(set(d["module"] for d in discovered))
        logger.info(f"[ 5/12] 扫描插件目录: 发现 {type_count} 个类型/{len(discovered)} 个插件")
    except Exception as e:
        logging.warning(f"[启动容错] 步骤 5 失败: {e}")
        degraded.append("plugins")
        pm = PluginManager()
        discovered = []
    app.state.plugin_manager = pm

    # 6. 查询已启用插件（同时把已禁用插件装入路由门禁禁用集合）
    try:
        router_manager = APIRouterManager(app)
        async with async_session_factory() as db:
            result = await db.execute(
                select(PluginModel.name, PluginModel.module, PluginModel.status)
            )
            all_plugins = [(r[0], r[1], r[2]) for r in result.all()]
        enabled_plugins = [(n, m) for n, m, s in all_plugins if s == 1]
        # status=2 的插件装入禁用集合，与运行时 enable/disable 状态保持一致
        for n, m, s in all_plugins:
            if s == 2:
                router_manager.mark_disabled(n)

        # 过滤：只加载在文件系统中实际存在的已启用插件
        discovered_names = {d["name"] for d in discovered}
        valid_enabled = [(n, m) for n, m in enabled_plugins if n in discovered_names]
        logger.info(f"[ 6/12] 数据库插件注册: {len(valid_enabled)} 个已启用")
    except Exception as e:
        logging.warning(f"[启动容错] 步骤 6 失败: {e}")
        degraded.append("plugin_registry")
        router_manager = APIRouterManager(app)
        valid_enabled = []

    # 7. 注册插件路由
    try:
        route_count = 0
        for name, module_name in valid_enabled:
            if router_manager.register_plugin_router(name):
                route_count += 1
        logger.info(f"[ 7/12] 路由注册: {route_count} 个插件路由已加载")
    except Exception as e:
        logging.warning(f"[启动容错] 步骤 7 失败: {e}")
        degraded.append("plugin_routes")
        route_count = 0

    # 8. 恢复插件显式能力；数据库旧 Hook 元数据不再参与运行时注册。
    _restore_plugin_capabilities(pm, valid_enabled, degraded)

    # 9. 注册核心通知事件订阅者。
    _register_core_notice_hooks(degraded)

    # 10. 权限节点由数据库初始化阶段种子函数加载（见下方 seed 调用）
    logger.info("[10/12] 权限节点已由种子阶段加载")

    # 11. 发布系统启动瞬时事件，所有订阅并行且不具备阻断能力。
    try:
        from core.events import event_bus
        await event_bus.publish_transient("system.startup", {})
        logger.info("[11/12] system.startup 事件已发布")
    except Exception as e:
        logging.warning(f"[启动容错] 步骤 11 失败: {e}")
        degraded.append("startup_hooks")

    # 11.5-11.6 启动调度器 + 注册挂件
    await _init_scheduler_and_widgets(app, degraded)

    # 12. 启动摘要
    app.state.router_manager = router_manager
    app.state.degraded_components = degraded
    from core.platform.health import platform_health
    app.state.platform_health = platform_health
    degraded.extend(
        item for item in platform_health.degraded_components()
        if item not in degraded
    )
    from core.events import event_registry, pipeline_engine
    from services.task.definitions import task_registry
    capability_count = (
        len(event_registry.subscription_items())
        + len(pipeline_engine.handlers())
        + len(task_registry.definitions())
    )
    # 获取缓存后端类型和任务调度器状态
    cache_backend = "file" if hasattr(app.state, 'cache') else "未初始化"
    task_status = "running" if (hasattr(app.state, 'task_manager') and getattr(app.state.task_manager, 'scheduler', None) and app.state.task_manager.scheduler.running) else ("stopped" if hasattr(app.state, 'task_manager') else "未启动")
    logger.info(f"[12/12] 启动完成 (核心表: 11张, 插件路由: {route_count}个, 扩展能力: {capability_count}项, 缓存: {cache_backend}, 任务调度器: {task_status})")
    logger.info("=" * 60)


def _restore_plugin_capabilities(
    pm: PluginManager, valid_enabled: list, degraded: list[str],
) -> int:
    """恢复已启用插件的显式能力声明，不扫描方法或读取旧 Hook 表。"""
    restored = 0
    for name, _module_name in valid_enabled:
        if pm.restore_capabilities(name):
            restored += 1
        else:
            degraded.append(f"plugin:{name}")
    logger.info("[ 8/12] 插件显式能力恢复: %s 个插件", restored)
    return restored


async def _init_scheduler_and_widgets(app, degraded: list[str]):
    """启动任务调度器 + 注册内置挂件"""
    from core.platform.health import platform_health

    # 11.5 启动任务调度器（定时清理缓存和日志）
    try:
        from services.task.task_manager import task_manager
        task_manager.start()
        app.state.task_manager = task_manager
        logger.info("[11.5] 任务调度器已启动")
    except Exception as e:
        logging.warning(f"[启动容错] 任务调度器启动失败: {e}")
        degraded.append("scheduler")

    # 11.55 注册天气定时任务（以 hy_configuration 为唯一事实源，weather_enabled=1 才注册拉取）
    try:
        from services.task.weather_worker import register_weather_tasks
        await register_weather_tasks()
        logger.info("[11.55] 天气定时任务已注册")
    except Exception as e:
        logging.warning(f"[启动容错] 天气定时任务注册失败: {e}")
        degraded.append("weather_tasks")

    # 11.6 注册仪表盘挂件（待办事项 + 天气模块产区积温）
    try:
        from services.widget.widget_engine import widget_engine
        from services.widget.widgets_todo import register_todo_widgets
        from services.widget.widgets_weather import register_weather_widgets
        register_todo_widgets(widget_engine)
        register_weather_widgets(widget_engine)
        logger.info("[11.6] 仪表盘挂件已注册")
    except Exception as e:
        logging.warning(f"[启动容错] 仪表盘挂件注册失败: {e}")
        degraded.append("widgets")

    # 11.7 启动任务队列 Worker（asyncio 后台协程，轮询 hy_task_queue）
    try:
        from services.task.queue_worker import queue_worker
        await queue_worker.start()
        app.state.queue_worker = queue_worker
        platform_health.mark("task_queue", "active", worker="running")
        logger.info("[11.7] 任务队列 Worker 已启动")
    except Exception as e:
        logging.warning(f"[启动容错] 任务队列 Worker 启动失败: {e}")
        platform_health.mark("task_queue", "degraded", str(e), worker="stopped")
        degraded.append("queue_worker")


async def shutdown():
    """应用关闭"""
    logger.info("慧眼护农 V4 正在关闭...")
    # 发布系统关闭瞬时事件，插件据此清理长连接等资源。
    try:
        from core.events import event_bus
        await event_bus.publish_transient("system.shutdown", {})
        logger.info("system.shutdown 事件已发布")
    except Exception as e:
        logger.warning(f"[关闭容错] system_shutdown 钩子失败: {e}")
    # 关闭任务调度器
    try:
        from services.task.task_manager import task_manager
        task_manager.shutdown()
    except Exception:
        pass

    # 停止任务队列 Worker
    try:
        from services.task.queue_worker import queue_worker
        await queue_worker.stop()
    except Exception:
        pass

    await engine.dispose()
    logger.info("数据库连接已释放")


async def _seed_data() -> bool:
    """写入种子数据 — 首次启动时初始化默认记录（SEED_VERSION 门控）

    Returns:
        是否实际创建了种子管理员（用于启动日志决定是否提示初始密码）
    """
    from core.auth.password import hash_password
    from core.seed import SEED_VERSION
    from core.db.configuration import ConfigurationModel

    admin_created = False
    async with async_session_factory() as db:
        # SEED_VERSION 门控：版本一致说明种子已是最新，整体跳过（避免每次启动大量 DB 探测）
        # 修改任何种子内容后必须递增 core/seed.py 的 SEED_VERSION，否则存量库不会重新执行
        version_row = (await db.execute(
            select(ConfigurationModel).where(ConfigurationModel.key == "seed_version")
        )).scalar_one_or_none()
        if version_row and version_row.value == SEED_VERSION:
            logger.info("[种子数据] seed_version=%s 已最新，跳过种子流程", SEED_VERSION)
            return admin_created

        # 清理历史遗留的“系统核心”虚拟插件记录（已归入框架层，不再入库）
        stale = (await db.execute(
            select(PluginModel).where(PluginModel.name == "system")
        )).scalar_one_or_none()
        if stale:
            await db.delete(stale)

        # 超级管理员（首次创建或密码算法升级）
        existing_admin = (await db.execute(
            select(Admin).where(Admin.username == "admin")
        )).scalar_one_or_none()
        if not existing_admin:
            pw = hash_password("123456")
            db.add(Admin(id=1, username="admin", password=pw, nickname="超级管理员", status=1))
            admin_created = True
        else:
            # 已存在管理员，不覆写密码（避免重置已修改的密码）
            # 旧格式密码将在管理员下次登录时通过渐进式迁移自动升级
            from core.auth.password import needs_rehash
            if needs_rehash(existing_admin.password):
                logger.info("[种子数据] 管理员 admin 密码为旧格式，将在下次登录时自动迁移至 bcrypt")

        # 默认角色
        existing_role = (await db.execute(
            select(AdminRole).where(AdminRole.name == "超级管理员")
        )).scalar_one_or_none()
        if not existing_role:
            db.add(AdminRole(id=1, name="超级管理员", description="系统内置超级管理员角色", is_system=1))
        existing_op_role = (await db.execute(
            select(AdminRole).where(AdminRole.name == "运营管理员")
        )).scalar_one_or_none()
        if not existing_op_role:
            db.add(AdminRole(id=2, name="运营管理员", description="日常运营管理角色", is_system=1))

        # 管理员-角色绑定
        existing_link = (await db.execute(
            select(AdminRoleLink).where(AdminRoleLink.admin_id == 1)
        )).scalar_one_or_none()
        if not existing_link:
            db.add(AdminRoleLink(admin_id=1, role_id=1))

        # 菜单种子数据（委托 core.seed 模块）
        from core.seed import seed_menus
        await seed_menus(db)

        # 农户端菜单种子数据
        from core.seed import seed_farmer_menus
        await seed_farmer_menus(db)

        # 系统页面注册种子数据（hy_nav 页面注册层）
        from core.seed_nav import seed_nav
        await seed_nav(db)

        # 页面权限种子数据 + 清理已卸载插件遗留权限（委托 core.seed 模块）
        from core.seed import seed_permissions
        await seed_permissions(db)

        # CRUD 权限码种子数据（管理员/农户/角色/权限/规则）
        from core.seed_crud import seed_crud_permissions
        await seed_crud_permissions(db)

        # 系统配置种子数据（委托 core.seed 模块）
        from core.seed import seed_configuration
        await seed_configuration(db)

        # 通知模块预置动作种子数据
        from core.seed_notice import seed_notice_actions
        await seed_notice_actions(db)

        # 回写种子版本号：下次启动版本一致时整体跳过种子流程
        if version_row:
            version_row.value = SEED_VERSION
        else:
            db.add(ConfigurationModel(
                key="seed_version", value=SEED_VERSION,
                description="种子数据版本号（启动门控，勿手动修改）",
            ))

        await db.commit()

    return admin_created


