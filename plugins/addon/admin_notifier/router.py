# -*- coding: utf-8 -*-
"""
邮件通知管理员插件路由 — 管理员端 API

仅提供告警配置管理、管理员列表、通知插件列表、测试发送等接口。
任务日志查询、手动重试、标记处理、状态概览等已移至核心
api/admin/task_monitor.py，不在本插件职责范围内。
"""
import logging
from datetime import datetime
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok

from plugins.addon.admin_notifier.models import TaskAlertConfig
from plugins.addon.admin_notifier.schemas import (
    AlertConfigUpdate,
    BatchConfigUpdate,
    NotifyTestRequest,
)
from plugins.addon.admin_notifier.services.alert_service import alert_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/v1/admin-notifier",
    tags=["邮件通知管理员"],
    dependencies=[Depends(check_admin)],
)

# 已知任务清单（用于自动创建告警配置，管理员可自行开关通知）
# 包含系统任务、天气任务和业务通知动作
_KNOWN_TASKS = [
    # 系统任务
    {"task_name": "clean_repeat_cache",    "task_title": "清理防重复缓存",   "task_type": "system"},
    {"task_name": "clean_old_logs",         "task_title": "清理过期系统日志", "task_type": "system"},
    {"task_name": "clean_task_logs",        "task_title": "清理过期任务日志", "task_type": "system"},
    {"task_name": "sweep_expired_cache",    "task_title": "清扫过期缓存",     "task_type": "system"},
    # 天气任务
    {"task_name": "weather_pull",           "task_title": "天气全量拉取",     "task_type": "weather"},
    {"task_name": "weather_daily_finalize", "task_title": "天气日终定格",     "task_type": "weather"},
    {"task_name": "weather_clean",          "task_title": "天气历史数据清理", "task_type": "weather"},
    {"task_name": "weather_alert_notify",   "task_title": "气象预警通知补推", "task_type": "weather"},
    # 业务通知动作（与发送设置页的 12 个通知动作一致）
    {"task_name": "user_registered",        "task_title": "用户注册",         "task_type": "notice"},
    {"task_name": "password_reset",         "task_title": "密码重置",         "task_type": "notice"},
    {"task_name": "cert_approved",          "task_title": "实名认证通过",     "task_type": "notice"},
    {"task_name": "cert_rejected",          "task_title": "实名认证驳回",     "task_type": "notice"},
    {"task_name": "order_created",           "task_title": "订单创建",         "task_type": "notice"},
    {"task_name": "order_completed",        "task_title": "订单完成",         "task_type": "notice"},
    {"task_name": "verify_code_login",      "task_title": "验证码登录",       "task_type": "notice"},
    {"task_name": "verify_code_register",   "task_title": "注册验证码",       "task_type": "notice"},
    {"task_name": "area_farmer_bindied",    "task_title": "产区绑定农户",     "task_type": "notice"},
    {"task_name": "plot_created",           "task_title": "新增地块通知",     "task_type": "notice"},
    {"task_name": "batch_status_change",    "task_title": "批次状态变更",     "task_type": "notice"},
    {"task_name": "system_maintenance",     "task_title": "系统维护通知",     "task_type": "notice"},
    # 推送插件调度
    {"task_name": "push_scheduler",         "task_title": "推送任务调度",     "task_type": "push_task"},
]


async def _ensure_default_configs(db):
    """确保所有已知任务都有告警配置记录（首次加载时自动补齐）"""
    existing_names = {
        r[0] for r in (await db.execute(
            select(TaskAlertConfig.task_name)
        )).all()
    }
    for task in _KNOWN_TASKS:
        if task["task_name"] not in existing_names:
            db.add(TaskAlertConfig(
                task_name=task["task_name"],
                task_title=task["task_title"],
                task_type=task["task_type"],
                notify_enabled=0,
                notify_channel="email",
                notify_interface="",
                admin_ids="",
            ))
    await db.flush()


