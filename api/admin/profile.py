"""
管理员账户设置 API
提供当前登录管理员查看/修改个人信息、修改密码功能
"""
import logging

from fastapi import APIRouter, Depends, Request, HTTPException

from core.admin_service import (
    get_admin_by_id,
    get_admin_by_username,
    update_admin as svc_update_admin,
)
from core.auth.middleware_chain import check_admin
from core.auth.password import hash_password, verify_password
from core.log.active_log import active_log
from core.response import ok
from schemas.profile import AdminProfileUpdate, PasswordChange

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1/profile", tags=["账户设置"])


# ========== 接口 ==========

@router.get("")
async def get_profile(
    request: Request,
    _: None = Depends(check_admin),
):
    """获取当前管理员个人信息"""
    admin_id = request.state.user_id
    admin = await get_admin_by_id(admin_id)
    if not admin:
        raise HTTPException(status_code=404, detail="管理员不存在")

    return ok({
        "id": admin["id"],
        "username": admin["username"],
        "nickname": admin["nickname"],
        "email": admin["email"],
        "phone": admin["phone"],
        "last_login_ip": admin["last_login_ip"],
        "create_time": admin["create_time"],
    })


@router.put("")
async def update_profile(
    body: AdminProfileUpdate,
    request: Request,
    _: None = Depends(check_admin),
):
    """修改当前管理员个人信息（昵称、邮箱、手机号）"""
    admin_id = request.state.user_id
    admin = await get_admin_by_id(admin_id)
    if not admin:
        raise HTTPException(status_code=404, detail="管理员不存在")

    await svc_update_admin(admin_id, {
        "nickname": body.nickname,
        "email": body.email,
        "phone": body.phone,
    })

    logger.info("[账户设置] 管理员 %s 更新了个人信息", request.state.user_name)
    await active_log(
        description="修改个人信息",
        log_type="profile",
        request=request,
    )

    return ok(msg="个人信息已更新")


@router.put("/password")
async def change_password(
    body: PasswordChange,
    request: Request,
    _: None = Depends(check_admin),
):
    """修改当前管理员密码"""
    admin_id = request.state.user_id
    admin = await get_admin_by_username(request.state.user_name)
    if not admin:
        raise HTTPException(status_code=404, detail="管理员不存在")

    if not verify_password(body.old_password, admin["password"]):
        raise HTTPException(status_code=400, detail="当前密码不正确")

    if verify_password(body.new_password, admin["password"]):
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    await svc_update_admin(admin_id, {"password": hash_password(body.new_password)})

    logger.info("[账户设置] 管理员 %s 修改了密码", request.state.user_name)
    await active_log(
        description="修改密码",
        log_type="profile",
        request=request,
    )

    return ok(msg="密码修改成功，请重新登录")
