# -*- coding: utf-8 -*-
"""
验证码核心服务
提供验证码的生成、发送、频率限制与比对验证功能
"""
import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Any, Tuple, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from core.db.base import async_session_factory
from core.db.verify_code import VerifyCode
from core.config_manager import ConfigManager
from core.notice_sender import notice_sender
from core.rate_limiter import check_rate

logger = logging.getLogger(__name__)

# 防爆破阈值：同一条验证码累计失败达到此次数后自动作废
_MAX_ATTEMPTS = 5


def generate_code(length: int = 6) -> str:
    """生成随机纯数字验证码（CSPRNG，防可预测性攻击）"""
    return "".join(secrets.choice(string.digits) for _ in range(length))


async def _get_config(db, key: str, default: str = "") -> str:
    """读取单个配置（内部辅助）"""
    cm = ConfigManager()
    value = await cm.get(key, db)
    return value if value is not None else default


async def send_verify_code(
    target: str,
    purpose: str,
    channel: str,
    ip: str = "",
) -> Tuple[bool, str, dict]:
    """
    发送验证码（含频率限制和过期清理）

    Args:
        target: 接收目标（手机号/邮箱）
        purpose: 用途 login / reset / register
        channel: 渠道 sms / email
        ip: 请求 IP（审计记录）

    Returns:
        (success, message, extra)
        extra 包含 expire / interval 等信息供前端展示
    """
    async with async_session_factory() as db:
        # 读取配置
        interval = int(await _get_config(db, "sms_code_interval", "60"))
        expire_seconds = int(await _get_config(db, "sms_code_expire", "300"))
        code_length = int(await _get_config(db, "sms_code_length", "6"))

        # 频率限制：同 target+purpose 距上次发送不足 interval 秒时拒绝
        now = datetime.now()
        result = await db.execute(
            select(VerifyCode)
            .where(
                VerifyCode.target == target,
                VerifyCode.purpose == purpose,
            )
            .order_by(VerifyCode.id.desc())
            .limit(1)
        )
        last_record = result.scalar_one_or_none()
        if last_record:
            elapsed = (now - last_record.create_time).total_seconds()
            if elapsed < interval:
                remaining = int(interval - elapsed)
                return (
                    False,
                    f"发送过于频繁，请 {remaining} 秒后重试",
                    {"retry_after": remaining},
                )

        # 清理旧的未使用验证码（同 target+purpose 标记过期）
        await db.execute(
            update(VerifyCode)
            .where(
                VerifyCode.target == target,
                VerifyCode.purpose == purpose,
                VerifyCode.used == 0,
            )
            .values(used=2)  # 2=已作废
        )

        # 生成新验证码
        code = generate_code(code_length)
        expire_time = now + timedelta(seconds=expire_seconds)

        record = VerifyCode(
            target=target,
            code=code,
            purpose=purpose,
            channel=channel,
            used=0,
            expire_time=expire_time,
            ip=ip,
            create_time=now,
        )
        db.add(record)
        await db.commit()

    # 通过通知发送器发送验证码
    action_key = "verify_code_login" if purpose == "login" else "verify_code_register"
    if purpose == "reset":
        action_key = "password_reset"

    try:
        await notice_sender.send(
            action_key=action_key,
            recipient=target,
            channel=channel,
            variables={
                "code": code,
                "expire": str(expire_seconds // 60),
                "minutes": str(expire_seconds // 60),
            },
        )
    except ValueError as e:
        # 通知配置未完成时降级：验证码仍写入数据库，仅记录警告
        logger.warning(f"[验证码] 通知发送失败（配置不完整）: {e}，验证码已入库可供测试")

    return (
        True,
        "验证码已发送",
        {"expire": expire_seconds, "interval": interval},
    )


async def verify_code(target: str, code: str, purpose: str) -> Tuple[bool, str]:
    """
    比对验证码（含防爆破：频控 + 失败计数作废）

    Args:
        target: 接收目标
        code: 用户输入的验证码
        purpose: 用途

    Returns:
        (success, message)
    """
    # 频控：同 target 每分钟最多 10 次校验尝试（进程内滑动窗口，单 worker 部署下全局有效）
    if not check_rate(f"verify:{target}", limit=10, window=60):
        return False, "操作过于频繁，请稍后再试"

    async with async_session_factory() as db:
        now = datetime.now()
        result = await db.execute(
            select(VerifyCode)
            .where(
                VerifyCode.target == target,
                VerifyCode.purpose == purpose,
                VerifyCode.used == 0,
                VerifyCode.expire_time > now,
            )
            .order_by(VerifyCode.id.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()

        if not record:
            return False, "验证码无效或已过期"

        if record.code != code:
            # 失败计数原子累加：避免并发读改写丢失计数，防就地穷举爆破
            await db.execute(
                update(VerifyCode)
                .where(VerifyCode.id == record.id)
                .values(attempt_count=func.coalesce(VerifyCode.attempt_count, 0) + 1)
            )
            # 达阈值时条件作废（与累加同事务提交，凭 rowcount 判断是否命中）
            invalidated = cast(CursorResult[Any], await db.execute(
                update(VerifyCode)
                .where(
                    VerifyCode.id == record.id,
                    VerifyCode.attempt_count >= _MAX_ATTEMPTS,
                )
                .values(used=2)  # 2=已作废
            ))
            await db.commit()
            if invalidated.rowcount:
                return False, "验证码已失效，请重新获取"
            return False, "验证码错误"

        # 标记已使用
        record.used = 1
        await db.commit()

    return True, "验证通过"
