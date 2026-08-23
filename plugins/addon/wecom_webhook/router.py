# -*- coding: utf-8 -*-
"""
企业微信通知插件路由 — 管理员端 API

提供全局配置、管理员通知动作、测试发送和日志查询接口。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok

from plugins.addon.wecom_webhook.models import WecomWebhookLog
from plugins.addon.wecom_webhook.schemas import (
    SendMessageRequest,
    WecomActionBatchUpdate,
    WecomActionUpdate,
    WecomConfigUpdate,
    WecomTestRequest,
)
from plugins.addon.wecom_webhook.services.notice_service import wecom_notice_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/v1/wecom-webhook",
    tags=["企业微信通知"],
    dependencies=[Depends(check_admin)],
)


# ------------------------------------------------------------------
# 配置管理
# ------------------------------------------------------------------
@router.get("/config", dependencies=[Depends(require_permission("wecom_webhook:config:view"))])
async def get_config():
    """获取企业微信通知配置"""
    config = await wecom_notice_service.get_config()
    return ok(config)


@router.put("/config", dependencies=[Depends(require_permission("wecom_webhook:config:update"))])
async def update_config(data: WecomConfigUpdate, request: Request):
    """更新企业微信通知配置"""
    await wecom_notice_service.update_config(data.model_dump())
    await active_log("更新企业微信通知配置", "wecom_webhook_config")
    return ok(msg="配置已保存")


# ------------------------------------------------------------------
# 管理员通知动作配置
# ------------------------------------------------------------------
@router.get("/actions", dependencies=[Depends(require_permission("wecom_webhook:config:view"))])
async def list_actions():
    """获取插件自有管理员通知动作。"""
    return ok(await wecom_notice_service.list_actions())


@router.put("/actions/batch", dependencies=[Depends(require_permission("wecom_webhook:config:update"))])
async def batch_update_actions(data: WecomActionBatchUpdate, request: Request):
    """批量保存管理员通知动作配置。"""
    updated = 0
    missing = []
    for item in data.items:
        action_key = item.action_key
        try:
            saved = await wecom_notice_service.upsert_action(
                action_key, item.model_dump(exclude={"action_key"}),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if saved:
            updated += 1
        else:
            missing.append(action_key)
    await active_log("批量更新企业微信通知动作", "wecom_webhook_action")
    return ok({"updated": updated, "missing": missing}, msg=f"已更新 {updated} 个通知动作")


@router.put("/actions/{action_key}", dependencies=[Depends(require_permission("wecom_webhook:config:update"))])
async def update_action(action_key: str, data: WecomActionUpdate, request: Request):
    """更新单个管理员通知动作配置。"""
    try:
        saved = await wecom_notice_service.upsert_action(action_key, data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not saved:
        raise HTTPException(status_code=404, detail="管理员通知动作不存在")
    await active_log(f"更新企业微信通知动作: {action_key}", "wecom_webhook_action")
    return ok(msg="动作配置已保存")


# ------------------------------------------------------------------
# 消息发送
# ------------------------------------------------------------------
@router.post("/send", dependencies=[Depends(require_permission("wecom_webhook:message:send"))])
async def send_message(data: SendMessageRequest, request: Request):
    """发送企业微信消息（手动发送，不走通知规则）"""
    result = await wecom_notice_service.send_message(data)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("msg", "发送失败"))

    await active_log("发送企业微信消息", "wecom_webhook_send")
    return ok(result, msg="发送成功")


@router.post("/test", dependencies=[Depends(require_permission("wecom_webhook:message:send"))])
async def test_message(data: WecomTestRequest, request: Request):
    """发送测试消息，可临时覆盖全局 Webhook。"""
    result = await wecom_notice_service.send_message(
        data, action_key="manual_test", webhook_url=data.webhook_url,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("msg", "发送失败"))
    await active_log("测试企业微信通知", "wecom_webhook_test")
    return ok(result, msg="测试发送成功")


# ------------------------------------------------------------------
# 发送日志
# ------------------------------------------------------------------
@router.get("/logs", dependencies=[Depends(require_permission("wecom_webhook:log:view"))])
async def list_logs(
    action_key: Optional[str] = Query(None, description="按动作标识筛选"),
    status: Optional[int] = Query(None, ge=0, le=1, description="按发送状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
):
    """分页查询发送日志"""
    async with async_session_factory() as db:
        q = select(WecomWebhookLog).order_by(WecomWebhookLog.id.desc())
        if action_key:
            q = q.where(WecomWebhookLog.action_key == action_key)
        if status is not None:
            q = q.where(WecomWebhookLog.status == status)
        q = q.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(q)
        logs = result.scalars().all()

        # 统计总数
        count_q = select(func.count()).select_from(WecomWebhookLog)
        if action_key:
            count_q = count_q.where(WecomWebhookLog.action_key == action_key)
        if status is not None:
            count_q = count_q.where(WecomWebhookLog.status == status)
        total_result = await db.execute(count_q)
        total = total_result.scalar() or 0

    items = [_log_to_dict(log) for log in logs]
    return ok({"list": items, "total": total, "page": page, "page_size": page_size})


# ------------------------------------------------------------------
# 内部辅助
# ------------------------------------------------------------------
def _log_to_dict(log: WecomWebhookLog) -> dict:
    """日志 ORM 转字典"""
    return {
        "id": log.id,
        "action_key": log.action_key or "",
        "msgtype": log.msgtype or "template_card",
        "content": log.content or "",
        "status": log.status,
        "error_msg": log.error_msg or "",
        "msg_id": log.msg_id or "",
        "create_time": str(log.create_time) if log.create_time else "",
    }
