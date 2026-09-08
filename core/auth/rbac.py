"""
RBAC 权限引擎
提供权限节点树管理、角色-权限绑定、权限缓存 和 require_permission 依赖注入
"""

import logging
from typing import Any, List

from fastapi import Depends, Request, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.base import async_session_factory
from core.db.permission import Permission
from core.cache.cache_manager import cache_manager
from core.auth.middleware_chain import check_admin

logger = logging.getLogger(__name__)

# 权限缓存过期时间（秒）：2小时
PERM_CACHE_TTL = 7200


# ================================================================
# 插件权限自动注册 / 注销
# ================================================================

async def register_plugin_permissions(plugin_name: str, perm_tree: list, db=None):
    """
    递归注册插件权限树
    perm_tree 格式: [{"title":"农场管理","code":"farm:*","url":"farm.html","children":[
        {"title":"查看","code":"farm:list"}, {"title":"创建","code":"farm:create"}
    ]}]

    参数:
        db: 可选事务会话。传入时复用调用方事务且不在此提交（由调用方
            统一 commit/rollback，保障插件安装原子性）；未传时自建会话自行提交。
    """
    if db is not None:
        await _insert_perm_nodes(db, plugin_name, perm_tree, parent_id=0)
    else:
        async with async_session_factory() as session:
            await _insert_perm_nodes(session, plugin_name, perm_tree, parent_id=0)
            await session.commit()
    logger.info("[RBAC] 插件 '%s' 权限树已注册: %d 个节点", plugin_name, _count_nodes(perm_tree))


async def _insert_perm_nodes(db, plugin_name: str, nodes: list, parent_id: int, sort_start: int = 0):
    """递归插入权限节点"""
    from core.db.permission import Permission
    from sqlalchemy import select as sa_select

    for i, node in enumerate(nodes):
        existing = (await db.execute(
            sa_select(Permission).where(Permission.code == node["code"])
        )).scalar_one_or_none()
        if existing:
            perm_id = existing.id
            # 更新已有节点
            existing.title = node["title"]
            existing.plugin = plugin_name
            existing.url = node.get("url", "")
            existing.parent_id = parent_id
            existing.sort_order = sort_start + i
        else:
            perm = Permission(
                title=node["title"], code=node["code"],
                url=node.get("url", ""), plugin=plugin_name,
                parent_id=parent_id, sort_order=sort_start + i
            )
            db.add(perm)
            await db.flush()
            perm_id = perm.id

        children = node.get("children", [])
        if children:
            await _insert_perm_nodes(db, plugin_name, children, parent_id=perm_id)


def _count_nodes(nodes: list) -> int:
    """统计权限树节点总数"""
    count = len(nodes)
    for node in nodes:
        count += _count_nodes(node.get("children", []))
    return count


async def unregister_plugin_permissions(plugin_name: str):
    """删除插件关联的所有权限节点 + 级联清理关联表"""
    from core.db.permission import Permission, RolePermissionLink
    from sqlalchemy import delete as sa_delete

    async with async_session_factory() as db:
        # 获取插件的权限节点 ID
        result = await db.execute(
            select(Permission.id).where(Permission.plugin == plugin_name)
        )
        perm_ids = [r[0] for r in result.all()]

        if perm_ids:
            # 删除角色-权限关联
            await db.execute(
                sa_delete(RolePermissionLink).where(RolePermissionLink.permission_id.in_(perm_ids))
            )
            # 删除权限节点
            await db.execute(
                sa_delete(Permission).where(Permission.plugin == plugin_name)
            )
            await db.commit()

    logger.info("[RBAC] 插件 '%s' 权限已注销: %d 个节点", plugin_name, len(perm_ids))


# ================================================================
# 权限查询辅助函数
# ================================================================

async def get_admin_permissions(
    admin_id: int, db: AsyncSession, use_cache: bool = True
) -> List[str]:
    """
    获取管理员的权限 code 列表。

    默认使用 2 小时缓存；MCP 鉴权传入 use_cache=False，确保权限变更
    在下一次 Key 校验时立即生效。
    """
    # 尝试从缓存读取
    cache_key = f"hy:admin_perm:{admin_id}"
    if use_cache:
        cached = await cache_manager.get(cache_key)
        if cached is not None:
            return cached

    # 缓存未命中，查询数据库
    from core.db.admin import AdminRoleLink
    from core.db.permission import RolePermissionLink

    result = await db.execute(
        select(AdminRoleLink.role_id).where(AdminRoleLink.admin_id == admin_id)
    )
    role_ids = [r[0] for r in result.all()]
    if not role_ids:
        if use_cache:
            await cache_manager.set(cache_key, [], expire=PERM_CACHE_TTL)
        return []

    result = await db.execute(
        select(Permission.code)
        .join(RolePermissionLink, RolePermissionLink.permission_id == Permission.id)
        .where(RolePermissionLink.role_id.in_(role_ids))
        .distinct()
    )
    permissions = [r[0] for r in result.all()]

    # 非缓存模式只返回实时结果，不污染长期权限缓存。
    if use_cache:
        await cache_manager.set(cache_key, permissions, expire=PERM_CACHE_TTL)
    return permissions


async def invalidate_admin_permissions(admin_id: int):
    """清除指定管理员的权限缓存"""
    cache_key = f"hy:admin_perm:{admin_id}"
    await cache_manager.delete(cache_key)


