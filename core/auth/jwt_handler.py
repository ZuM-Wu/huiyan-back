"""
JWT 认证模块
提供 Admin 和 Farmer 双密钥体系的 JWT 签发和验证
"""

from datetime import timedelta
from core.time_utils import china_now_aware
from typing import Optional, Dict

from jose import jwt, JWTError

from core.config import settings

# JWT 算法
ALGORITHM = "HS256"


def create_jwt(data: Dict, is_admin: bool = True, expire_seconds: Optional[int] = None) -> str:
    """
    签发 JWT

    参数:
        data:           载荷数据，至少包含 {"id": 1, "name": "xxx"}
        is_admin:       True=使用 admin 密钥, False=使用 farmer 密钥
        expire_seconds: 自定义过期时长（秒），None 则使用默认配置
    返回:
        JWT 字符串
    """
    key = settings.JWT_KEY_ADMIN if is_admin else settings.JWT_KEY_FARMER
    expire = expire_seconds if expire_seconds is not None else settings.JWT_EXPIRE_SECONDS

    now = china_now_aware()
    payload = {
        "id": data["id"],
        "name": data.get("name", ""),
        "is_admin": is_admin,
        "iat": now,
        "exp": now + timedelta(seconds=expire),
    }
    return jwt.encode(payload, key, algorithm=ALGORITHM)


def verify_jwt(token: str, is_admin: bool = True) -> Optional[Dict]:
    """
    验证 JWT

    参数:
        token:    JWT 字符串
        is_admin: True=使用 admin 密钥验证, False=使用 farmer 密钥验证
    返回:
        载荷字典，验证失败返回 None
    """
    key = settings.JWT_KEY_ADMIN if is_admin else settings.JWT_KEY_FARMER
    try:
        payload = jwt.decode(token, key, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def create_admin_token(admin_id: int, username: str, expire_seconds: Optional[int] = None) -> str:
    """创建管理员 JWT"""
    return create_jwt({"id": admin_id, "name": username}, is_admin=True, expire_seconds=expire_seconds)


def create_farmer_token(farmer_id: int, username: str, expire_seconds: Optional[int] = None) -> str:
    """创建农户 JWT"""
    return create_jwt({"id": farmer_id, "name": username}, is_admin=False, expire_seconds=expire_seconds)
