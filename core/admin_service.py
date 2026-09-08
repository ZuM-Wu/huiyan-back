"""管理员与角色查询服务层

封装 core.db.admin 的查询，供 api 层和插件层调用。
"""
import logging
from core.time_utils import china_now

from sqlalchemy import select, update, delete, func

from core.db.base import async_session_factory
from core.db.admin import Admin, AdminRole, AdminRoleLink
from core.db.permission import RolePermissionLink, Permission

logger = logging.getLogger(__name__)


# ==================== 管理员查询 ====================

async def list_admins(page: int = 1, limit: int = 10, keywords: str = "",
                      status: int | None = None) -> dict:
    """分页查询管理员列表（含角色信息）

    Args:
        page: 页码（从1开始）
        limit: 每页条数
        keywords: 搜索关键词（支持 ID/用户名/昵称/邮箱）
        status: 状态筛选 0=禁用 1=正常

    Returns:
        {"total": int, "page": int, "limit": int, "list": [...]}
    """
    async with async_session_factory() as db:
        q = select(Admin)
        if keywords:
            if keywords.isdigit():
                # 纯数字时按 ID 精确匹配
                q = q.where(Admin.id == keywords)
            else:
                q = q.where(
                    Admin.username.contains(keywords) |
                    Admin.nickname.contains(keywords) |
                    Admin.email.contains(keywords)
                )
        if status is not None:
            q = q.where(Admin.status == status)

        total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
        admins = (await db.execute(
            q.order_by(Admin.id).offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        # 查询每个管理员所属角色（角色名拼接为逗号分隔字符串）
        admin_ids = [a.id for a in admins]
        role_map: dict[int, str] = {}
        role_id_map: dict[int, int] = {}
        if admin_ids:
            r = await db.execute(
                select(AdminRoleLink.admin_id, AdminRoleLink.role_id, AdminRole.name)
                .join(AdminRole, AdminRole.id == AdminRoleLink.role_id)
                .where(AdminRoleLink.admin_id.in_(admin_ids))
            )
            for admin_id, role_id, role_name in r.all():
                if admin_id in role_map:
                    role_map[admin_id] += "," + role_name
                else:
                    role_map[admin_id] = role_name
                if admin_id not in role_id_map:
                    role_id_map[admin_id] = role_id

        return {
            "total": total, "page": page, "limit": limit,
            "list": [
                {"id": a.id, "username": a.username, "nickname": a.nickname,
                 "email": a.email, "phone": a.phone, "status": a.status,
                 "roles": role_map.get(a.id, ""),
                 "role_id": role_id_map.get(a.id, 0),
                 "last_login_ip": a.last_login_ip,
                 "create_time": str(a.create_time) if a.create_time else None}
                for a in admins
            ]
        }


async def get_admin_by_id(admin_id: int) -> dict | None:
    """查询管理员详情，不存在时返回 None"""
    async with async_session_factory() as db:
        admin = (await db.execute(select(Admin).where(Admin.id == admin_id))).scalar_one_or_none()
        if not admin:
            return None
        return {
            "id": admin.id, "username": admin.username,
            "nickname": admin.nickname or "", "email": admin.email or "",
            "phone": admin.phone or "", "status": admin.status,
            "last_login_ip": admin.last_login_ip or "",
            "last_action_time": str(admin.last_action_time) if admin.last_action_time else None,
            "create_time": str(admin.create_time) if admin.create_time else None,
        }


async def get_admin_by_username(username: str) -> dict | None:
    """按用户名查询管理员（登录用），返回包含密码哈希的完整信息"""
    async with async_session_factory() as db:
        admin = (await db.execute(select(Admin).where(Admin.username == username))).scalar_one_or_none()
        if not admin:
            return None
        return {
            "id": admin.id, "username": admin.username, "password": admin.password,
            "nickname": admin.nickname or "", "email": admin.email or "",
            "phone": admin.phone or "", "status": admin.status,
            "last_login_ip": admin.last_login_ip or "",
            "last_action_time": str(admin.last_action_time) if admin.last_action_time else None,
            "create_time": str(admin.create_time) if admin.create_time else None,
        }


async def find_admin_for_login(account: str) -> dict | None:
    """按用户名、手机号或邮箱查询登录所需的管理员字段。"""
    async with async_session_factory() as db:
        condition = Admin.username == account
        if account.isdigit() and len(account) == 11:
            condition = Admin.phone == account
        elif "@" in account:
            condition = Admin.email == account
        admin = (await db.execute(select(Admin).where(condition))).scalar_one_or_none()
        if not admin:
            return None
        return {
            "id": admin.id,
            "username": admin.username,
            "password": admin.password,
            "status": admin.status,
        }


async def update_admin_login_state(
    admin_id: int,
    ip: str,
    password_hash: str | None = None,
) -> None:
    """更新登录 IP、操作时间，并可在登录时迁移旧密码哈希。"""
    values = {"last_login_ip": ip, "last_action_time": china_now()}
    if password_hash is not None:
        values["password"] = password_hash
    async with async_session_factory() as db:
        await db.execute(update(Admin).where(Admin.id == admin_id).values(**values))
        await db.commit()


async def get_admin_contacts(admin_ids: list[int]) -> list[dict]:
    """批量获取管理员联系方式（邮箱/手机）

    从 alert_service.get_notify_admins 提取逻辑：
    只返回有邮箱且 status=1 的管理员。
    """
    if not admin_ids:
        return []
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Admin.id, Admin.nickname, Admin.email, Admin.phone).where(
                Admin.id.in_(admin_ids),
                Admin.email != "",
                Admin.status == 1,
            )
        )).all()
        return [
            {"id": r[0], "nickname": r[1], "email": r[2], "phone": r[3] or ""}
            for r in rows if r[2]
        ]


