"""管理员认证路由"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request

from core.admin_service import (
    find_admin_for_login,
    list_admins as svc_list_admins,
    update_admin_login_state,
)
from core.auth.jwt_handler import create_admin_token
from core.auth.middleware_chain import check_admin, reject_repeat
from core.auth.password import verify_password, needs_rehash, hash_password
from core.auth.security_policy import (
    check_login_allowed, record_login_failure, clear_login_failure, get_session_duration
)
from core.hook_events import emit_admin_login
from core.log.active_log import active_log
from core.response import ok
from schemas.auth import LoginRequest, LoginResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1", tags=["管理员认证"])

# 手机号正则（11位中国大陆号码）
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(reject_repeat)])
async def login(req: LoginRequest, request: Request):
    """管理员登录 — 使用中间件链后 endpoint 体不再写认证逻辑（已挂载防重复提交）"""
    # 检查账户是否被锁定（username+IP 组合键，防恶意锁定 DoS）
    client_ip = request.client.host if request.client else ""
    lock_msg = await check_login_allowed(req.username, client_ip)
    if lock_msg:
        raise HTTPException(status_code=423, detail=lock_msg)

    account = req.username.strip()
    admin = await find_admin_for_login(account)
    if not admin or admin["status"] != 1:
        await record_login_failure(req.username, client_ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 验证密码（兼容旧版SHA-256和新版bcrypt）
    if not verify_password(req.password, admin["password"]):
        await record_login_failure(req.username, client_ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    await clear_login_failure(req.username, client_ip)
    password_hash = None
    if needs_rehash(admin["password"]):
        password_hash = hash_password(req.password)
        logger.info("管理员 %s 密码已从 SHA-256 迁移至 bcrypt", admin["username"])

    session_duration = await get_session_duration()
    token = create_admin_token(
        admin["id"], admin["username"], expire_seconds=session_duration,
    )
    ip = request.client.host if request.client else ""
    await update_admin_login_state(admin["id"], ip, password_hash)
    await active_log("管理员登录", "login", request=request)
    await emit_admin_login(admin["id"], ip)
    return LoginResponse(token=token, user_id=admin["id"], username=admin["username"])


@router.get("/me")
async def me(request: Request, _: None = Depends(check_admin)):
    """获取当前管理员信息 — 含角色与常用字段"""
    user_id = request.state.user_id
    result = await svc_list_admins(page=1, limit=1, keywords=str(user_id))
    admin_list = result.get("list", [])
    if not admin_list:
        return ok({"id": user_id, "name": request.state.user_name, "is_admin": True})

    admin = admin_list[0]
    return ok({
        "id": admin["id"],
        "name": admin["username"],
        "nickname": admin["nickname"],
        "email": admin["email"],
        "phone": admin["phone"],
        "role": admin["roles"],
        "is_admin": True
    })
