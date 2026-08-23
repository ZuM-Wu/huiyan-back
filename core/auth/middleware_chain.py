"""
中间件链
使用 FastAPI Depends 实现路由级中间件注册

中间件执行顺序:
  CheckAdmin/CheckFarmer(登录校验) -> RejectRepeat(防重复提交) -> Controller
"""

import hashlib
import logging
import time
from typing import Optional

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.auth.jwt_handler import verify_jwt
from core.db.base import async_session_factory
from sqlalchemy import select

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

# 简易内存缓存（防重复提交用，配套过期清理任务见 task_manager）
_repeat_cache: dict = {}


# ============================================================
# CheckAdmin — 后台登录校验
# ============================================================

async def _check_ip_whitelist(request: Request) -> None:
    """
    IP 白名单校验
    从 hy_configuration 读取 ip_whitelist_enabled / ip_whitelist
    开启时仅允许白名单 IP 访问后台
    """
    from core.auth.security_policy import get_config_int, get_config_value

    enabled = await get_config_int("ip_whitelist_enabled", 0)
    if not enabled:
        return

    whitelist_raw = await get_config_value("ip_whitelist", "")
    if not whitelist_raw.strip():
        return

    # 解析白名单 IP（支持换行和逗号分隔）
    allowed_ips = set()
    for line in whitelist_raw.replace(",", "\n").split("\n"):
        ip = line.strip()
        if ip:
            allowed_ips.add(ip)

    if not allowed_ips:
        return

    client_ip = request.client.host if request.client else ""
    # 超管(id=1) 和 localhost 始终放行
    if client_ip in ("127.0.0.1", "::1"):
        return

    if client_ip not in allowed_ips:
        logger.warning(f"[IP 白名单] 拒绝访问: {client_ip}")
        raise HTTPException(status_code=403, detail="您的 IP 不在后台访问白名单中")

async def check_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> None:
    """
    验证管理员身份
    未通过验证返回 401，IP 不在白名单返回 403
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="未提供认证令牌")

    payload = verify_jwt(credentials.credentials, is_admin=True)
    if not payload:
        raise HTTPException(status_code=401, detail="无效或过期的管理员令牌")

    if not payload.get("is_admin"):
        raise HTTPException(status_code=401, detail="无权访问后台接口")

    # IP 白名单校验
    await _check_ip_whitelist(request)

    # 查询管理员状态（检查是否被禁用）
    async with async_session_factory() as db:
        from core.db.admin import Admin
        result = await db.execute(select(Admin).where(Admin.id == payload["id"]))
        admin = result.scalar_one_or_none()
        if not admin:
            raise HTTPException(status_code=401, detail="管理员不存在")
        if admin.status != 1:
            raise HTTPException(status_code=401, detail="管理员账户已被禁用")

    request.state.user_id = payload["id"]
    request.state.user_name = payload.get("name", "")
    request.state.user_type = "admin"
    request.state.is_admin = True


# ============================================================
# CheckFarmer — 前台登录校验
# ============================================================

async def check_farmer(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> None:
    """
    验证农户身份
    未通过验证返回 401
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="未提供认证令牌")

    payload = verify_jwt(credentials.credentials, is_admin=False)
    if not payload:
        raise HTTPException(status_code=401, detail="无效或过期的农户令牌")

    if payload.get("is_admin"):
        raise HTTPException(status_code=401, detail="无权访问农户接口")

    # 查询农户状态（检查是否被禁用）
    async with async_session_factory() as db:
        from core.db.farmer import Farmer
        result = await db.execute(select(Farmer).where(Farmer.id == payload["id"]))
        farmer = result.scalar_one_or_none()
        if not farmer:
            raise HTTPException(status_code=401, detail="农户不存在")
        if farmer.status != 1:
            raise HTTPException(status_code=401, detail="农户账户已被禁用")

    request.state.user_id = payload["id"]
    request.state.user_name = payload.get("name", "")
    request.state.user_type = "farmer"
    request.state.is_admin = False


# ============================================================
# RejectRepeat — 防重复提交
# ============================================================

async def reject_repeat(request: Request) -> None:
    """
    防重复提交
    仅对 POST/PUT 请求生效，3秒内相同请求拒绝
    """
    if request.method not in ("POST", "PUT"):
        return

    # 清理过期缓存
    now = time.time()
    expired_keys = [k for k, v in _repeat_cache.items() if v.get("expire", 0) < now]
    for k in expired_keys:
        del _repeat_cache[k]

    # 生成请求指纹: IP + URL + Body SHA-256
    body_bytes = await request.body()
    client_ip = request.client.host if request.client else "unknown"
    raw = f"{client_ip}:{request.url.path}:{body_bytes.decode('utf-8', errors='replace')}"
    request_hash = hashlib.sha256(raw.encode()).hexdigest()

    if request_hash in _repeat_cache:
        logger.warning(f"[RejectRepeat] 检测到重复提交: {client_ip} -> {request.url.path}")
        raise HTTPException(status_code=429, detail="请勿重复提交，请稍后再试")

    _repeat_cache[request_hash] = {"expire": now + 3, "ip": client_ip}
