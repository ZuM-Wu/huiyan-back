# -*- coding: utf-8 -*-
"""农户端验证码发送 API"""
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from core.config_service import get_config
from core.farmer_service import farmer_contact_exists
from core.rate_limiter import check_rate_detail
from core.verify_code import send_verify_code
from schemas.auth import SendCodeRequest
from core.response import ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["验证码"])

# 手机号正则（11位中国大陆号码）
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
# 邮箱正则
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 枚举模糊化统一文案：登录/找回密码发码不透出账号是否存在
_FUZZY_SEND_MESSAGE = "若该账号存在，验证码已发送"


async def _get_cfg(key: str, default: str = "") -> str:
    val = await get_config(key)
    return val if val is not None else default


def _validate_format(target: str, channel: str):
    """校验目标格式（手机号/邮箱）"""
    if channel == "sms":
        if not _PHONE_RE.match(target):
            raise HTTPException(status_code=400, detail="手机号格式不正确")
    elif not _EMAIL_RE.match(target):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")


async def _check_config_switch(purpose: str, channel: str):
    """配置开关校验：对应功能是否已开启"""
    if purpose == "login":
        if channel == "sms" and await _get_cfg("farmer_phone_sms_login", "1") != "1":
            raise HTTPException(status_code=403, detail="短信验证码登录功能未开启")
        if channel == "email" and await _get_cfg("farmer_email_code_login", "1") != "1":
            raise HTTPException(status_code=403, detail="邮箱验证码登录功能未开启")
    elif purpose == "register":
        if channel == "sms" and await _get_cfg("farmer_allow_phone_register", "1") != "1":
            raise HTTPException(status_code=403, detail="手机号注册功能未开启")
        if channel == "email" and await _get_cfg("farmer_allow_email_register", "1") != "1":
            raise HTTPException(status_code=403, detail="邮箱注册功能未开启")


async def fuzzy_send_response() -> dict:
    """账号不存在时的模糊化响应：与正常发送同构（防账号枚举，实际未发送）"""
    expire = int(await _get_cfg("sms_code_expire", "300"))
    interval = int(await _get_cfg("sms_code_interval", "60"))
    return ok({"expire": expire, "interval": interval}, msg=_FUZZY_SEND_MESSAGE)


async def enforce_send_interval(purpose: str, target: str):
    """发码间隔统一记账（查库前执行，真假分支同构）

    补齐枚举模糊化侧信道：真实分支有 DB 间隔检查（二次请求 429）而假分支永远 200，
    攻击者可凭连发二次响应差异探测账号存在性；此处对 login/reset 用途不分真假统一频控。
    DB 层间隔检查保留作为进程重启后的兜底。
    """
    interval = int(await _get_cfg("sms_code_interval", "60"))
    allowed, retry_after = check_rate_detail(f"send:{purpose}:{target}", 1, interval)
    if not allowed:
        # 与 core/verify_code.py 真实分支的 429 文案同构，不可区分
        raise HTTPException(status_code=429, detail=f"发送过于频繁，请 {retry_after} 秒后重试")


async def _check_registration_status(target: str, channel: str, purpose: str) -> bool:
    """业务校验：注册需未注册（注册页依赖明确提示）；登录/重置返回账号是否存在，由调用方模糊化处理"""
    exists = await farmer_contact_exists(target, channel)
    if purpose == "register" and exists:
        raise HTTPException(status_code=400, detail="该账号已被注册")
    return exists


@router.post("/send-code")
async def api_send_code(req: SendCodeRequest, request: Request):
    """
    发送验证码

    - purpose=login: target 必须已注册（phone 存在于 hy_farmer）
    - purpose=reset: target 必须已注册（phone 或 email 存在于 hy_farmer）
    - purpose=register: target 不能已被注册
    """
    target = (req.target or "").strip()
    purpose = (req.purpose or "").strip().lower()
    channel = (req.channel or "sms").strip().lower()

    if purpose not in ("login", "reset", "register"):
        raise HTTPException(status_code=400, detail="无效的用途参数")
    if channel not in ("sms", "email"):
        raise HTTPException(status_code=400, detail="无效的渠道参数")

    _validate_format(target, channel)
    await _check_config_switch(purpose, channel)

    # 枚举模糊化闭环：login/reset 用途在查库前统一记账，register 保持明确提示逻辑
    if purpose in ("login", "reset"):
        await enforce_send_interval(purpose, target)

    exists = await _check_registration_status(target, channel, purpose)

    # 枚举模糊化：登录/找回密码场景下账号不存在时返回同构假成功，不透出注册状态
    if purpose in ("login", "reset") and not exists:
        return await fuzzy_send_response()

    # 发送验证码
    ip = request.client.host if request.client else ""
    success, message, extra = await send_verify_code(
        target=target, purpose=purpose, channel=channel, ip=ip
    )

    if not success:
        raise HTTPException(status_code=429, detail=message)

    # 登录/找回密码的真实发送也用模糊文案，与不存在分支不可区分
    if purpose in ("login", "reset"):
        message = _FUZZY_SEND_MESSAGE

    return ok(extra, msg=message)
