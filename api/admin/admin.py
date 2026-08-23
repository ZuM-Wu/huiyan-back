"""管理员管理 API"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.admin_service import (
    create_admin as svc_create_admin,
    delete_admin as svc_delete_admin,
    get_admin_by_id,
    get_admin_by_username,
    list_admins as svc_list_admins,
    update_admin as svc_update_admin,
)
from core.auth.middleware_chain import check_admin
from core.auth.password import hash_password
from core.auth.rbac import require_admin_permission
from core.auth.security_policy import validate_password_or_400
from core.log.active_log import active_log
from core.response import ok
from schemas.admin import AdminCreate, AdminUpdate, AdminStatusUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1/admin", tags=["管理员管理"])


@router.get(
    "/list",
    dependencies=[Depends(require_admin_permission("admin:list"))],
)
async def list_admins(
    keywords: str = Query("", description="搜索关键词"),
    status: int = Query(None, description="状态筛选 0=禁用 1=正常"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
    _: None = Depends(check_admin),
):
    """管理员列表，支持搜索+分页+状态筛选"""
    return ok(await svc_list_admins(
        page=page, limit=limit, keywords=keywords, status=status,
    ))


@router.post(
    "/create",
    dependencies=[Depends(require_admin_permission("admin:create"))],
)
async def create_admin(data: AdminCreate, request: Request):
    """创建管理员"""
    try:
        await validate_password_or_400(data.password)

        existing = await get_admin_by_username(data.username)
        if existing:
            raise HTTPException(409, "用户名已存在")

        pw_hash = hash_password(data.password)
        admin_id = await svc_create_admin({
            "username": data.username,
            "password": pw_hash,
            "nickname": data.nickname or data.username,
            "email": data.email,
            "phone": data.phone,
            "role_id": data.role_id,
        })

        await active_log(f"创建管理员: {data.username}", "admin_create", rel_id=admin_id)
        return ok({"id": admin_id}, msg=f"管理员 '{data.username}' 创建成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] create_admin: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误，请稍后重试")


@router.put(
    "/{admin_id}",
    dependencies=[Depends(require_admin_permission("admin:update"))],
)
async def update_admin(admin_id: int, data: AdminUpdate, request: Request):
    """更新管理员"""
    try:
        admin = await get_admin_by_id(admin_id)
        if not admin:
            raise HTTPException(404, "管理员不存在")

        if data.username != admin["username"]:
            dup = await get_admin_by_username(data.username)
            if dup:
                raise HTTPException(409, "用户名已被占用")

        update_data = {
            "username": data.username,
            "nickname": data.nickname,
            "email": data.email,
            "phone": data.phone,
            "role_id": data.role_id,
        }
        if data.password:
            await validate_password_or_400(data.password)
            update_data["password"] = hash_password(data.password)

        await svc_update_admin(admin_id, update_data)
        await active_log(f"更新管理员: {data.username}", "admin_update", rel_id=admin_id)
        return ok(msg="更新成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] update_admin: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误，请稍后重试")


@router.delete(
    "/{admin_id}",
    dependencies=[Depends(require_admin_permission("admin:delete"))],
)
async def delete_admin(admin_id: int, request: Request):
    """删除管理员"""
    if admin_id == 1:
        raise HTTPException(400, "超级管理员不可删除")
    try:
        admin = await get_admin_by_id(admin_id)
        if not admin:
            raise HTTPException(404, "管理员不存在")
        await svc_delete_admin(admin_id)
        await active_log(f"删除管理员: {admin['username']}", "admin_delete", rel_id=admin_id)
        return ok(msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] delete_admin: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误，请稍后重试")


@router.put(
    "/{admin_id}/status",
    dependencies=[Depends(require_admin_permission("admin:status"))],
)
async def toggle_admin_status(admin_id: int, data: AdminStatusUpdate, request: Request):
    """切换管理员状态 — PUT /{id}/status"""
    if admin_id == 1 and data.status == 0:
        raise HTTPException(400, "超级管理员不可禁用")
    try:
        admin = await get_admin_by_id(admin_id)
        if not admin:
            raise HTTPException(404, "管理员不存在")
        await svc_update_admin(admin_id, {"status": data.status})
        status_text = "启用" if data.status == 1 else "禁用"
        await active_log(
            f"切换管理员状态为{status_text}: {admin['username']}",
            "admin_status", rel_id=admin_id
        )
        return ok(msg=f"管理员状态已更新为{status_text}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] toggle_admin_status: {e}", exc_info=True)
        raise HTTPException(500, "服务器内部错误，请稍后重试")
