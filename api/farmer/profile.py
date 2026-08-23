"""农户个人信息 API"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.exc import IntegrityError

from core.auth.middleware_chain import check_farmer
from core.auth.password import hash_password, verify_password
from core.auth.security_policy import validate_password_or_400
from core.farmer_service import (
    get_farmer_by_id, update_farmer, verify_farmer_password, update_farmer_password,
)
from core.log.active_log import active_log
from core.response import ok
from schemas.profile import PasswordChange
from schemas.farmer import FarmerProfileUpdate
from services.image_upload import save_uploaded_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/profile", tags=["农户信息"])

@router.get("")
async def get_profile(request: Request, _: None = Depends(check_farmer)):
    """获取个人信息"""
    farmer = await get_farmer_by_id(request.state.user_id)
    if not farmer:
        raise HTTPException(status_code=404, detail="农户不存在")
    return ok({
        "id": farmer["id"], "username": farmer["username"],
        "nickname": farmer.get("nickname", "") or "",
        "avatar": farmer.get("avatar", "") or "",
        "email": farmer.get("email", "") or "",
        "phone": farmer.get("phone", "") or "", "company": farmer.get("company", "") or "",
        "status": farmer["status"],
        "create_time": farmer.get("create_time"),
    })


@router.put("")
async def update_profile(data: FarmerProfileUpdate, request: Request, _: None = Depends(check_farmer)):
    """更新个人信息（nickname/email/phone/company）"""
    update_data = data.model_dump(exclude_unset=True)
    # 手机号/邮箱空串归一为 NULL，与注册逻辑一致：
    # 唯一索引下多个 NULL 合法，而多个空串会误撞唯一索引
    for key in ("phone", "email"):
        if key in update_data and isinstance(update_data[key], str):
            update_data[key] = update_data[key].strip() or None
    try:
        success = await update_farmer(request.state.user_id, update_data)
    except IntegrityError:
        # 手机号/邮箱存在唯一索引，撞库时返回友好提示（避免 500）
        raise HTTPException(status_code=400, detail="该手机号/邮箱已被使用")
    if not success:
        raise HTTPException(status_code=404, detail="农户不存在")

    await active_log(
        description="农户修改个人信息",
        log_type="farmer_profile",
        request=request,
    )
    return ok(msg="个人信息已更新")


@router.put("/password")
async def change_password(data: PasswordChange, request: Request, _: None = Depends(check_farmer)):
    """修改当前农户密码"""
    farmer_id = request.state.user_id
    current_hash = await verify_farmer_password(farmer_id)
    if not current_hash:
        raise HTTPException(status_code=404, detail="农户不存在")

    if not verify_password(data.old_password, current_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")

    if verify_password(data.new_password, current_hash):
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    # 密码复杂度校验：与注册/重置流程一致，防止经改密入口绕过全局密码策略
    await validate_password_or_400(data.new_password)

    await update_farmer_password(farmer_id, hash_password(data.new_password))

    await active_log(
        description="农户修改密码",
        log_type="farmer_profile",
        request=request,
    )
    return ok(msg="密码修改成功，请重新登录")


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(..., description="头像图片"),
    request: Request = None,
    _: None = Depends(check_farmer),
):
    """上传农户头像

    接收图片文件，存储到 upload/avatar/ 目录，自动更新当前农户的 avatar 字段。
    格式和大小限制从系统配置读取（与管理员端 upload.py 的图片限制一致）。
    """
    attachment = await save_uploaded_image(
        file, source="farmer_avatar", directory="avatar", use_storage_url=False,
    )
    relative_url = attachment["url"]
    logger.info("[农户头像] 农户 %s 上传头像: %s", request.state.user_id, relative_url)

    # 更新当前农户的 avatar 字段
    await update_farmer(request.state.user_id, {"avatar": relative_url})

    await active_log(
        description=f"农户上传头像: {file.filename}",
        log_type="farmer_profile",
        request=request,
    )
    return ok({"avatar": relative_url}, msg="头像更新成功")
