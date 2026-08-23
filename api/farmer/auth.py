"""农户认证路由"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from core.auth.jwt_handler import create_farmer_token
from core.auth.middleware_chain import check_farmer, reject_repeat
from core.auth.password import verify_password, needs_rehash, hash_password
from core.auth.security_policy import (
    check_login_allowed, record_login_failure, clear_login_failure,
    is_password_complexity_enabled, check_password_complexity,
    is_farmer_register_allowed, check_farmer_email_suffix, get_farmer_session_duration,
    is_farmer_register_phone_required, is_farmer_register_email_required
)
from core.config_service import get_config as get_config_value
from core.farmer_service import (
    list_farmers, get_farmer_by_id, get_farmer_by_username,
    verify_farmer_password, update_farmer_password,
    update_farmer_login_info, create_farmer,
)
from core.rate_limiter import check_rate
from core.verify_code import verify_code as verify_code_service
from core.log.active_log import active_log
from core.hook_events import emit_farmer_login
from core.db.base import async_session_factory
from core.events import event_bus
from core.response import ok
from schemas.auth import FarmerLoginRequest, RegisterRequest, LoginResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["农户认证"])

# 手机号正则（11位中国大陆号码）
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def _norm_or_none(s: str | None) -> str | None:
    """空串归一为 None（避免与唯一索引冲突）"""
    s = (s or "").strip()
    return s if s else None


async def _find_farmer_by_field(field: str, value: str) -> dict | None:
    """按 phone/email 精确查找农户

    list_farmers 使用 contains 模糊匹配，需 Python 精确过滤。
    """
    result = await list_farmers(keywords=value, search_field=field, limit=10)
    for f in result["list"]:
        if f.get(field) == value:
            return f
    return None


async def _get_cfg(key: str, default: str = "") -> str:
    """读取系统配置项"""
    val = await get_config_value(key)
    return val if val is not None else default


@router.get("/check-account")
async def check_account(request: Request = None, phone: str = "", email: str = ""):
    """实时检测手机号或邮箱是否已注册（供注册表单防抖调用，按 IP 频控防批量枚举）"""
    # 频控：同 IP 每分钟最多 20 次（注册页防抖下正常用户远达不到）
    ip = request.client.host if request is not None and request.client else ""
    if not check_rate(f"check-account:{ip}", limit=20, window=60):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")

    phone = phone.strip()
    email = email.strip()
    if not phone and not email:
        return ok({"available": True})
    if phone:
        farmer = await _find_farmer_by_field("phone", phone)
        return ok({"available": farmer is None, "field": "phone"})
    if email:
        farmer = await _find_farmer_by_field("email", email)
        return ok({"available": farmer is None, "field": "email"})


async def _check_register_guards(req: RegisterRequest):
    """注册前置守卫：开关/必填项/邮箱后缀/密码复杂度"""
    # 注册开关：管理员可在「系统设置-访问设置-前台设置」关闭注册
    if not await is_farmer_register_allowed():
        raise HTTPException(status_code=403, detail="当前系统未开放注册")

    # 手机号必填校验
    if await is_farmer_register_phone_required() and not (req.phone or "").strip():
        raise HTTPException(status_code=400, detail="请填写手机号")

    # 邮箱必填校验
    if await is_farmer_register_email_required() and not (req.email or "").strip():
        raise HTTPException(status_code=400, detail="请填写邮箱")

    # 邮箱后缀限制校验（仅开启限制且填写邮箱时生效）
    suffix_err = await check_farmer_email_suffix(req.email or "")
    if suffix_err:
        raise HTTPException(status_code=400, detail=suffix_err)

    # 密码复杂度校验
    if await is_password_complexity_enabled():
        err = check_password_complexity(req.password)
        if err:
            raise HTTPException(status_code=400, detail=f"密码不符合安全策略: {err}")


async def _check_register_uniqueness(username: str, phone, email):
    """注册唯一性预检：用户名/手机号/邮箱（友好提示，最终由唯一索引兜底）"""
    existing = await get_farmer_by_username(username)
    if existing:
        raise HTTPException(status_code=400, detail="该手机号或邮箱已注册")

    # 手机号唯一性校验
    if phone:
        farmer = await _find_farmer_by_field("phone", phone)
        if farmer:
            raise HTTPException(status_code=400, detail="该手机号已被注册")

    # 邮箱唯一性校验
    if email:
        farmer = await _find_farmer_by_field("email", email)
        if farmer:
            raise HTTPException(status_code=400, detail="该邮箱已被注册")


async def _check_register_verify_code(phone, email, verify_code: str):
    """注册验证码校验：按渠道配置开关比对（手机优先，与发码接口渠道语义一致）"""
    verify_target = ""
    if phone and await _get_cfg("farmer_phone_register_verify", "1") == "1":
        verify_target = phone
    elif email and await _get_cfg("farmer_email_register_verify", "1") == "1":
        verify_target = email
    if not verify_target:
        return
    code = (verify_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="请填写验证码")
    ok_val, msg = await verify_code_service(verify_target, code, "register")
    if not ok_val:
        raise HTTPException(status_code=400, detail=msg)


@router.post("/register", dependencies=[Depends(reject_repeat)])
async def register(req: RegisterRequest, request: Request):
    """农户注册（挂载防重复提交中间件，3秒内相同请求拒绝）"""
    await _check_register_guards(req)

    # 手机号/邮箱统一归一：空串转 None（避免与唯一索引冲突）
    phone = _norm_or_none(req.phone)
    email = _norm_or_none(req.email)

    # 用户名自动填充：留空时使用手机号或邮箱
    username = (req.username or "").strip()
    if not username:
        username = phone or email or ""
    if not username:
        raise HTTPException(status_code=400, detail="无法确定用户名，请填写手机号或邮箱")

    await _check_register_uniqueness(username, phone, email)
    await _check_register_verify_code(phone, email, req.verify_code)

    pw_hash = hash_password(req.password)
    try:
        async with async_session_factory() as db:
            farmer_id = await create_farmer(
                username, pw_hash, nickname=req.nickname or username,
                email=email or "", phone=phone or "", db=db, commit=False,
            )
            await event_bus.publish_durable("farmer.registered", {
                "farmer_id": farmer_id, "username": username,
                "phone": phone or "", "email": email or "",
                "nickname": req.nickname or username,
            }, db)
            await db.commit()
    except IntegrityError:
        # 并发竞态兜底：唯一索引冲突时返回 400 而非 500
        raise HTTPException(status_code=400, detail="该手机号或邮箱已被注册")

    token = create_farmer_token(farmer_id, username)
    await active_log("农户注册", "farmer_register", rel_id=farmer_id, request=request)
    return LoginResponse(token=token, user_id=farmer_id, username=username)


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(reject_repeat)])
async def login(req: FarmerLoginRequest, request: Request):
    """
    农户统一登录（支持密码/验证码双模式，对标 ZJMF）

    - type=password: 密码登录（account 根据格式判断手机/邮箱/用户名）
    - type=code: 短信验证码登录（account 必须为手机号）
    - 向后兼容: 当 username 有值且 account 为空时，走传统用户名+密码逻辑
    """
    login_type = (req.type or "password").strip().lower()

    # 向后兼容旧接口：username 有值且 account 为空时走旧逻辑
    if req.username and not req.account:
        return await _login_by_username(req.username, req.password, request)

    if login_type == "code":
        return await _login_by_code(req.account, req.code, request)
    else:
        return await _login_by_password(req.account, req.password, request)


async def _login_by_password(account: str, password: str, request: Request) -> LoginResponse:
    """密码登录：根据 account 格式判断凭证类型"""
    account = (account or "").strip()
    if not account or not password:
        raise HTTPException(status_code=400, detail="请填写账号和密码")

    # 判断 account 格式
    if "@" in account:
        # 邮箱登录
        if await _get_cfg("farmer_email_password_login", "1") != "1":
            raise HTTPException(status_code=403, detail="邮箱密码登录功能未开启")
        field = "email"
    elif _PHONE_RE.match(account):
        # 手机号登录
        if await _get_cfg("farmer_phone_password_login", "1") != "1":
            raise HTTPException(status_code=403, detail="手机号密码登录功能未开启")
        field = "phone"
    else:
        # 用户名登录
        field = "username"

    # 检查账户是否被锁定（username+IP 组合键，防恶意锁定 DoS）
    client_ip = request.client.host if request.client else ""
    lock_msg = await check_login_allowed(account, client_ip)
    if lock_msg:
        raise HTTPException(status_code=423, detail=lock_msg)

    # 查找农户并验证密码
    if field == "username":
        farmer = await get_farmer_by_username(account)
        if not farmer or farmer["status"] != 1:
            await record_login_failure(account, client_ip)
            raise HTTPException(status_code=401, detail="账号或密码错误")
        farmer_password = farmer["password"]
    else:
        farmer = await _find_farmer_by_field(field, account)
        if not farmer or farmer["status"] != 1:
            await record_login_failure(account, client_ip)
            raise HTTPException(status_code=401, detail="账号或密码错误")
        farmer_password = await verify_farmer_password(farmer["id"])

    if not verify_password(password, farmer_password):
        await record_login_failure(account, client_ip)
        raise HTTPException(status_code=401, detail="账号或密码错误")

    await clear_login_failure(account, client_ip)

    # 渐进式迁移密码
    if needs_rehash(farmer_password):
        new_hash = hash_password(password)
        await update_farmer_password(farmer["id"], new_hash)
        logger.info("农户 %s 密码已从 SHA-256 迁移至 bcrypt", farmer["username"])

    session_duration = await get_farmer_session_duration()
    token = create_farmer_token(
        farmer["id"], farmer["username"], expire_seconds=session_duration)

    ip = request.client.host if request.client else ""
    await update_farmer_login_info(farmer["id"], ip)

    await active_log("农户登录", "farmer_login", rel_id=farmer["id"], request=request)
    # 触发 farmer_login 钩子（审计/联动用，fire-and-forget 不阻塞）
    await emit_farmer_login(farmer["id"], ip)
    return LoginResponse(token=token, user_id=farmer["id"], username=farmer["username"])


async def _login_by_username(username: str, password: str, request: Request) -> LoginResponse:
    """传统用户名+密码登录（向后兼容）"""
    # username+IP 组合键，防恶意锁定 DoS
    client_ip = request.client.host if request.client else ""
    lock_msg = await check_login_allowed(username, client_ip)
    if lock_msg:
        raise HTTPException(status_code=423, detail=lock_msg)

    farmer = await get_farmer_by_username(username)
    if not farmer or farmer["status"] != 1:
        await record_login_failure(username, client_ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not verify_password(password, farmer["password"]):
        await record_login_failure(username, client_ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    await clear_login_failure(username, client_ip)

    if needs_rehash(farmer["password"]):
        new_hash = hash_password(password)
        await update_farmer_password(farmer["id"], new_hash)

    session_duration = await get_farmer_session_duration()
    token = create_farmer_token(
        farmer["id"], farmer["username"], expire_seconds=session_duration)

    ip = request.client.host if request.client else ""
    await update_farmer_login_info(farmer["id"], ip)

    await active_log("农户登录", "farmer_login", rel_id=farmer["id"], request=request)
    # 触发 farmer_login 钩子（审计/联动用，fire-and-forget 不阻塞）
    await emit_farmer_login(farmer["id"], ip)
    return LoginResponse(token=token, user_id=farmer["id"], username=farmer["username"])


async def _login_by_code(account: str, code: str, request: Request) -> LoginResponse:
    """验证码登录（支持手机号和邮箱）"""
    account = (account or "").strip()
    code = (code or "").strip()

    if not account or not code:
        raise HTTPException(status_code=400, detail="请填写账号和验证码")

    # 判断账号类型并校验配置开关
    if "@" in account:
        # 邮箱验证码登录
        if await _get_cfg("farmer_email_code_login", "1") != "1":
            raise HTTPException(status_code=403, detail="邮箱验证码登录功能未开启")
        field = "email"
    else:
        # 手机短信验证码登录
        if await _get_cfg("farmer_phone_sms_login", "1") != "1":
            raise HTTPException(status_code=403, detail="短信验证码登录功能未开启")
        field = "phone"

    # 验证码比对
    success, msg = await verify_code_service(account, code, "login")
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    # 查找用户
    farmer = await _find_farmer_by_field(field, account)
    if not farmer:
        raise HTTPException(status_code=400, detail="该账号未注册")
    if farmer["status"] != 1:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    session_duration = await get_farmer_session_duration()
    token = create_farmer_token(
        farmer["id"], farmer["username"], expire_seconds=session_duration)

    ip = request.client.host if request.client else ""
    await update_farmer_login_info(farmer["id"], ip)

    await active_log("农户验证码登录", "farmer_login", rel_id=farmer["id"], request=request)
    # 触发 farmer_login 钩子（审计/联动用，fire-and-forget 不阻塞）
    await emit_farmer_login(farmer["id"], ip)
    return LoginResponse(token=token, user_id=farmer["id"], username=farmer["username"])


@router.get("/me")
async def me(request: Request, _: None = Depends(check_farmer)):
    """获取当前农户信息 — 含常用字段"""
    user_id = request.state.user_id
    farmer = await get_farmer_by_id(user_id)
    if not farmer:
        return ok({"id": user_id, "name": request.state.user_name, "is_admin": False})

    return ok({
        "id": farmer["id"],
        "name": farmer["username"],
        "nickname": farmer.get("nickname", ""),
        "avatar": farmer.get("avatar", ""),
        "email": farmer.get("email", ""),
        "phone": farmer.get("phone", ""),
        "company": farmer.get("company", ""),
        "is_admin": False
    })
