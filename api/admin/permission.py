"""权限管理 API — 权限节点的 CRUD 操作"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, delete, func

from core.admin_service import get_admin_permissions
from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.base import async_session_factory
from core.db.permission import Permission, RolePermissionLink
from core.log.active_log import active_log
from core.menu_service import list_permissions as svc_list_permissions
from core.plugin_query_service import list_all_plugins
from core.response import ok
from schemas.permission import PermissionCreate, PermissionUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1/permission", tags=["权限管理"])


# ========== 接口 ==========

@router.get(
    "/tree",
    dependencies=[Depends(require_permission("permission:list"))],
)
async def permission_tree(_: None = Depends(check_admin)):
    """权限节点树（完整树形结构，含 children）"""
    perms = await svc_list_permissions()
    plugins = await list_all_plugins()
    installed = {p["name"] for p in plugins}

    # 过滤掉已卸载插件的权限节点
    perms = [p for p in perms if not p["plugin"] or p["plugin"] in installed]

    perm_dict = {}
    for p in perms:
        perm_dict[p["id"]] = {
            "id": p["id"], "title": p["title"], "code": p["code"],
            "parent_id": p["parent_id"], "sort_order": p["sort_order"],
            "plugin": p["plugin"], "url": p.get("url") or "",
            "description": p["description"], "children": []
        }

    tree = []
    for node in perm_dict.values():
        if node["parent_id"] == 0:
            tree.append(node)
        else:
            parent = perm_dict.get(node["parent_id"])
            if parent:
                parent["children"].append(node)

    return ok({"list": tree})


@router.get(
    "/my-auth",
)
async def my_permissions(
    request: Request,
    _: None = Depends(check_admin),
):
    """当前管理员的权限与可访问页面映射"""
    user_id = getattr(request.state, "user_id", 0)

    perms = await svc_list_permissions()
    plugins = await list_all_plugins()
    installed = {p["name"] for p in plugins}

    # 仅返回系统核心 + 已安装插件的页面映射
    pages = {
        p["url"]: p["code"]
        for p in perms
        if p.get("url") and (not p["plugin"] or p["plugin"] in installed)
    }

    if user_id == 1:
        codes = [p["code"] for p in perms]
    else:
        codes = await get_admin_permissions(user_id)

    return ok({"auth": codes, "pages": pages})


@router.get(
    "/list",
    dependencies=[Depends(require_permission("permission:list"))],
)
async def list_permissions(_: None = Depends(check_admin)):
    """权限节点列表"""
    perms = await svc_list_permissions()
    return ok({
        "total": len(perms),
        "list": [
            {"id": p["id"], "title": p["title"], "code": p["code"],
             "parent_id": p["parent_id"], "sort_order": p["sort_order"],
             "plugin": p["plugin"], "description": p["description"],
             "url": p.get("url") or ""}
            for p in perms
        ]
    })


@router.post(
    "/create",
    dependencies=[Depends(require_permission("permission:create"))],
)
async def create_permission(
    data: PermissionCreate,
    _: None = Depends(check_admin),
):
    """创建权限节点"""
    try:
        async with async_session_factory() as db:
            existing = (await db.execute(
                select(Permission).where(Permission.code == data.code)
            )).scalar_one_or_none()
            if existing:
                raise HTTPException(409, f"权限标识 '{data.code}' 已存在")

            if data.parent_id > 0:
                parent = (await db.execute(
                    select(Permission).where(Permission.id == data.parent_id)
                )).scalar_one_or_none()
                if not parent:
                    raise HTTPException(400, "父权限节点不存在")

            perm = Permission(
                title=data.title, code=data.code,
                parent_id=data.parent_id, sort_order=data.sort_order,
                description=data.description, plugin=""
            )
            db.add(perm)
            await db.commit()
            await db.refresh(perm)

            await active_log(
                f"创建权限节点: {data.code}",
                "permission_create", rel_id=perm.id
            )
            return ok({"id": perm.id}, msg=f"权限节点 '{data.title}' 创建成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] create_permission: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误")


@router.put(
    "/{perm_id}",
    dependencies=[Depends(require_permission("permission:update"))],
)
async def update_permission(
    perm_id: int,
    data: PermissionUpdate,
    _: None = Depends(check_admin),
):
    """更新权限节点"""
    try:
        async with async_session_factory() as db:
            perm = (await db.execute(
                select(Permission).where(Permission.id == perm_id)
            )).scalar_one_or_none()
            if not perm:
                raise HTTPException(404, "权限节点不存在")

            if data.code != perm.code:
                dup = (await db.execute(
                    select(Permission).where(Permission.code == data.code)
                )).scalar_one_or_none()
                if dup:
                    raise HTTPException(409, f"权限标识 '{data.code}' 已被使用")

            if data.parent_id == perm_id:
                raise HTTPException(400, "不能将权限节点设为自身的子节点")

            perm.title = data.title
            perm.code = data.code
            perm.parent_id = data.parent_id
            perm.sort_order = data.sort_order
            perm.description = data.description
            await db.commit()

            await active_log(
                f"更新权限节点: {data.code}",
                "permission_update", rel_id=perm_id
            )
            return ok(msg="更新成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] update_permission: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误")


@router.delete(
    "/{perm_id}",
    dependencies=[Depends(require_permission("permission:delete"))],
)
async def delete_permission(
    perm_id: int,
    _: None = Depends(check_admin),
):
    """删除权限节点（含子节点和角色关联检查）"""
    try:
        async with async_session_factory() as db:
            perm = (await db.execute(
                select(Permission).where(Permission.id == perm_id)
            )).scalar_one_or_none()
            if not perm:
                raise HTTPException(404, "权限节点不存在")

            child_count = (await db.execute(
                select(func.count()).select_from(Permission)
                .where(Permission.parent_id == perm_id)
            )).scalar() or 0
            if child_count > 0:
                raise HTTPException(400, "该节点下还有子权限，请先删除子节点")

            link_count = (await db.execute(
                select(func.count()).select_from(RolePermissionLink)
                .where(RolePermissionLink.permission_id == perm_id)
            )).scalar() or 0
            if link_count > 0:
                raise HTTPException(400, f"该权限已被 {link_count} 个角色使用，请先解除关联")

            await db.execute(delete(Permission).where(Permission.id == perm_id))
            await db.commit()

            await active_log(
                f"删除权限节点: {perm.code}",
                "permission_delete", rel_id=perm_id
            )
            return ok(msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] delete_permission: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误")
