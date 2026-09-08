"""
菜单与导航服务层

封装对 core.db.menu / nav / permission / admin 模型的数据库查询逻辑，
供 api 层调用。所有函数返回纯 dict/list/int/bool，不返回 ORM 实例。
"""
import logging

from sqlalchemy import select, delete

from core.db.base import async_session_factory
from core.db.menu import Menu
from core.db.nav import Nav
from core.db.permission import Permission, RolePermissionLink
from core.db.admin import AdminRole
from core.auth.rbac import get_menu_tree, invalidate_role_admin_permissions

logger = logging.getLogger(__name__)


# ========== 内部辅助函数 ==========

def _fmt_dt(dt) -> str:
    """格式化 datetime 为字符串，None 返回空串"""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _menu_to_dict(m: Menu) -> dict:
    """Menu ORM 实例转 dict"""
    return {
        "id": m.id,
        "name": m.name,
        "title": m.title,
        "path": m.path,
        "icon": m.icon,
        "parent_id": m.parent_id,
        "sort_order": m.sort_order,
        "plugin": m.plugin,
        "visible": m.visible,
        "nav_type": m.nav_type,
        "page_type": m.page_type,
        "target_type": m.target_type,
        "create_time": _fmt_dt(m.create_time),
    }


def _nav_to_dict(n: Nav) -> dict:
    """Nav ORM 实例转 dict"""
    return {
        "id": n.id,
        "key": n.key,
        "title": n.title,
        "path": n.path,
        "icon": n.icon,
        "nav_type": n.nav_type,
        "source": n.source,
        "plugin": n.plugin,
        "group_name": n.group_name,
        "page_type": n.page_type,
        "sort_order": n.sort_order,
        "create_time": _fmt_dt(n.create_time),
    }


def _permission_to_dict(p: Permission) -> dict:
    """Permission ORM 实例转 dict"""
    return {
        "id": p.id,
        "title": p.title,
        "code": p.code,
        "url": p.url,
        "parent_id": p.parent_id,
        "sort_order": p.sort_order,
        "plugin": p.plugin,
        "description": p.description,
        "create_time": _fmt_dt(p.create_time),
    }


# ========== 菜单 CRUD ==========

async def list_menus(nav_type: str = "") -> list:
    """获取菜单列表（可选按 nav_type 筛选）

    :param nav_type: 导航类型筛选 (admin/frontend)，空字符串=全部
    :return: [dict, ...]
    """
    async with async_session_factory() as db:
        q = select(Menu)
        if nav_type:
            q = q.where(Menu.nav_type == nav_type)
        rows = (await db.execute(
            q.order_by(Menu.sort_order, Menu.plugin, Menu.id)
        )).scalars().all()
        return [_menu_to_dict(m) for m in rows]


async def create_menu(data: dict) -> int:
    """创建菜单节点

    :param data: 菜单字段字典
    :return: 新建菜单 ID
    """
    async with async_session_factory() as db:
        menu = Menu(**data)
        db.add(menu)
        await db.commit()
        await db.refresh(menu)
        return menu.id


async def update_menu(menu_id: int, data: dict) -> bool:
    """更新菜单节点（仅更新非 None 值）

    :param menu_id: 菜单 ID
    :param data:    待更新字段字典
    :return: 是否更新成功
    """
    async with async_session_factory() as db:
        result = await db.execute(select(Menu).where(Menu.id == menu_id))
        menu = result.scalar_one_or_none()
        if not menu:
            return False

        for field, value in data.items():
            if value is not None:
                setattr(menu, field, value)

        await db.commit()
        return True


async def _cleanup_menu_page_permission(db, menu: Menu) -> None:
    """删除系统菜单时同步清理 page: 权限与角色关联

    仅对系统菜单（plugin 为空）生效，插件菜单的权限由插件自行管理。
    """
    if menu.plugin:
        return
    perm_code = f"page:{menu.name}"
    perm = (await db.execute(
        select(Permission).where(Permission.code == perm_code)
    )).scalar_one_or_none()
    if not perm:
        return
    # 查找关联的角色 ID（用于后续失效缓存）
    role_ids = [r[0] for r in (await db.execute(
        select(RolePermissionLink.role_id).where(
            RolePermissionLink.permission_id == perm.id)
    )).all()]
    # 删除角色权限关联
    await db.execute(
        delete(RolePermissionLink).where(
            RolePermissionLink.permission_id == perm.id)
    )
    # 删除权限节点
    await db.execute(
        delete(Permission).where(Permission.id == perm.id)
    )
    # 失效角色权限缓存
    for role_id in role_ids:
        await invalidate_role_admin_permissions(role_id)