# ------------------------------------------------------------------
# 告警配置
# ------------------------------------------------------------------
@router.get("/configs", dependencies=[Depends(require_permission("admin_notifier:config:view"))])
async def list_configs():
    """告警配置列表（首次加载时自动补齐已知任务的配置记录）"""
    async with async_session_factory() as db:
        await _ensure_default_configs(db)
        await db.commit()

        result = await db.execute(
            select(TaskAlertConfig).order_by(TaskAlertConfig.task_type, TaskAlertConfig.id)
        )
        configs = result.scalars().all()

        items = []
        for c in configs:
            items.append({
                "id": c.id,
                "task_name": c.task_name,
                "task_title": c.task_title or c.task_name,
                "task_type": c.task_type,
                "notify_enabled": c.notify_enabled,
                "notify_interface": c.notify_interface or "",
                "admin_ids": c.admin_ids or "",
                "create_time": str(c.create_time) if c.create_time else "",
                "update_time": str(c.update_time) if c.update_time else "",
            })

    return ok({"list": items, "total": len(items)})


@router.put("/configs/{task_name}", dependencies=[Depends(require_permission("admin_notifier:config:update"))])
async def update_config(task_name: str, data: AlertConfigUpdate, request: Request):
    """更新单个任务的告警配置"""
    # URL 中的 task_name 可能被编码，需解码
    task_name = unquote(task_name)

    async with async_session_factory() as db:
        result = await db.execute(
            select(TaskAlertConfig).where(TaskAlertConfig.task_name == task_name)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="任务配置不存在")

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(config, key, value)
        config.update_time = datetime.now()
        await db.commit()

        await active_log(
            f"更新告警配置: {task_name}",
            "admin_notifier_config",
            db=db,
        )

    return ok(msg="配置已保存")


@router.put("/configs", dependencies=[Depends(require_permission("admin_notifier:config:update"))])
async def batch_update_configs(data: BatchConfigUpdate, request: Request):
    """批量更新告警配置"""
    async with async_session_factory() as db:
        for item in data.items:
            result = await db.execute(
                select(TaskAlertConfig).where(TaskAlertConfig.task_name == item.task_name)
            )
            config = result.scalar_one_or_none()
            if config:
                config.notify_enabled = item.notify_enabled
                config.notify_interface = item.notify_interface
                config.admin_ids = item.admin_ids
                if item.task_title:
                    config.task_title = item.task_title
                config.update_time = datetime.now()
            else:
                # 不存在则创建
                new_config = TaskAlertConfig(
                    task_name=item.task_name,
                    task_title=item.task_title or item.task_name,
                    task_type=item.task_type,
                    notify_enabled=item.notify_enabled,
                    notify_interface=item.notify_interface,
                    admin_ids=item.admin_ids,
                )
                db.add(new_config)
        await db.commit()

        await active_log(
            "批量更新告警配置",
            "admin_notifier_config",
            db=db,
        )

    return ok(msg="配置已保存")


# ------------------------------------------------------------------
# 管理员列表（有邮箱的）
# ------------------------------------------------------------------
@router.get("/admins", dependencies=[Depends(require_permission("admin_notifier:config:view"))])
async def list_admins():
    """获取有邮箱的管理员列表（供配置页面选择接收人）"""
    from core.admin_service import list_admins_with_email
    items = await list_admins_with_email()
    return ok({"list": items, "total": len(items)})


# ------------------------------------------------------------------
# 已安装的通知插件列表
# ------------------------------------------------------------------
@router.get("/plugins", dependencies=[Depends(require_permission("admin_notifier:config:view"))])
async def list_notify_plugins():
    """获取已安装的邮件插件列表（供配置页面选择通知接口）"""
    from core.plugin_query_service import list_plugins_by_module
    items = await list_plugins_by_module("mail")
    return ok({"list": items, "total": len(items)})


# ------------------------------------------------------------------
# 发送测试通知
# ------------------------------------------------------------------
@router.post("/test", dependencies=[Depends(require_permission("admin_notifier:test:send"))])
async def test_notify(data: NotifyTestRequest, request: Request):
    """发送测试通知"""
    if not data.admin_ids:
        raise HTTPException(status_code=400, detail="请选择接收测试通知的管理员")

    # 复用 alert_service 的发送逻辑
    try:
        await alert_service._send_notify(
            task_name="test_notify",
            task_desc="测试通知",
            error_msg="这是一条测试通知，用于验证邮件通知功能是否正常工作。",
            interface=data.notify_interface,
            admin_ids=data.admin_ids,
        )
        await active_log(
            "发送测试通知",
            "admin_notifier_test",
        )
    except Exception as e:
        logger.error(f"[admin_notifier] 测试通知发送失败: {e}")
        raise HTTPException(status_code=500, detail=f"发送失败: {e}")

    return ok(msg="测试通知已发送")
