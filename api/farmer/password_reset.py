# -*- coding: utf-8 -*-
"""农户端密码找回 API"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth.middleware_chain import reject_repeat
from core.auth.password import hash_password
from core.auth.security_policy import is_password_complexity_enabled, check_password_complexity
from core.verify_code import send_verify_code, verify_code
from core.farmer_service import list_farmers, update_farmer_password
from api.farmer.verify_code import fuzzy_send_response, enforce_send_interval, _FUZZY_SEND_MESSAGE
from schemas.auth import PasswordResetSendRequest, PasswordResetVerifyRequest
from core.response import ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/password-reset", tags=["密码找回"])

# 手机号正则（11位中国大陆号码）
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
# 邮箱正则
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _detect_channel(target: str) -> str:
    """根据 target 格式自动判断渠道"""
    if _PHONE_RE.match(target):
        return "sms"
    if _EMAIL_RE.match(target):
        return "email"
    return ""


async def _find_farmer_by_target(target: str, channel: str) -> dict | None:
    """按手机号或邮箱精确查找农户（利用 list_farmers 的 contains 过滤 + Python 精确匹配）"""
    search_field = "phone" if channel == "sms" else "email"
    result = await list_farmers(keywords=target, search_field=search_field, limit=10)
    for f in result["list"]:
        if f.get(search_field) == target:
            return f
    return None


@router.post("/send")
async def password_reset_send(req: PasswordResetSendRequest, request: Request):
    """
    密码找回 - 发送验证码

    target: 手机号或邮箱
    channel: sms / email（可省略，自动根据 target 格式判断）
    """
    target = (req.target or "").strip()
    channel = (req.channel or "").strip().lower()

    # 自动判断渠道
    if not channel:
        channel = _detect_channel(target)
    if channel not in ("sms", "email"):
        raise HTTPException(status_code=400, detail="无法识别的目标格式，请输入手机号或邮箱")

    # 格式校验
    if channel == "sms" and not _PHONE_RE.match(target):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    if channel == "email" and not _EMAIL_RE.match(target):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")

    # 枚举模糊化闭环：查库前统一记账（key 与 /send-code 的 reset 用途共用）
    await enforce_send_interval("reset", target)

    # 校验 target 已注册
    farmer = await _find_farmer_by_target(target, channel)

    if not farmer:
        # 枚举模糊化：不透出账号是否注册，返回与正常发送同构的假成功（实际未发送）
        return await fuzzy_send_response()

    # 发送验证码
    ip = request.client.host if request.client else ""
    success, message, extra = await send_verify_code(
        target=target, purpose="reset", channel=channel, ip=ip
    )

    if not success:
        raise HTTPException(status_code=429, detail=message)

    # 真实发送也用模糊文案，与不存在分支不可区分
    return ok(extra, msg=_FUZZY_SEND_MESSAGE)


@router.post("/verify", dependencies=[Depends(reject_repeat)])
async def password_reset_verify(req: PasswordResetVerifyRequest):
    """
    密码找回 - 验证码比对 + 重置密码（已挂载防重复提交）

    验证码通过后直接设置新密码
    """
    target = (req.target or "").strip()
    code = (req.code or "").strip()
    new_password = req.new_password

    if not target or not code or not new_password:
        raise HTTPException(status_code=400, detail="参数不完整")

    # 密码复杂度校验
    if await is_password_complexity_enabled():
        err = check_password_complexity(new_password)
        if err:
            raise HTTPException(status_code=400, detail=f"密码不符合安全策略: {err}")

    # 验证码比对
    success, msg = await verify_code(target, code, "reset")
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    # 确定渠道并查找用户
    channel = _detect_channel(target)
    farmer = await _find_farmer_by_target(target, channel)

    if not farmer:
        raise HTTPException(status_code=400, detail="用户不存在")

    # 更新密码
    pw_hash = hash_password(new_password)
    await update_farmer_password(farmer["id"], pw_hash)

    logger.info("[密码找回] 用户 %s 密码已重置 (target=%s)", farmer.get("username", ""), target)
    return ok(msg="密码重置成功")
