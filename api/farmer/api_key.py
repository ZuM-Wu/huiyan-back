"""个人 API Key 管理 API（农户端）

用于 MCP 服务的 Bearer Key 鉴权。密钥为个人资产：
- 仅能为本人（request.state.user_id）创建/查看/吊销
- 明文落库（key_plain），列表可随时查看与复制；鉴权仍走 sha256 哈希查找
- 吊销后直调 invalidate_key_cache，单进程内即时生效
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth.middleware_chain import check_farmer
from core.api_key_service import (
    list_api_keys, create_api_key, revoke_api_key,
)
from schemas.api_key import ApiKeyCreate, ApiKeyRevoke
from core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/api-key", tags=["农户API密钥"])

USER_TYPE = "farmer"


@router.post("/create")
async def create_key(request: Request, data: ApiKeyCreate, _: None = Depends(check_farmer)):
    """创建个人 API Key（明文落库，列表可随时查看与复制）"""
    user_id = request.state.user_id
    try:
        result = await create_api_key(USER_TYPE, user_id, data.name)
        return ok(result, msg="创建成功")
    except Exception as e:
        logger.error(f"[API Error] farmer create_api_key: {e}", exc_info=True)
        raise HTTPException(500, "创建失败")


@router.get("/list")
async def list_keys(request: Request, _: None = Depends(check_farmer)):
    """本人 Key 列表（含明文 key 供可视化查看，不回哈希）"""
    user_id = request.state.user_id
    data = await list_api_keys(USER_TYPE, user_id, page=1, limit=1000)
    return ok({"total": data["total"], "list": data["list"]})


@router.post("/revoke")
async def revoke_key(request: Request, data: ApiKeyRevoke, _: None = Depends(check_farmer)):
    """吊销本人 Key（status=2）并即时失效鉴权缓存"""
    user_id = request.state.user_id
    try:
        success = await revoke_api_key(data.id, USER_TYPE, user_id)
        if not success:
            raise HTTPException(404, "密钥不存在")
        return ok(msg="吊销成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] farmer revoke_api_key: {e}", exc_info=True)
        raise HTTPException(500, "吊销失败")
