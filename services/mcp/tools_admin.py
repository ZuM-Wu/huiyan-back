"""
后台运维 MCP 核心工具（农户列表 + 任务监控闭环 + 系统概览）

- farmer_list         农户列表查询（audience=admin，需 farmer:list 权限）
- task_pending        待人工处理的失败任务查询（固定筛 failed 且未处理，
                      与 /api/admin/v1/task-monitor/status 的 unhandled_count 同口径）
- task_retry          手动重试失败任务（前置幂等校验后直调 task_monitor）
- task_mark_handled   标记日志为已处理
- task_mark_ignored   标记日志为已忽略
- system_overview     系统概览聚合计数（产区/地块/批次/农户/待办/预警，
                      供 AI 会话开场一次调用掌握全局）

任务监控四工具对齐 api/admin/task_monitor.py（仅 check_admin 无独立权限码，
故 permission_code=None 保持口径一致）；归因身份由 tool_utils.admin_identity
按 user_id 反查 Admin 表补齐（claims 不含用户名）。
"""
import logging
from datetime import datetime

from sqlalchemy import select, desc, func, or_
from fastmcp.exceptions import ToolError

from core.db.base import async_session_factory
from core.db.farmer import Farmer
from core.db.production_area import ProductionArea, Plot, PlantingBatch
from core.db.task_log import TaskLog
from core.db.weather import WeatherAlert
from services.mcp.tool_utils import _clamp_limit, _current_claims, admin_identity

logger = logging.getLogger(__name__)


async def farmer_list(keyword: str = "", status: int = -1, limit: int = 20) -> list[dict]:
    """查询农户列表（管理员运维用）。

    :param keyword: 关键词，模糊匹配用户名/昵称/手机号（空串不筛选）
    :param status: 状态筛选，0=禁用 1=启用，-1 表示不筛选
    :param limit: 返回条数上限（1-100，默认 20）
    :return: 农户列表，每项含 id/username/nickname/phone/email/company/
             status/create_time
    """
    limit = _clamp_limit(limit)

    async with async_session_factory() as db:
        q = select(Farmer)
        if keyword:
            kw = f"%{keyword}%"
            q = q.where(or_(
                Farmer.username.like(kw),
                Farmer.nickname.like(kw),
                Farmer.phone.like(kw),
            ))
        if status in (0, 1):
            q = q.where(Farmer.status == status)
        rows = (await db.execute(
            q.order_by(desc(Farmer.id)).limit(limit)
        )).scalars().all()

    return [
        {
            "id": f.id,
            "username": f.username,
            "nickname": f.nickname or "",
            "phone": f.phone or "",
            "email": f.email or "",
            "company": f.company or "",
            "status": f.status,
            "create_time": str(f.create_time) if f.create_time else "",
        }
        for f in rows
    ]


async def _load_task_log(log_id: int) -> TaskLog:
    """查询任务日志，不存在抛 ToolError

    服务层 mark_handled/mark_ignored 用 update 不报 404，故在工具层前置存在性校验。
    """
    async with async_session_factory() as db:
        log = (await db.execute(
            select(TaskLog).where(TaskLog.id == log_id)
        )).scalar_one_or_none()
    if log is None:
        raise ToolError(f"日志不存在: {log_id}")
    return log


async def task_pending(task_type: str = "", limit: int = 20) -> list[dict]:
    """查询待人工处理的失败任务（固定筛 failed 且未处理，按时间倒序）。

    :param task_type: 任务类型筛选，system/weather/notice/plugin（空串不筛选）
    :param limit: 返回条数上限（1-100，默认 20）
    :return: 待处理日志列表，每项含 id/task_name/task_desc/task_type/
             error_msg/duration_ms/retry_count/create_time
    """
    limit = _clamp_limit(limit)

    async with async_session_factory() as db:
        # 待处理口径与后台 /status 的 unhandled_count 一致: failed 且未处理
        q = select(TaskLog).where(
            TaskLog.status == "failed",
            TaskLog.handle_status == 0,
        )
        if task_type:
            q = q.where(TaskLog.task_type == task_type)
        rows = (await db.execute(
            q.order_by(desc(TaskLog.id)).limit(limit)
        )).scalars().all()

    return [
        {
            "id": log.id,
            "task_name": log.task_name,
            "task_desc": log.task_desc or log.task_name,
            "task_type": log.task_type,
            "error_msg": log.error_msg or "",
            "duration_ms": log.duration_ms,
            "retry_count": log.retry_count,
            "create_time": str(log.create_time) if log.create_time else "",
        }
        for log in rows
    ]


async def task_retry(log_id: int) -> dict:
    """手动重试指定的失败任务日志。

    :param log_id: 任务日志ID（由 task_pending 工具获取）
    :return: 重试结果，含 log_id/task_name/msg
    """
    claims = _current_claims()

    # 幂等前置校验（服务层 retry_task 无防护，重复重试会放大故障）:
    # 仅 "failed 且未处理" 的日志才允许重试
    log = await _load_task_log(log_id)
    if log.status != "failed" or log.handle_status != 0:
        raise ToolError("该日志不是待处理的失败任务，无需重试")

    admin_id, admin_name = await admin_identity(claims)

    from services.task.task_monitor import task_monitor
    result = await task_monitor.retry_task(log_id, admin_id, admin_name)
    if not result.get("success"):
        raise ToolError(result.get("msg") or "重试失败")
    return {"log_id": log_id, "task_name": log.task_name, "msg": result.get("msg", "")}