# ==================== 管理员增删改 ====================

async def create_admin(data: dict) -> int:
    """创建管理员（data 含 username/password/nickname/email/phone/role_id），返回新管理员 ID

    Raises:
        sqlalchemy.exc.IntegrityError: 用户名唯一索引冲突
    """
    async with async_session_factory() as db:
        admin = Admin(
            username=data["username"], password=data["password"],
            nickname=data.get("nickname") or data["username"],
            email=data.get("email", ""), phone=data.get("phone", ""),
            status=1
        )
        db.add(admin)
        await db.flush()
        # 绑定角色
        role_id = data.get("role_id")
        if role_id:
            db.add(AdminRoleLink(admin_id=admin.id, role_id=role_id))
        await db.commit()
        return admin.id


async def update_admin(admin_id: int, data: dict) -> bool:
    """更新管理员信息（含角色绑定），返回是否成功

    data 可包含：username/nickname/email/phone/password（已哈希）/role_id
    """
    async with async_session_factory() as db:
        admin = (await db.execute(select(Admin).where(Admin.id == admin_id))).scalar_one_or_none()
        if not admin:
            return False
        # 更新基本信息（排除 role_id，角色单独处理）
        update_fields = {k: v for k, v in data.items() if k != "role_id"}
        if update_fields:
            await db.execute(update(Admin).where(Admin.id == admin_id).values(**update_fields))
        # 更新角色绑定（先删后插）
        if "role_id" in data:
            await db.execute(delete(AdminRoleLink).where(AdminRoleLink.admin_id == admin_id))
            if data["role_id"]:
                db.add(AdminRoleLink(admin_id=admin_id, role_id=data["role_id"]))
        await db.commit()
        return True


async def delete_admin(admin_id: int) -> bool:
    """删除管理员（含角色关联），返回是否成功"""
    async with async_session_factory() as db:
        admin = (await db.execute(select(Admin).where(Admin.id == admin_id))).scalar_one_or_none()
        if not admin:
            return False
        # 先删除角色关联，再删除管理员
        await db.execute(delete(AdminRoleLink).where(AdminRoleLink.admin_id == admin_id))
        await db.execute(delete(Admin).where(Admin.id == admin_id))
        await db.commit()
        return True


# ==================== 角色管理 ====================

async def list_roles(keywords: str = "") -> list:
    """查询角色列表（含 admins 字段，逗号分隔的管理员用户名）"""
    async with async_session_factory() as db:
        q = select(AdminRole)
        if keywords:
            q = q.where(AdminRole.name.contains(keywords) | AdminRole.description.contains(keywords))
        roles = (await db.execute(q.order_by(AdminRole.id))).scalars().all()

        # 查询每个角色下的管理员
        role_ids = [r.id for r in roles]
        admin_map: dict[int, list[str]] = {}
        if role_ids:
            links = (await db.execute(
                select(AdminRoleLink.role_id, AdminRoleLink.admin_id)
                .where(AdminRoleLink.role_id.in_(role_ids))
            )).all()
            admin_ids = [a[1] for a in links]
            if admin_ids:
                r2 = await db.execute(select(Admin.id, Admin.username).where(Admin.id.in_(admin_ids)))
                id_to_name = {a[0]: a[1] for a in r2.all()}
                for role_id, admin_id in links:
                    name = id_to_name.get(admin_id, str(admin_id))
                    admin_map.setdefault(role_id, []).append(name)

        return [
            {"id": r.id, "name": r.name, "description": r.description,
             "is_system": bool(r.is_system),
             "admins": ",".join(admin_map.get(r.id, [])),
             "create_time": str(r.create_time) if r.create_time else None}
            for r in roles
        ]


