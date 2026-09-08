# -*- coding: utf-8 -*-
"""AgentScope 聊天附件上传路由。

新 AI 页面统一挂载在 ``/api/ai`` 下。这里仅负责把 AgentScope 的用户身份
映射到既有图片上传门面，格式/大小校验、UUID 命名和对象存储调度仍由
``services.image_upload.save_uploaded_image`` 统一处理。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from agentscope.app.deps import get_current_user_id

from core.log.active_log import active_log
from core.response import ok
from services.agentscope.image_inputs import build_model_image_url
from services.image_upload import save_uploaded_image


router = APIRouter(prefix="/upload", tags=["AgentScope 文件上传"])


def _upload_context(user_id: str) -> tuple[str, int | None]:
    """解析 AgentScope 用户标识并返回上传来源与管理员 ID。"""
    try:
        user_type, raw_id = user_id.split(":", 1)
        numeric_id = int(raw_id)
    except (AttributeError, ValueError):
        raise HTTPException(status_code=401, detail="AgentScope 用户身份无效") from None
    if user_type not in {"admin", "farmer"} or numeric_id < 1:
        raise HTTPException(status_code=401, detail="AgentScope 用户身份无效")
    # 管理员沿用通用上传的 ``admin`` 来源；农户沿用旧聊天上传的 ``ai_farmer``
    # 来源，保持 file_log 统计和对象存储插件的既有来源语义不变。
    source = "admin" if user_type == "admin" else "ai_farmer"
    admin_id = numeric_id if user_type == "admin" else None
    return source, admin_id


@router.post("/image")
async def upload_chat_image(
    request: Request,
    file: UploadFile = File(..., description="聊天图片"),
    user_id: str = Depends(get_current_user_id),
):
    """上传管理员或农户 AI 聊天图片，返回可持久化附件元数据。"""
    source, admin_id = _upload_context(user_id)
    attachment = await save_uploaded_image(file, source=source, admin_id=admin_id)
    attachment["model_url"] = build_model_image_url(attachment["url"])
    if admin_id is not None:
        await active_log(
            description=f"上传 AI 聊天图片: {file.filename}",
            log_type="upload",
            request=request,
        )
    return ok(attachment, msg="上传成功")


__all__ = ["router"]
