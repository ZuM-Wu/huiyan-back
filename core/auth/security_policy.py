"""
安全策略模块
从 hy_configuration 表读取安全配置并提供校验函数

功能:
- 密码复杂度校验
- 登录失败锁定判定
- JWT 会话时长获取
"""

import re
import logging
from typing import Optional

from sqlalchemy import select
from core.db.base import async_session_factory
from core.db.configuration import ConfigurationModel

logger = logging.getLogger(__name__)


async def get_config_value(key: str, default: str = "") -> str:
    """从 hy_configuration 表读取单个配置值"""
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(ConfigurationModel.value).where(ConfigurationModel.key == key)
            )
            row = result.first()
            return row[0] if row else default
    except Exception as e:
        logger.warning(f"[安全策略] 读取配置 {key} 失败，使用默认值: {e}")
        return default


async def get_config_int(key: str, default: int = 0) -> int:
    """从 hy_configuration 表读取整数配置值"""
    val = await get_config_value(key, str(default))
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def check_password_complexity(password: str) -> Optional[str]:
    """
    校验密码复杂度
    规则: 至少 8 位，包含大写字母、小写字母、数字、特殊字符中的至少 3 种

    返回:
        None — 校验通过
        str  — 错误描述
    """
    if len(password) < 8:
        return "密码长度至少 8 位"

    categories = 0
    if re.search(r'[A-Z]', password):
        categories += 1
    if re.search(r'[a-z]', password):
        categories += 1
    if re.search(r'[0-9]', password):
        categories += 1
    if re.search(r'[^A-Za-z0-9]', password):
        categories += 1

    if categories < 3:
        return "密码需包含大写字母、小写字母、数字、特殊字符中的至少 3 种"

    return None


# ================================================================
# 登录失败锁定 — 使用内存字典暂存失败计数（单 worker 部署下合法）
# ================================================================

# {"username|ip": {"count": int, "locked_until": float_timestamp}}
# 键采用 username+IP 组合：防止攻击者仅凭用户名恶意锁定他人账户（锁定 DoS）
_login_fail_cache: dict = {}


def _lock_key(username: str, ip: str) -> str:
    """生成锁定缓存键（username+IP 组合维度）"""
    return f"{username}|{ip}"


async def check_login_allowed(username: str, ip: str = "") -> Optional[str]:
    """
    检查用户是否允许登录（未被锁定）

    返回:
        None — 允许登录
        str  — 拒绝原因
    """
    import time

    key = _lock_key(username, ip)
    record = _login_fail_cache.get(key)
    if not record:
        return None

    locked_until = record.get("locked_until", 0)
    if locked_until > time.time():
        remaining = int(locked_until - time.time())
        return f"账户已锁定，请 {remaining} 秒后再试"

    # 锁定已过期，清除记录
    if locked_until > 0 and locked_until <= time.time():
        _login_fail_cache.pop(key, None)

    return None


async def record_login_failure(username: str, ip: str = "") -> None:
    """
    记录一次登录失败，达到上限时锁定账户
    """
    import time

    retry_limit = await get_config_int("login_retry_limit", 5)
    lock_duration = await get_config_int("login_lock_duration", 900)

    key = _lock_key(username, ip)
    record = _login_fail_cache.get(key, {"count": 0, "locked_until": 0})
    record["count"] += 1

    if record["count"] >= retry_limit:
        record["locked_until"] = time.time() + lock_duration
        record["count"] = 0  # 锁定后重置计数
        logger.warning(f"[安全策略] 用户 {username}（IP={ip or '未知'}）登录失败 {retry_limit} 次，已锁定 {lock_duration} 秒")
    else:
        logger.info(f"[安全策略] 用户 {username}（IP={ip or '未知'}）登录失败第 {record['count']}/{retry_limit} 次")

    _login_fail_cache[key] = record


async def clear_login_failure(username: str, ip: str = "") -> None:
    """登录成功后清除失败计数"""
    _login_fail_cache.pop(_lock_key(username, ip), None)


async def get_session_duration() -> int:
    """获取登录会话时长（秒），优先读数据库配置"""
    return await get_config_int("login_session_duration", 7200)


async def is_password_complexity_enabled() -> bool:
    """检查密码复杂度策略是否开启"""
    val = await get_config_int("force_password_complexity", 1)
    return val == 1


async def validate_password_or_400(password: str) -> None:
    """
    密码复杂度校验共享辅助（admin.py / farmer.py 同构逻辑收敛）

    策略开启且不达标时直接抛 400，调用方无需再写三层嵌套判断。
    """
    from fastapi import HTTPException
    if await is_password_complexity_enabled():
        err = check_password_complexity(password)
        if err:
            raise HTTPException(status_code=400, detail=f"密码不符合安全策略: {err}")


# ================================================================
# 前台（农户）访问设置
# ================================================================


async def is_farmer_register_allowed() -> bool:
    """检查是否允许农户注册"""
    return await get_config_int("allow_farmer_register", 1) == 1


async def check_farmer_email_suffix(email: str) -> Optional[str]:
    """
    校验农户注册邮箱后缀是否在允许列表内。

    仅当开启「限制邮箱后缀」且邮箱非空时才校验。

    返回:
        None — 校验通过（未开启限制 / 邮箱为空 / 后缀合法）
        str  — 错误描述
    """
    if await get_config_int("farmer_email_suffix_enabled", 0) != 1:
        return None
    if not email:
        return None

    raw = await get_config_value("farmer_email_suffixes", "")
    # 允许列表为空时视为不通过任何邮箱，给出明确提示
    suffixes = [s.strip().lower() for s in raw.replace("，", ",").split(",") if s.strip()]
    if not suffixes:
        return "当前未配置允许的邮箱后缀，暂不支持邮箱注册"

    email_lower = email.strip().lower()
    if any(email_lower.endswith(sfx) for sfx in suffixes):
        return None
    return f"邮箱后缀不被允许，仅支持：{raw}"


async def get_farmer_session_duration() -> int:
    """获取农户登录会话时长（秒），优先读农户专用配置，缺失回退全局会话时长"""
    val = await get_config_int("farmer_session_duration", 0)
    if val > 0:
        return val
    return await get_config_int("login_session_duration", 7200)


async def is_farmer_register_phone_required() -> bool:
    """检查农户注册时手机号是否必填"""
    return await get_config_int("farmer_register_phone_required", 0) == 1


async def is_farmer_register_email_required() -> bool:
    """检查农户注册时邮箱是否必填"""
    return await get_config_int("farmer_register_email_required", 0) == 1