async def invalidate_role_admin_permissions(role_id: int):
    """清除某角色下所有管理员的权限缓存"""
    from core.db.admin import AdminRoleLink
    async with async_session_factory() as db:
        result = await db.execute(
            select(AdminRoleLink.admin_id).where(AdminRoleLink.role_id == role_id)
        )
        admin_ids = [r[0] for r in result.all()]
        for aid in admin_ids:
            await invalidate_admin_permissions(aid)


async def get_menu_tree(db: AsyncSession, plugin: str = "", nav_type: str = "admin") -> List[dict]:
    """获取菜单树（含插件过滤和导航类型筛选）

    默认仅展示系统核心菜单 + 已安装插件的菜单，
    避免已卸载插件的残留菜单出现在侧边栏。
    """
    from core.db.menu import Menu
    from core.db.plugin import PluginModel
    from sqlalchemy import or_

    query = select(Menu).where(Menu.nav_type == nav_type)

    if plugin:
        # 指定插件：系统核心 + 指定插件
        query = query.where(
            or_(Menu.plugin == "", Menu.plugin == plugin)
        )
    else:
        # 未指定插件：系统核心 + 所有已安装插件的菜单
        installed = {r[0] for r in (await db.execute(select(PluginModel.name))).all()}
        if installed:
            query = query.where(
                or_(Menu.plugin == "", Menu.plugin.in_(installed))
            )
        else:
            query = query.where(Menu.plugin == "")

    result = await db.execute(
        query.order_by(Menu.sort_order, Menu.plugin, Menu.id)
    )
    menus = result.scalars().all()

    # 构建树形结构
    menu_dict: dict[int, dict[str, Any]] = {}
    for m in menus:
        menu_dict[m.id] = {
            "id": m.id, "name": m.name, "title": m.title,
            "path": m.path, "icon": m.icon, "parent_id": m.parent_id,
            "sort_order": m.sort_order, "plugin": m.plugin,
            "visible": bool(m.visible),
            "page_type": m.page_type or "system",
            "target_type": m.target_type or "",
            "children": []
        }

    tree: list[dict[str, Any]] = []
    for node in menu_dict.values():
        if node["parent_id"] == 0:
            tree.append(node)
        else:
            parent = menu_dict.get(node["parent_id"])
            if parent:
                parent["children"].append(node)

    return tree


def filter_menu_tree_by_permissions(tree: List[dict], codes: List[str]) -> List[dict]:
    """
    按管理员的权限 code 集合过滤菜单树（用于侧边栏"隐藏无权限菜单"）。

    过滤规则：
    - 系统核心菜单（plugin 为空）：需拥有 page:<菜单标识> 权限，否则剔除；
      与种子数据 seed_permissions 生成的 page:<name> 权限节点一一对应。
    - 插件菜单（plugin 非空）：拥有独立权限模型，此处不按 page:<name> 过滤，始终保留。
    - 父级菜单（含子节点）：递归过滤子节点，过滤后无可见子项则整体剔除。

    超级管理员（id=1）在调用方直接返回完整树，不进入本函数。
    """
    codes_set = set(codes or [])
    result = []
    for node in tree:
        children = node.get("children") or []
        if children:
            # 父级菜单：先递归过滤子节点，无可见子项则不显示该分组
            filtered_children = filter_menu_tree_by_permissions(children, codes)
            if filtered_children:
                new_node = dict(node)
                new_node["children"] = filtered_children
                result.append(new_node)
            continue
        # 叶子菜单：插件菜单直接保留；系统菜单按 page:<name> 校验
        if node.get("plugin"):
            result.append(node)
        elif ("page:" + node.get("name", "")) in codes_set:
            result.append(node)
    return result


# ================================================================
# require_permission — FastAPI Depends 依赖注入
# ================================================================

def require_permission(code: str):
    """
    权限校验依赖
    使用闭包方式，无需操作函数 __signature__

    用法:
        @router.get("/farms", dependencies=[Depends(require_permission("farm:list"))])
        async def list_farms(): ...
    """
    async def checker(request: Request):
        # 优先从 request.state 读取（若 check_admin 已先执行）
        user_id = getattr(request.state, "user_id", 0)
        user_type = getattr(request.state, "user_type", "anonymous")

        # 路由级 dependencies 先于函数级 Depends(check_admin)，
        # 此时 state 可能尚未注入，需自行解析 JWT
        if user_type == "anonymous" or user_id == 0:
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:]
                from core.auth.jwt_handler import verify_jwt
                payload = verify_jwt(token, is_admin=True)
                if payload:
                    user_id = payload.get("id", 0)
                    user_type = "admin"

        # 超级管理员(id=1)直接放行
        if user_type == "admin" and user_id == 1:
            return

        # 查询用户权限（在函数体内开会话，避免将 sessionmaker 当依赖导致
        # FastAPI 误把其 __call__(**local_kw) 的 local_kw 当成必填查询参数）
        async with async_session_factory() as db:
            permissions = await get_admin_permissions(user_id, db)
        if code not in permissions:
            raise HTTPException(status_code=403, detail=f"缺少权限: {code}")

    return checker


def require_admin_permission(code: str):
    """管理员完整认证链 + 操作权限校验。

    统一确保路由级权限校验不会绕过管理员禁用状态和 IP 白名单检查。
    需要管理员操作权限的端点应优先使用此门面。
    """
    async def checker(
        request: Request,
        _: None = Depends(check_admin),
    ):
        user_id = getattr(request.state, "user_id", 0)
        if user_id == 1:
            return
        async with async_session_factory() as db:
            permissions = await get_admin_permissions(user_id, db)
        if code not in permissions:
            raise HTTPException(status_code=403, detail=f"缺少权限: {code}")

    return checker
