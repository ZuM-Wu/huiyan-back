# -*- coding: utf-8 -*-
"""管理员文件上传、上传限制与统一上传策略 API。"""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.hook_events import emit_config_changed
from core.log.active_log import active_log
from core.oss_service import oss_service
from core.response import ok
from services.image_upload import save_uploaded_image
from services.upload_policy import (
    CONFIG_KEY,
    CORE_POLICY_DEFINITIONS,
    UploadPolicyError,
    build_policy_items,
    collect_policy_definitions,
    get_effective_policy,
    save_policies,
    stream_upload,
    validate_filename,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/upload", tags=["文件上传"])
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "upload"


class UploadPolicyValue(BaseModel):
    """统一上传策略保存项。"""

    id: str = Field(min_length=1, max_length=128)
    max_size_mb: int
    extensions: list[str]


class UploadPolicySave(BaseModel):
    """统一上传策略批量保存请求。"""

    policies: list[UploadPolicyValue]


def _raise_policy_error(exc: UploadPolicyError) -> None:
    status_code = 409 if exc.code == "policy_unavailable" else 400
    raise HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


async def _core_limits() -> dict:
    image = await get_effective_policy(CORE_POLICY_DEFINITIONS[0])
    file_policy = await get_effective_policy(CORE_POLICY_DEFINITIONS[1])
    return {
        "image_extensions": image["extensions"],
        "image_max_size_mb": image["max_size_mb"],
        "file_extensions": file_policy["extensions"],
        "file_max_size_mb": file_policy["max_size_mb"],
    }


@router.get("/settings")
async def get_upload_settings(
    request: Request,
    _: None = Depends(check_admin),
):
    """读取核心与已安装插件的统一上传策略。"""
    definitions, warnings = await collect_policy_definitions(
        request.app.state.plugin_manager,
    )
    policies, migrated = await build_policy_items(definitions)
    return ok({
        "schema_version": 1,
        "migrated": migrated,
        "policies": policies,
        "warnings": warnings,
    })


@router.put("/settings", dependencies=[Depends(require_admin_permission("upload:settings"))])
async def update_upload_settings(
    data: UploadPolicySave,
    request: Request,
    _: None = Depends(check_admin),
):
    """事务保存当前可见上传策略，并镜像旧配置键。"""
    definitions, warnings = await collect_policy_definitions(
        request.app.state.plugin_manager,
    )
    try:
        await save_policies(definitions, [item.model_dump() for item in data.policies])
    except UploadPolicyError as exc:
        _raise_policy_error(exc)
    await emit_config_changed(CONFIG_KEY, "updated")
    await active_log("保存统一上传设置", log_type="config", request=request)
    policies, migrated = await build_policy_items(definitions)
    return ok({
        "schema_version": 1,
        "migrated": migrated,
        "policies": policies,
        "warnings": warnings,
    }, msg="上传设置已保存")


@router.get("/limits")
async def get_upload_limits(_: None = Depends(check_admin)):
    """获取核心上传限制，保持现有接口字段兼容。"""
    return ok(await _core_limits())


@router.post("/image")
async def upload_image(
    file: UploadFile = File(..., description="图片文件"),
    request: Request = None,
    _: None = Depends(check_admin),
):
    """上传通用图片并返回永久 URL 元数据。"""
    attachment = await save_uploaded_image(file, source="admin")
    await active_log(
        description=f"上传图片: {file.filename}", log_type="upload", request=request,
    )
    return ok(attachment, msg="上传成功")


@router.post("/file")
async def upload_file(
    file: UploadFile = File(..., description="通用文件"),
    request: Request = None,
    _: None = Depends(check_admin),
):
    """按统一策略分块保存通用文件。"""
    policy = await get_effective_policy(CORE_POLICY_DEFINITIONS[1])
    try:
        extension = validate_filename(file.filename, policy)
    except UploadPolicyError as exc:
        _raise_policy_error(exc)

    save_name = f"{uuid.uuid4().hex}{extension}"
    save_path = UPLOAD_DIR / "common" / save_name
    try:
        file_size = await stream_upload(file, save_path, policy["max_size_mb"])
    except UploadPolicyError as exc:
        _raise_policy_error(exc)

    fallback_url = f"/upload/common/{save_name}"
    try:
        await oss_service.upload(
            save_path=str(save_path), save_name=save_name,
            original_name=file.filename, ext=extension, file_size=file_size,
            admin_id=None, source="admin",
        )
        url = oss_service.stable_url(f"common/{save_name}")
    except Exception as exc:
        logger.warning("[文件上传] 存储服务异常，回落直写: %s", exc)
        url = fallback_url
    await active_log(
        description=f"上传文件: {file.filename}", log_type="upload", request=request,
    )
    return ok({"url": url}, msg="上传成功")
