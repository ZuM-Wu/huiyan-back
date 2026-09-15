"""启动阶段种子数据初始化。"""

import logging

from sqlalchemy import select

from core.db.admin import Admin, AdminRole, AdminRoleLink
from core.db.base import async_session_factory
from core.db.configuration import ConfigurationModel
from core.db.plugin import PluginModel

logger = logging.getLogger(__name__)


async def seed_startup_data() -> bool:
    """写入种子数据 — 首次启动时初始化默认记录（SEED_VERSION 门控）

    Returns:
        是否实际创建了种子管理员（用于启动日志决定是否提示初始密码）
    """
    from core.auth.password import hash_password
    from core.seed import SEED_VERSION

    admin_created = False
    async with async_session_factory() as db:
        # 清理历史遗留的“系统核心”虚拟插件记录（已归入框架层，不再入库）。
        # 此清理必须位于 SEED_VERSION 门控之前，否则存量库在种子版本未变化时
        # 会跳过清理，导致旧记录继续作为 addon 出现在应用列表中。
        stale = (await db.execute(
            select(PluginModel).where(PluginModel.name == "system")
        )).scalar_one_or_none()
        if stale:
            await db.delete(stale)
            logger.info("[种子数据] 已清理历史遗留的系统核心虚拟插件记录")

        # SEED_VERSION 门控：版本一致说明种子已是最新，整体跳过（避免每次启动大量 DB 探测）
        # 修改任何种子内容后必须递增 core/seed.py 的 SEED_VERSION，否则新种子不会在存量环境生效
        version_row = (await db.execute(
            select(ConfigurationModel).where(ConfigurationModel.key == "seed_version")
        )).scalar_one_or_none()
        if version_row and version_row.value == SEED_VERSION:
            if stale:
                await db.commit()
            logger.info("[种子数据] seed_version=%s 已最新，跳过种子流程", SEED_VERSION)
            return admin_created

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