async def task_mark_handled(log_id: int, note: str = "") -> dict:
    """将任务日志标记为已处理。

    :param log_id: 任务日志ID（由 task_pending 工具获取）
    :param note: 处理备注（可选）
    :return: 标记结果，含 log_id/msg
    """
    claims = _current_claims()
    await _load_task_log(log_id)
    admin_id, admin_name = await admin_identity(claims)

    from services.task.task_monitor import task_monitor
    result = await task_monitor.mark_handled(log_id, admin_id, admin_name, note)
    return {"log_id": log_id, "msg": result.get("msg", "已标记为已处理")}


async def task_mark_ignored(log_id: int, note: str = "") -> dict:
    """将任务日志标记为已忽略。

    :param log_id: 任务日志ID（由 task_pending 工具获取）
    :param note: 忽略备注（可选）
    :return: 标记结果，含 log_id/msg
    """
    claims = _current_claims()
    await _load_task_log(log_id)
    admin_id, admin_name = await admin_identity(claims)

    from services.task.task_monitor import task_monitor
    result = await task_monitor.mark_ignored(log_id, admin_id, admin_name, note)
    return {"log_id": log_id, "msg": result.get("msg", "已标记为已忽略")}


async def _count(db, stmt) -> int:
    """执行单条 count 查询并返回整数（system_overview 内部复用）"""
    return int((await db.execute(stmt)).scalar_one() or 0)


async def system_overview() -> dict:
    """查询系统概览聚合计数（仅计数不含明细）。

    :return: 各核心实体计数：产区总数/启用数、地块数、种植批次数、
             农户总数/启用数、待处理失败任务数、当前生效气象预警数
    """
    async with async_session_factory() as db:
        area_total = await _count(db, select(func.count(ProductionArea.id)))
        area_enabled = await _count(db, select(func.count(ProductionArea.id))
                                    .where(ProductionArea.status == 1))
        plot_total = await _count(db, select(func.count(Plot.id)))
        batch_total = await _count(db, select(func.count(PlantingBatch.id)))
        farmer_total = await _count(db, select(func.count(Farmer.id)))
        farmer_enabled = await _count(db, select(func.count(Farmer.id))
                                      .where(Farmer.status == 1))
        # 待办口径与 task_pending 一致: failed 且未处理
        task_pending_count = await _count(db, select(func.count(TaskLog.id)).where(
            TaskLog.status == "failed", TaskLog.handle_status == 0,
        ))
        # 预警口径与 weather_alerts 一致: 结束时间未过即生效
        alert_active = await _count(db, select(func.count(WeatherAlert.id)).where(
            WeatherAlert.end_time >= datetime.now(),
        ))

    return {
        "area_total": area_total,
        "area_enabled": area_enabled,
        "plot_total": plot_total,
        "batch_total": batch_total,
        "farmer_total": farmer_total,
        "farmer_enabled": farmer_enabled,
        "task_pending": task_pending_count,
        "weather_alert_active": alert_active,
    }


# 后台运维工具声明列表（tools_core.py 聚合进 CORE_TOOLS）
ADMIN_TOOLS: list[dict] = [
    {
        "name": "farmer_list",
        "description": "查询农户列表。参数 keyword 模糊匹配用户名/昵称/手机号，"
                       "status 状态筛选（0=禁用 1=启用，-1 不筛选），limit 为条数上限（1-100）。"
                       "返回每个农户的 id/用户名/昵称/手机号/邮箱/公司/状态/注册时间。",
        "handler": farmer_list,
        "audience": "admin",
        "permission_code": "farmer:list",
    },
    {
        "name": "task_pending",
        "description": "查询待人工处理的失败任务（固定筛选执行失败且未处理的日志，按时间倒序）。"
                       "参数 task_type 任务类型筛选（system/weather/notice/plugin，空不筛），"
                       "limit 条数上限（1-100）。返回日志ID/任务名/描述/类型/错误信息/耗时/重试次数/时间，"
                       "配合 task_retry / task_mark_handled / task_mark_ignored 工具完成处置闭环。",
        "handler": task_pending,
        "audience": "admin",
        "permission_code": None,
    },
    {
        "name": "task_retry",
        "description": "手动重试失败任务。参数 log_id 为任务日志ID（由 core_task_pending 获取）；"
                       "仅支持重试状态为失败且未处理的日志，重试后原日志自动标记为已处理。",
        "handler": task_retry,
        "audience": "admin",
        "permission_code": None,
    },
    {
        "name": "task_mark_handled",
        "description": "将任务日志标记为已处理。参数 log_id 为任务日志ID，note 为处理备注（可选）。",
        "handler": task_mark_handled,
        "audience": "admin",
        "permission_code": None,
    },
    {
        "name": "task_mark_ignored",
        "description": "将任务日志标记为已忽略（确认无需处理的失败）。参数 log_id 为任务日志ID，"
                       "note 为忽略备注（可选）。",
        "handler": task_mark_ignored,
        "audience": "admin",
        "permission_code": None,
    },
    {
        "name": "system_overview",
        "description": "查询系统概览聚合计数，无参数。返回产区总数/启用数、地块数、"
                       "种植批次数、农户总数/启用数、待处理失败任务数、当前生效气象预警数，"
                       "适合会话开场一次调用掌握系统全局。",
        "handler": system_overview,
        "audience": "admin",
        "permission_code": None,
    },
]
