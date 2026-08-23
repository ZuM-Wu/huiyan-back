# -*- coding: utf-8 -*-
"""聊天、头像与后台共用的图片上传服务。"""
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from core.oss_service import oss_service
from services.upload_policy import (
    CORE_POLICY_DEFINITIONS,
    UploadPolicyError,
    get_effective_policy,
    stream_upload,
    validate_filename,
)

logger = logging.getLogger(__name__)
_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "upload"
_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}


async def get_image_upload_config() -> dict:
    """兼容旧调用方，返回带点扩展名集合和字节大小。"""
    policy = await get_effective_policy(CORE_POLICY_DEFINITIONS[0])
    return {
        "extensions": {f".{item}" for item in policy["extensions"]},
        "max_size": policy["max_size_mb"] * 1024 * 1024,
    }


async def save_uploaded_image(
    file: UploadFile,
    *,
    source: str = "admin",
    admin_id: int = None,
    directory: str = "common",
    use_storage_url: bool = True,
) -> dict:
    """按统一图片策略校验、分块落盘并调度对象存储。"""
    config = await get_image_upload_config()
    policy = {
        "extensions": [item.lstrip(".") for item in config["extensions"]],
        "max_size_mb": max(1, (config["max_size"] + 1024 * 1024 - 1) // (1024 * 1024)),
    }
    try:
        extension = validate_filename(file.filename, policy)
    except UploadPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_name = f"{uuid.uuid4().hex}{extension}"
    save_path = _UPLOAD_DIR / directory / save_name
    try:
        file_size = await stream_upload(file, save_path, policy["max_size_mb"])
    except UploadPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fallback_url = f"/upload/{directory}/{save_name}"
    try:
        result = await oss_service.upload(
            save_path=str(save_path), save_name=save_name,
            original_name=file.filename or "", ext=extension,
            file_size=file_size, admin_id=admin_id, source=source,
        )
        storage_url = result.get("data", {}).get("url", fallback_url)
        url = storage_url if use_storage_url else fallback_url
    except Exception as exc:
        logger.warning("[图片上传] 存储服务异常，回落直写: %s", exc)
        url = fallback_url
    logger.info("[图片上传] %s -> %s", file.filename, url)
    return {
        "type": "image", "url": url, "name": file.filename or "",
        "mime_type": _MIME_BY_EXT[extension], "size": file_size,
    }
