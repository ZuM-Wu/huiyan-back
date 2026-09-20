"""
管理员账户设置 API
提供当前登录管理员查看/修改个人信息、修改密码功能
"""
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import select

from core.admin_service import (
    get_admin_by_id,
    get_admin_by_username,
    update_admin as svc_update_admin,
)
from core.auth.middleware_chain import check_admin
from core.auth.password import hash_password, verify_password
from core.db.admin_preference import AdminPreference
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok
from schemas.profile import AdminProfileUpdate, PasswordChange, TableColumnPreferenceUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1/profile", tags=["账户设置"])
_TABLE_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_.:-]{1,96}$")


def _preference_key(table_key: str) -> str:
    """将公开 table key 收敛到管理员偏好命名空间。"""
    if not _TABLE_KEY_PATTERN.fullmatch(table_key):
        raise HTTPException(status_code=422, detail="table_key 格式不正确")
    return f"table-columns:{table_key}"


def _clean_keys(values: list[str]) -> list[str]:
    """去重并限制字段 key 长度，避免异常输入膨胀偏好文本。"""
    return list(dict.fromkeys(
        item for item in values
        if isinstance(item, str) and 0 < len(item) <= 96
    ))


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


@router.get("/preferences/table-columns/{table_key}")
async def get_table_column_preference(
    request: Request,
    table_key: str = Path(..., description="列表稳定标识"),
    _: None = Depends(check_admin),
):
    """读取当前管理员某个列表的字段隐藏偏好。"""
    key = _preference_key(table_key)
    async with async_session_factory() as db:
        result = await db.execute(select(AdminPreference.preference_value).where(
            AdminPreference.admin_id == request.state.user_id,
            AdminPreference.preference_key == key,
        ))
        raw = result.scalar_one_or_none()
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    hidden = value.get("hidden", [])
    if not isinstance(hidden, list):
        hidden = []
    return ok({
        "table_key": table_key,
        "hidden": _clean_keys(hidden),
        "configured": raw is not None,
    })


@router.put("/preferences/table-columns/{table_key}")
async def save_table_column_preference(
    body: TableColumnPreferenceUpdate,
    request: Request,
    table_key: str = Path(..., description="列表稳定标识"),
    _: None = Depends(check_admin),
):
    """保存当前管理员某个列表的字段隐藏偏好并校验列元数据。"""
    key = _preference_key(table_key)
    available = set(_clean_keys(body.available))
    required = set(_clean_keys(body.required)) & available
    hidden = [
        item for item in _clean_keys(body.hidden)
        if item in available and item not in required
    ]
    value = json.dumps({"hidden": hidden}, ensure_ascii=False, separators=(",", ":"))
    async with async_session_factory() as db:
        result = await db.execute(select(AdminPreference).where(
            AdminPreference.admin_id == request.state.user_id,
            AdminPreference.preference_key == key,
        ))
        preference = result.scalar_one_or_none()
        if preference:
            preference.preference_value = value
        else:
            db.add(AdminPreference(
                admin_id=request.state.user_id,
                preference_key=key,
                preference_value=value,
            ))
        await db.commit()
    return ok({"table_key": table_key, "hidden": hidden}, msg="字段显示设置已保存")