async def create_role(data: dict) -> int:
    """创建角色（data 含 name/description/auth[权限ID列表]），返回新角色 ID"""
    async with async_session_factory() as db:
        role = AdminRole(name=data["name"], description=data.get("description", ""))
        db.add(role)
        await db.flush()
        # 绑定权限
        for auth_id in data.get("auth", []):
            db.add(RolePermissionLink(role_id=role.id, permission_id=auth_id))
        await db.commit()
        return role.id


async def update_role(role_id: int, data: dict) -> bool:
    """更新角色（含权限先删后插），返回是否成功

    data 可包含：name/description/auth[权限ID列表]
    """
    async with async_session_factory() as db:
        role = (await db.execute(select(AdminRole).where(AdminRole.id == role_id))).scalar_one_or_none()
        if not role:
            return False
        role.name = data.get("name", role.name)
        role.description = data.get("description", role.description)
        # 权限：先删后插
        await db.execute(delete(RolePermissionLink).where(RolePermissionLink.role_id == role_id))
        for auth_id in data.get("auth", []):
            db.add(RolePermissionLink(role_id=role_id, permission_id=auth_id))
        await db.commit()
        return True


async def delete_role(role_id: int) -> bool:
    """删除角色（含权限关联），返回是否成功

    如果角色下仍有管理员，返回 False。
    """
    async with async_session_factory() as db:
        # 检查角色下是否有管理员
        count = (await db.execute(
            select(func.count()).select_from(AdminRoleLink).where(AdminRoleLink.role_id == role_id)
        )).scalar() or 0
        if count > 0:
            return False
        role = (await db.execute(select(AdminRole).where(AdminRole.id == role_id))).scalar_one_or_none()
        if not role:
            return False
        await db.execute(delete(RolePermissionLink).where(RolePermissionLink.role_id == role_id))
        await db.execute(delete(AdminRole).where(AdminRole.id == role_id))
        await db.commit()
        return True


# ==================== 权限相关 ====================

async def get_admin_permissions(admin_id: int) -> list[str]:
    """获取管理员权限码列表（通过角色关联查询权限节点 code）

    查询链路：AdminRoleLink → RolePermissionLink → Permission
    """
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Permission.code)
            .join(RolePermissionLink, RolePermissionLink.permission_id == Permission.id)
            .join(AdminRoleLink, AdminRoleLink.role_id == RolePermissionLink.role_id)
            .where(AdminRoleLink.admin_id == admin_id)
        )).all()
        return [r[0] for r in rows]


async def assign_admin_roles(admin_id: int, role_ids: list[int]) -> bool:
    """分配管理员角色（先删后插），返回是否成功"""
    async with async_session_factory() as db:
        admin = (await db.execute(select(Admin).where(Admin.id == admin_id))).scalar_one_or_none()
        if not admin:
            return False
        # 先删除旧的角色关联
        await db.execute(delete(AdminRoleLink).where(AdminRoleLink.admin_id == admin_id))
        # 再插入新的角色关联
        for role_id in role_ids:
            db.add(AdminRoleLink(admin_id=admin_id, role_id=role_id))
        await db.commit()
        return True


async def list_admins_with_email() -> list[dict]:
    """获取所有有邮箱的启用管理员（供 admin_notifier 插件配置页面选择接收人）

    返回 [{id, nickname, email, username}]，nickname 为空时回退 username。
    """
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Admin.id, Admin.nickname, Admin.email, Admin.username).where(
                Admin.email != "",
                Admin.email != None,  # noqa: E711
                Admin.status == 1,
            )
        )).all()
    return [
        {"id": r[0], "nickname": r[1] or r[3], "email": r[2], "username": r[3]}
        for r in rows
    ]
