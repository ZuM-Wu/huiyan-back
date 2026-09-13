"""插件数据库体检管理员 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.auth.rbac import require_admin_permission
from core.config_service import get_config, set_config
from core.plugin_database_service import plugin_database_service
from core.platform.plugin import plugin_platform
from core.platform.plugin_update_store import persist_plan, load_persisted_plan
from core.response import ok
from services.task.plugin_database_worker import (
    ALLOWED_INTERVALS, active_plugin_database_scan_task, apply_plugin_database_schedule,
    enqueue_plugin_database_scan,
)

router = APIRouter(prefix="/api/admin/v1/plugin-database", tags=["插件数据库体检"])


class ScanConfig(BaseModel):
    enabled: bool = True
    interval_hours: int = Field(6, description="允许值: 1/6/12/24")


class BatchRequest(BaseModel):
    names: list[str] = Field(default_factory=list, min_length=1)


class RepairConfirmRequest(BaseModel):
    """修复确认请求体。"""

    operation_id: str = Field(..., min_length=1, description="待确认修复计划编号")


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.get("/status", dependencies=[Depends(require_admin_permission("plugin_database:list"))])
async def status(page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=200), module: str = "", state: str = Query("", alias="status"), keyword: str = ""):
    data = await plugin_database_service.list_status(page, limit, module, state, keyword)
    summary_rows = await plugin_database_service.list_status(1, 10000)
    counts = {}
    for row in summary_rows["list"]:
        key = row.get("status", "unverified")
        counts[key] = counts.get(key, 0) + 1
    data["summary"] = counts
    data["summary_stats"] = {
        "plugin_total": summary_rows["total"],
        "pending_upgrade": counts.get("version_mismatch", 0),
        "schema_anomaly": counts.get("schema_mismatch", 0),
        "file_anomaly": counts.get("missing_files", 0),
        "not_installed_or_unmanaged": counts.get("not_installed", 0) + counts.get("unverified", 0),
    }
    data["last_scan_at"] = plugin_database_service._last_scan_at
    data["last_scan_failure"] = plugin_database_service._last_scan_failure
    data["task_status"] = {"active_task_id": plugin_database_service._active_task_id}
    return ok(data)


@router.post("/scan", dependencies=[Depends(require_admin_permission("plugin_database:scan"))])
async def scan():
    if plugin_database_service._active_task_id:
        return ok({"task_id": plugin_database_service._active_task_id, "reused": True}, "已有活动扫描任务")
    try:
        queued_task_id = await active_plugin_database_scan_task()
    except Exception:
        # 队列表暂不可读时仍允许提交逻辑继续按幂等键去重，避免把数据库故障
        # 转换成误导性的“扫描进行中”业务错误。
        queued_task_id = None
    if queued_task_id:
        return ok({"task_id": queued_task_id, "reused": True}, "已有活动扫描任务")
    try:
        task_id = await enqueue_plugin_database_scan()
    except Exception as exc:
        raise _error(409, "scan_in_progress", str(exc)) from exc
    return ok({"task_id": str(task_id), "reused": False}, "扫描任务已提交")


@router.get("/config", dependencies=[Depends(require_admin_permission("plugin_database:config"))])
async def get_scan_config():
    enabled = await get_config("plugin_database_scan_enabled")
    interval = await get_config("plugin_database_scan_interval_hours")
    try:
        interval_value = int(interval or 6)
    except (TypeError, ValueError):
        interval_value = 6
    if interval_value not in ALLOWED_INTERVALS:
        interval_value = 6
    return ok({"enabled": str(enabled if enabled is not None else "1").lower() not in {"0", "false", "off"}, "interval_hours": interval_value})


@router.put("/config", dependencies=[Depends(require_admin_permission("plugin_database:config"))])
async def put_scan_config(payload: ScanConfig):
    if payload.interval_hours not in ALLOWED_INTERVALS:
        raise _error(422, "invalid_schedule", "扫描周期只能是 1、6、12 或 24 小时")
    await set_config("plugin_database_scan_enabled", "1" if payload.enabled else "0")
    await set_config("plugin_database_scan_interval_hours", str(payload.interval_hours))
    await apply_plugin_database_schedule()
    return ok(payload.model_dump())


@router.get("/{name}", dependencies=[Depends(require_admin_permission("plugin_database:list"))])
async def detail(name: str):
    state = await plugin_database_service.get_status(name)
    if not state:
        raise _error(404, "plugin_not_found", f"插件 '{name}' 不存在")
    return ok(state)


@router.post("/{name}/repair/prepare", dependencies=[Depends(require_admin_permission("plugin_database:repair"))])
async def prepare_repair(name: str):
    try:
        return ok(await plugin_database_service.prepare_repair(name))
    except LookupError as exc:
        raise _error(404, "plugin_not_found", str(exc)) from exc
    except NotImplementedError as exc:
        raise _error(409, "repair_not_supported", str(exc)) from exc
    except ValueError as exc:
        raise _error(409, "state_conflict", str(exc)) from exc
    except RuntimeError as exc:
        raise _error(409, "state_conflict", str(exc)) from exc


@router.post("/{name}/repair/confirm", dependencies=[Depends(require_admin_permission("plugin_database:repair"))])
async def confirm_repair(
    name: str,
    payload: RepairConfirmRequest = Body(...),
):
    """确认修复计划；operation_id 通过 JSON 请求体传入。"""
    operation_id = payload.operation_id
    plan = await load_persisted_plan(plugin_platform, operation_id)
    if not plan or plan.get("plugin_id") != name or plan.get("operation_type") != "repair":
        raise _error(404, "plan_expired", "修复计划不存在")
    if plan.get("status") != "prepared":
        raise _error(409, "state_conflict", "修复计划当前不可确认")
    try:
        created = datetime.fromisoformat(plan.get("created_at", ""))
        from core.time_utils import CHINA_TIMEZONE, china_now
        if created.tzinfo is not None:
            created = created.astimezone(CHINA_TIMEZONE).replace(tzinfo=None)
        if china_now() - created > plugin_platform.PLAN_TTL:
            raise ValueError("修复计划已过期")
    except (TypeError, ValueError) as exc:
        plan.update(status="failed", error_reason=str(exc))
        await persist_plan(plan)
        raise _error(409, "plan_expired", str(exc)) from exc
    from core.time_utils import china_now
    plan.update(status="awaiting_restart", restart_required=True, confirmed_at=china_now().isoformat(), confirmed_by="admin")
    await persist_plan(plan)
    return ok(plan, "修复计划已确认，需通过本地控制中心执行")


@router.post("/prepare-batch", dependencies=[Depends(require_admin_permission("plugin:upgrade"))])
async def prepare_batch(payload: BatchRequest):
    plans, skipped = [], []
    for name in payload.names:
        state = await plugin_database_service.get_status(name)
        if not state:
            skipped.append({"name": name, "reason": "plugin_not_found"})
            continue
        if state.get("status") != "version_mismatch":
            skipped.append({"name": name, "reason": state.get("status", "unverified")})
            continue
        try:
            plans.append(await plugin_platform.prepare_update(name, "", identity="admin"))
        except Exception as exc:
            skipped.append({"name": name, "reason": str(exc)})
    return ok({"plans": plans, "skipped": skipped})
