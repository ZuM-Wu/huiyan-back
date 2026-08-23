# -*- coding: utf-8 -*-
"""农户端上传限制查询 API。"""
from fastapi import APIRouter, Depends

from core.auth.middleware_chain import check_farmer
from core.response import ok
from services.upload_policy import CORE_POLICY_DEFINITIONS, get_effective_policy

router = APIRouter(prefix="/api/v1/upload", tags=["农户文件上传"])


@router.get("/limits")
async def get_upload_limits(_: None = Depends(check_farmer)):
    """返回农户端图片选择器需要的安全上传限制。"""
    policy = await get_effective_policy(CORE_POLICY_DEFINITIONS[0])
    return ok({
        "image_extensions": policy["extensions"],
        "image_max_size_mb": policy["max_size_mb"],
    })
