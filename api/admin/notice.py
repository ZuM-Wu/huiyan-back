"""
通知动作管理 API（管理员端）
提供通知动作的增删改查与批量配置能力
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from schemas.notice import (
    NoticeActionCreate, NoticeActionUpdate
)
from core.log.active_log import active_log
from core.response import ok
from core.notice_query_service import (
    search_notice_actions,
    check_action_key_exists,
    create_notice_action,
    get_notice_action,
    update_notice_action,
    delete_notice_action,
    batch_set_action_enabled,
    bulk_update_action_templates,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/notice/actions", tags=["通知动作管理"])


@router.get("/list", dependencies=[Depends(require_permission("notice:list"))])
async def list_actions(
    keywords: str = Query("", description="搜索关键词"),
    action_type: str = Query(None, description="类型筛选"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """通知动作列表 — 支持搜索、分页、类型筛选"""
    result = await search_notice_actions(
        page=page, limit=limit,
        keywords=keywords, action_type=action_type or "",
    )
    return ok(result)


@router.post("/", dependencies=[Depends(require_permission("notice:create"))])
async def create_action(
    data: NoticeActionCreate,
    request: Request,
    _: None = Depends(check_admin),
):
    """创建通知动作"""
    if await check_action_key_exists(data.action_key):
        raise HTTPException(status_code=400, detail="该动作标识已存在")

    action_dict = await create_notice_action(data.model_dump())

    logger.info(f"[通知动作] 创建动作：{data.action_key}")
    await active_log(f"创建通知动作：{data.action_key}", log_type="notice_action", rel_id=action_dict["id"], request=request)
    return ok(action_dict)


@router.get("/{action_key}", dependencies=[Depends(require_permission("notice:list"))])
async def get_action(
    action_key: str,
    _: None = Depends(check_admin),
):
    """获取单个动作详情"""
    action = await get_notice_action(action_key)
    if not action:
        raise HTTPException(status_code=404, detail="动作不存在")
    return ok(action)


@router.put("/{action_key}", dependencies=[Depends(require_permission("notice:update"))])
async def update_action(
    action_key: str,
    data: NoticeActionUpdate,
    request: Request,
    _: None = Depends(check_admin),
):
    """更新动作配置"""
    update_data = data.model_dump(exclude_unset=True)
    success = await update_notice_action(action_key, update_data)
    if not success:
        raise HTTPException(status_code=404, detail="动作不存在")

    action = await get_notice_action(action_key)
    logger.info(f"[通知动作] 更新动作：{action_key}, 更新内容：{update_data}")
    await active_log(f"更新通知动作：{action_key}", log_type="notice_action", rel_id=action["id"], request=request)
    return ok(action)


@router.delete("/{action_key}", dependencies=[Depends(require_permission("notice:delete"))])
async def delete_action(
    action_key: str,
    request: Request,
    _: None = Depends(check_admin),
):
    """删除动作（级联删除关联模板）"""
    success = await delete_notice_action(action_key)
    if not success:
        raise HTTPException(status_code=404, detail="动作不存在")

    logger.info(f"[通知动作] 删除动作：{action_key}")
    await active_log(f"删除通知动作：{action_key}", log_type="notice_action", request=request)
    return ok(msg="删除成功")


# ========== 批量操作 ==========

@router.post("/batch-enable", dependencies=[Depends(require_permission("notice:update"))])
async def batch_enable(
    request: Request,
    action_keys: list[str] = Query(..., description="动作标识列表"),
    enabled: bool = True,
    _: None = Depends(check_admin),
):
    """批量启用/禁用动作"""
    count = await batch_set_action_enabled(action_keys, enabled)

    logger.info(f"[通知动作] 批量设置启用状态：{count}个动作，enabled={enabled}")
    await active_log(f"批量{'启用' if enabled else '禁用'}通知动作：{count}个", log_type="notice_action", request=request)

    return ok({"updated": count})


@router.post("/bulk-update-templates", dependencies=[Depends(require_permission("notice:update"))])
async def bulk_update_templates(
    data: dict,
    request: Request,
    _: None = Depends(check_admin),
):
    """
    批量更新模板绑定

    {
      "sms_interface": "aliyun",
      "sms_template_id": 10,
      "action_keys": ["user_registered", "user_login"]
    }
    """
    sms_interface = data.get("sms_interface")
    sms_template_id = data.get("sms_template_id")
    email_interface = data.get("email_interface")
    email_template_id = data.get("email_template_id")
    action_keys = data.get("action_keys", [])

    if not action_keys:
        raise HTTPException(status_code=400, detail="缺少 action_keys")

    count = await bulk_update_action_templates(
        action_keys,
        sms_interface=sms_interface,
        sms_template_id=sms_template_id,
        email_interface=email_interface,
        email_template_id=email_template_id,
    )

    logger.info(f"[通知动作] 批量更新模板绑定：{count}个动作")
    await active_log(f"批量更新通知模板绑定：{count}个动作", log_type="notice_action", request=request)

    return ok({"updated": count})
