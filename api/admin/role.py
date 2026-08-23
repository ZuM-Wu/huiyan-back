"""角色管理 API"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from core.admin_service import (
    create_role as svc_create_role,
    delete_role as svc_delete_role,
    list_roles as svc_list_roles,
    update_role as svc_update_role,
)
from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission, invalidate_role_admin_permissions
from core.log.active_log import active_log
from core.menu_service import list_roles_with_permissions
from core.response import ok
from schemas.role import RoleCreate, RoleUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1/role", tags=["角色管理"])


@router.get(
    "/list",
    dependencies=[Depends(require_permission("role:list"))],
)
async def list_roles(
    keywords: str = Query("", description="搜索关键词"),
    _: None = Depends(check_admin),
):
    """角色列表（含 admins 字段）"""
    roles = await svc_list_roles(keywords=keywords)
    return ok({"total": len(roles), "list": roles})


@router.get(
    "/{role_id}",
    dependencies=[Depends(require_permission("role:list"))],
)
async def get_role(role_id: int, _: None = Depends(check_admin)):
    """角色详情（含权限ID数组）"""
    roles = await svc_list_roles("")
    role_info = next((r for r in roles if r["id"] == role_id), None)
    if not role_info:
        raise HTTPException(404, "角色不存在")

    # 获取权限ID数组
    roles_perms = await list_roles_with_permissions()
    role_perms = next((r for r in roles_perms if r["id"] == role_id), None)
    auth = [p["id"] for p in role_perms["permissions"]] if role_perms else []

    return ok({
        "id": role_info["id"], "name": role_info["name"],
        "description": role_info["description"],
        "is_system": role_info["is_system"],
        "admins": role_info["admins"],
        "auth": auth,
    })


@router.post(
    "/create",
    dependencies=[Depends(require_permission("role:create"))],
)
async def create_role(data: RoleCreate, _: None = Depends(check_admin)):
    """创建角色（含权限赋值）"""
    try:
        role_id = await svc_create_role({
            "name": data.name,
            "description": data.description,
            "auth": data.auth or [],
        })
        await active_log(f"创建角色: {data.name}", "role_create", rel_id=role_id)
        await invalidate_role_admin_permissions(role_id)
        return ok({"id": role_id}, msg=f"角色 '{data.name}' 创建成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] create_role: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误，请稍后重试")


@router.put(
    "/{role_id}",
    dependencies=[Depends(require_permission("role:update"))],
)
async def update_role(role_id: int, data: RoleUpdate, _: None = Depends(check_admin)):
    """更新角色（权限先删后插）"""
    if role_id == 1:
        raise HTTPException(400, "超级管理员角色不可修改")

    try:
        success = await svc_update_role(role_id, {
            "name": data.name,
            "description": data.description,
            "auth": data.auth or [],
        })
        if not success:
            raise HTTPException(404, "角色不存在")

        await active_log(f"更新角色: {data.name}", "role_update", rel_id=role_id)
        await invalidate_role_admin_permissions(role_id)
        return ok(msg="更新成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] update_role: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误，请稍后重试")


@router.delete(
    "/{role_id}",
    dependencies=[Depends(require_permission("role:delete"))],
)
async def delete_role(role_id: int, _: None = Depends(check_admin)):
    """删除角色（含保护逻辑）"""
    if role_id == 1:
        raise HTTPException(400, "超级管理员角色不可删除")

    try:
        # 先检查角色是否存在
        roles = await svc_list_roles("")
        role_info = next((r for r in roles if r["id"] == role_id), None)
        if not role_info:
            raise HTTPException(404, "角色不存在")

        success = await svc_delete_role(role_id)
        if not success:
            raise HTTPException(400, "该角色下仍有管理员，无法删除。请先移除管理员后再试。")

        await active_log(f"删除角色: {role_info['name']}", "role_delete", rel_id=role_id)
        await invalidate_role_admin_permissions(role_id)
        return ok(msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] delete_role: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误，请稍后重试")
