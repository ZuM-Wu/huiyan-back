# -*- coding: utf-8 -*-
"""推送中心 2.0 管理员 API。"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func, select

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok
from plugins.addon.push.models import PushCenterDeliveryLog, PushCenterTask
from plugins.addon.push.schemas import (
    PushPreviewSend,
    PushTargetPreview,
    PushTaskCreate,
    PushTaskUpdate,
    StatusChange,
    TestSend,
)
from plugins.addon.push.target_resolver import count_targets, resolve_targets

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/v1/push",
    tags=["推送中心"],
    dependencies=[Depends(check_admin)],
)


def _task_data(task: PushCenterTask) -> dict:
    """将 ORM 任务转换为稳定的前端 DTO。"""
    schedule = task.schedule_rule or {}
    channels = task.channels or {}
    target = task.target_rule or {}
    return {
        "id": task.id,
        "title": task.title,
        "keywords": task.keywords or "",
        "content": task.content or "",
        "subject": task.subject or "",
        "channels": channels,
        "target_rule": target,
        "schedule_rule": schedule,
        "target_count": task.target_count or 0,
        "send_num": task.send_num or 0,
        "success_num": task.success_num or 0,
        "fail_num": task.fail_num or 0,
        "last_exec_time": str(task.last_exec_time) if task.last_exec_time else "",
        "status": task.status,
        "admin_id": task.admin_id,
        "create_time": str(task.create_time) if task.create_time else "",
        "update_time": str(task.update_time) if task.update_time else "",
        # 兼容旧页面/外部调用
        "type": 1 if channels.get("inbox") and not (channels.get("sms") or channels.get("email")) else 2,
        "push_target": target,
        "send_cycle": schedule.get("cycle", "onetime"),
        "time_hour_min": schedule.get("time_hour_min", "09:00"),
    }


def _legacy_payload(payload: dict) -> dict:
    """将旧表单字段转换为推送中心请求字段。"""
    channels = payload.get("channels") or {
        "inbox": payload.get("type", 1) == 1,
        "sms": payload.get("type", 1) == 2 and bool(payload.get("sms_template_id")),
        "email": payload.get("type", 1) == 2 and bool(payload.get("email_template_id")),
        "sms_template_id": payload.get("sms_template_id", 0),
        "email_template_id": payload.get("email_template_id", 0),
    }
    target = payload.get("target_rule") or payload.get("push_target") or {"mode": "all"}
    schedule = payload.get("schedule_rule") or {
        "cycle": payload.get("send_cycle", "onetime"),
        "week_day": payload.get("week_day") or 1,
        "month_day": payload.get("month_day") or 1,
        "time_hour_min": payload.get("time_hour_min", "09:00"),
        "start_time": payload.get("push_start_time") or None,
        "end_time": payload.get("push_end_time") or None,
    }
    return {
        "title": payload.get("title", ""),
        "keywords": payload.get("keywords", ""),
        "content": payload.get("content", ""),
        "subject": payload.get("subject", ""),
        "channels": channels,
        "target_rule": target,
        "schedule_rule": schedule,
    }


async def _get_task(db, task_id: int) -> PushCenterTask:
    result = await db.execute(select(PushCenterTask).where(PushCenterTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="推送任务不存在")
    return task


async def _create_task(payload: dict, request: Request) -> int:
    data = PushTaskCreate.model_validate(payload)
    target_rule = data.target_rule.model_dump(mode="json")
    schedule_rule = data.schedule_rule.model_dump(mode="json")
    channels = data.channels.model_dump()
    target_count = await count_targets(target_rule)
    async with async_session_factory() as db:
        task = PushCenterTask(
            title=data.title,
            keywords=data.keywords,
            content=data.content,
            subject=data.subject,
            channels=channels,
            target_rule=target_rule,
            schedule_rule=schedule_rule,
            target_count=target_count,
            status="Wait",
            admin_id=getattr(request.state, "admin_id", 0),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id
        await active_log(f"创建推送中心任务: {data.title}", "push_center_create", rel_id=task_id, db=db)
        if data.schedule_rule.cycle == "onetime":
            from plugins.addon.push.scheduler import schedule_onetime_task
            await schedule_onetime_task(task)
    return task_id


@router.get("/tasks", dependencies=[Depends(require_permission("push:list"))])
async def list_tasks(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    """推送中心任务列表。"""
    async with async_session_factory() as db:
        query = select(PushCenterTask)
        count_query = select(func.count(PushCenterTask.id))
        if status:
            query = query.where(PushCenterTask.status == status)
            count_query = count_query.where(PushCenterTask.status == status)
        if keyword:
            from sqlalchemy import or_
            condition = or_(PushCenterTask.title.like(f"%{keyword}%"), PushCenterTask.keywords.like(f"%{keyword}%"))
            query = query.where(condition)
            count_query = count_query.where(condition)
        total = (await db.execute(count_query)).scalar() or 0
        result = await db.execute(
            query.order_by(desc(PushCenterTask.id)).offset((page - 1) * limit).limit(limit)
        )
        items = [_task_data(task) for task in result.scalars().all()]
    return ok({"list": items, "total": total, "page": page, "limit": limit})


@router.post("/tasks", dependencies=[Depends(require_permission("push:create"))])
async def create_task(data: PushTaskCreate, request: Request):
    task_id = await _create_task(data.model_dump(mode="json"), request)
    return ok({"id": task_id}, msg="创建成功")


@router.get("/tasks/{task_id}", dependencies=[Depends(require_permission("push:list"))])
async def get_task(task_id: int):
    async with async_session_factory() as db:
        task = await _get_task(db, task_id)
        return ok(_task_data(task))


@router.patch("/tasks/{task_id}", dependencies=[Depends(require_permission("push:update"))])
async def update_task(task_id: int, data: PushTaskUpdate):
    async with async_session_factory() as db:
        task = await _get_task(db, task_id)
        if task.status != "Wait":
            raise HTTPException(status_code=400, detail="仅等待状态任务可编辑")
        values = data.model_dump(exclude_unset=True, mode="json")
        if "target_rule" in values:
            values["target_count"] = await count_targets(values["target_rule"])
        for key, value in values.items():
            if value is not None:
                setattr(task, key, value)
        await db.commit()
        await active_log(f"更新推送中心任务: {task_id}", "push_center_update", rel_id=task_id, db=db)
    return ok(msg="更新成功")


@router.delete("/tasks/{task_id}", dependencies=[Depends(require_permission("push:delete"))])
async def delete_task(task_id: int):
    async with async_session_factory() as db:
        task = await _get_task(db, task_id)
        if task.status == "Exec":
            raise HTTPException(status_code=400, detail="执行中的任务不可删除")
        await db.delete(task)
        await db.commit()
    return ok(msg="删除成功")


async def _change_status(task_id: int, status: str):
    allowed = {"Wait": {"Suspended"}, "Suspended": {"Wait"}}
    async with async_session_factory() as db:
        task = await _get_task(db, task_id)
        if status not in allowed.get(task.status, set()):
            raise HTTPException(status_code=400, detail=f"状态 {task.status} 不允许切换为 {status}")
        task.status = status
        await db.commit()
        if status == "Wait":
            from plugins.addon.push.scheduler import schedule_onetime_task
            await schedule_onetime_task(task)
    return ok(msg="状态已更新")


@router.post("/tasks/{task_id}/pause", dependencies=[Depends(require_permission("push:update"))])
async def pause_task(task_id: int):
    return await _change_status(task_id, "Suspended")


@router.post("/tasks/{task_id}/resume", dependencies=[Depends(require_permission("push:update"))])
async def resume_task(task_id: int):
    return await _change_status(task_id, "Wait")


@router.post("/tasks/{task_id}/resend", dependencies=[Depends(require_permission("push:create"))])
async def resend_task(task_id: int, request: Request):
    async with async_session_factory() as db:
        source = await _get_task(db, task_id)
        payload = {
            "title": f"{source.title}（重发）",
            "keywords": source.keywords,
            "content": source.content,
            "subject": source.subject,
            "channels": source.channels,
            "target_rule": source.target_rule,
            "schedule_rule": {**(source.schedule_rule or {}), "cycle": "onetime"},
        }
    new_id = await _create_task(payload, request)
    return ok({"id": new_id}, msg="重发任务已创建")


@router.post("/tasks/target-preview", dependencies=[Depends(require_permission("push:list"))])
async def preview_targets(data: PushTargetPreview):
    rule = data.target_rule.model_dump()
    farmers, total = await resolve_targets(rule)
    start = (data.page - 1) * data.limit
    return ok({"total": total, "list": farmers[start:start + data.limit], "page": data.page, "limit": data.limit})


@router.get("/tasks/{task_id}/deliveries", dependencies=[Depends(require_permission("push:list"))])
async def list_deliveries(task_id: int, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), channel: str = ""):
    async with async_session_factory() as db:
        await _get_task(db, task_id)
        query = select(PushCenterDeliveryLog).where(PushCenterDeliveryLog.task_id == task_id)
        count_query = select(func.count(PushCenterDeliveryLog.id)).where(PushCenterDeliveryLog.task_id == task_id)
        if channel:
            query = query.where(PushCenterDeliveryLog.channel == channel)
            count_query = count_query.where(PushCenterDeliveryLog.channel == channel)
        total = (await db.execute(count_query)).scalar() or 0
        result = await db.execute(query.order_by(desc(PushCenterDeliveryLog.id)).offset((page - 1) * limit).limit(limit))
        items = [{
            "id": row.id, "farmer_id": row.farmer_id, "username": row.username,
            "channel": row.channel, "status": row.status, "reason": row.reason or "",
            "notification_log_id": row.notification_log_id,
            "create_time": str(row.create_time) if row.create_time else "",
        } for row in result.scalars().all()]
    return ok({"list": items, "total": total, "page": page, "limit": limit})


@router.post("/tasks/{task_id}/preview-send", dependencies=[Depends(require_permission("push:send"))])
async def preview_send(task_id: int, data: PushPreviewSend):
    if not data.email.strip() and not data.phone.strip():
        raise HTTPException(status_code=400, detail="请填写测试邮箱或手机号")
    from plugins.addon.push.executor import send_preview
    result = await send_preview(task_id, data.email.strip(), data.phone.strip())
    return ok(result, msg="预览发送完成")


# 旧路径兼容别名，避免现有页面与外部调用立即失效。
@router.get("/list", dependencies=[Depends(require_permission("push:list"))])
async def legacy_list(keyword: str = "", status: str = "", page: int = 1, limit: int = 10):
    return await list_tasks(keyword, status, page, limit)


@router.post("/", dependencies=[Depends(require_permission("push:create"))])
async def legacy_create(payload: dict, request: Request):
    task_id = await _create_task(_legacy_payload(payload), request)
    return ok({"id": task_id}, msg="创建成功")


@router.get("/{task_id}", dependencies=[Depends(require_permission("push:list"))])
async def legacy_get(task_id: int):
    return await get_task(task_id)


@router.put("/{task_id}", dependencies=[Depends(require_permission("push:update"))])
async def legacy_update(task_id: int, payload: dict):
    return await update_task(task_id, PushTaskUpdate.model_validate(_legacy_payload(payload)))


@router.delete("/{task_id}", dependencies=[Depends(require_permission("push:delete"))])
async def legacy_delete(task_id: int):
    return await delete_task(task_id)


@router.put("/{task_id}/status", dependencies=[Depends(require_permission("push:update"))])
async def legacy_status(task_id: int, data: StatusChange):
    return await _change_status(task_id, data.status)


@router.post("/{task_id}/resend", dependencies=[Depends(require_permission("push:create"))])
async def legacy_resend(task_id: int, request: Request):
    return await resend_task(task_id, request)


@router.post("/target-preview", dependencies=[Depends(require_permission("push:list"))])
async def target_preview(data):
    """兼容旧请求体 push_target，并修复旧 db 参数导致的 500。"""
    rule = getattr(data, "target_rule", None) or getattr(data, "push_target", None)
    if rule is None and isinstance(data, dict):
        rule = data.get("target_rule") or data.get("push_target")
    if hasattr(rule, "model_dump"):
        rule = rule.model_dump()
    farmers, total = await resolve_targets(rule or {"mode": "all"})
    return ok({"total": total, "list": farmers})


@router.get("/{task_id}/logs", dependencies=[Depends(require_permission("push:list"))])
async def legacy_logs(task_id: int, page: int = 1, limit: int = 20):
    return await list_deliveries(task_id, page, limit)


@router.post("/preview", dependencies=[Depends(require_permission("push:send"))])
async def legacy_preview(data: TestSend):
    if data.test_email or data.test_phone:
        from plugins.addon.push.executor import send_direct_preview
        return ok(await send_direct_preview(data), msg="预览发送完成")
    raise HTTPException(status_code=400, detail="请提供测试邮箱或手机号")