async def delete_menu(menu_id: int) -> bool:
    """删除菜单节点

    :param menu_id: 菜单 ID
    :return: 是否删除成功
    """
    async with async_session_factory() as db:
        result = await db.execute(select(Menu).where(Menu.id == menu_id))
        menu = result.scalar_one_or_none()
        if not menu:
            return False
        await _cleanup_menu_page_permission(db, menu)
        await db.delete(menu)
        await db.commit()
        return True


# ========== 导航（页面注册表）CRUD ==========

async def list_navs(nav_type: str = "") -> list:
    """获取导航列表（可选按 nav_type 筛选）

    :param nav_type: 导航类型筛选，空字符串=全部
    :return: [dict, ...]
    """
    async with async_session_factory() as db:
        q = select(Nav)
        if nav_type:
            q = q.where(Nav.nav_type == nav_type)
        rows = (await db.execute(
            q.order_by(Nav.sort_order, Nav.plugin, Nav.id)
        )).scalars().all()
        return [_nav_to_dict(n) for n in rows]


async def create_nav(data: dict) -> int:
    """创建导航页面

    :param data: 导航字段字典
    :return: 新建导航 ID
    """
    async with async_session_factory() as db:
        nav = Nav(**data)
        db.add(nav)
        await db.commit()
        await db.refresh(nav)
        return nav.id


async def update_nav(nav_id: int, data: dict) -> bool:
    """更新导航页面（仅更新非 None 值）

    :param nav_id: 导航 ID
    :param data:   待更新字段字典
    :return: 是否更新成功
    """
    async with async_session_factory() as db:
        result = await db.execute(select(Nav).where(Nav.id == nav_id))
        nav = result.scalar_one_or_none()
        if not nav:
            return False

        for field, value in data.items():
            if value is not None:
                setattr(nav, field, value)

        await db.commit()
        return True


async def delete_nav(nav_id: int) -> bool:
    """删除导航页面

    :param nav_id: 导航 ID
    :return: 是否删除成功
    """
    async with async_session_factory() as db:
        result = await db.execute(select(Nav).where(Nav.id == nav_id))
        nav = result.scalar_one_or_none()
        if not nav:
            return False
        await db.delete(nav)
        await db.commit()
        return True


# ========== 权限管理 ==========

async def list_permissions() -> list:
    """获取全部权限节点列表

    :return: [dict, ...]
    """
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Permission).order_by(
                Permission.sort_order, Permission.plugin, Permission.id
            )
        )).scalars().all()
        return [_permission_to_dict(p) for p in rows]


async def assign_permissions(role_id: int, permission_ids: list[int]) -> bool:
    """为角色分配权限（先清除旧关联，再写入新关联）

    :param role_id:        角色 ID
    :param permission_ids: 权限 ID 列表
    :return: 始终返回 True
    """
    async with async_session_factory() as db:
        # 先删除该角色的全部旧权限关联
        await db.execute(
            delete(RolePermissionLink).where(
                RolePermissionLink.role_id == role_id
            )
        )
        # 再批量写入新关联
        for perm_id in permission_ids:
            db.add(RolePermissionLink(role_id=role_id, permission_id=perm_id))
        await db.commit()
        return True


async def list_roles_with_permissions() -> list:
    """获取所有角色及其关联的权限列表

    :return: [{"id": int, "name": str, "description": str, "is_system": int,
              "create_time": str, "permissions": [dict, ...]}, ...]
    """
    async with async_session_factory() as db:
        # 查询全部角色
        roles = (await db.execute(
            select(AdminRole).order_by(AdminRole.id)
        )).scalars().all()

        result = []
        for role in roles:
            # 查询该角色关联的权限节点
            perm_rows = (await db.execute(
                select(Permission)
                .join(RolePermissionLink, RolePermissionLink.permission_id == Permission.id)
                .where(RolePermissionLink.role_id == role.id)
                .order_by(Permission.sort_order, Permission.plugin, Permission.id)
            )).scalars().all()

            result.append({
                "id": role.id,
                "name": role.name,
                "description": role.description,
                "is_system": role.is_system,
                "create_time": _fmt_dt(role.create_time),
                "permissions": [_permission_to_dict(p) for p in perm_rows],
            })
        return result


# ========== 农户端菜单 ==========

async def get_farmer_menus(farmer_id: int) -> list:
    """获取农户端菜单树（前台侧边栏用，不做权限过滤）

    :param farmer_id: 农户 ID（预留参数，当前农户端菜单无权限过滤）
    :return: 菜单树 list[dict]
    """
    async with async_session_factory() as db:
        tree = await get_menu_tree(db, nav_type="frontend")
        return tree
