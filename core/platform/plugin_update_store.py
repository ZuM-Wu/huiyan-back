"""插件更新计划持久化实现。"""

from datetime import datetime

def plan_to_dict(row) -> dict:
    """将 ORM 更新计划转换为平台内部字典。"""
    if isinstance(row, dict):
        return dict(row)
    return {
        "operation_id": row.operation_id, "plugin_id": row.plugin_name,
        "plugin_name": row.plugin_name, "owner": row.plugin_name,
        "current_version": row.current_version, "target_version": row.target_version,
        "package_ref": row.package_ref or "",
        "package_digest": getattr(row, "package_digest", "") or "",
        "package_module": getattr(row, "package_module", "") or "",
        "status": row.status,
        "restart_required": bool(row.restart_required),
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "error_reason": row.error_reason or "", "identity": row.identity or "system",
        "confirmed_by": row.confirmed_by or "", "task_id": row.task_id,
    }


async def load_persisted_plan(platform, operation_id: str) -> dict | None:
    """读取计划；迁移尚未完成或测试无数据库时回退内存。"""
    try:
        from sqlalchemy import select
        from core.db.base import async_session_factory
        from core.db.plugin_update_plan import PluginUpdatePlanModel
        async with async_session_factory() as db:
            row = (await db.execute(select(PluginUpdatePlanModel).where(
                PluginUpdatePlanModel.operation_id == operation_id
            ))).scalar_one_or_none()
            plan = plan_to_dict(row) if row else None
            if plan:
                platform._persisted_operations.add(plan["operation_id"])
            return plan
    except Exception:
        return None


async def persist_plan(plan: dict) -> bool:
    """插入或更新计划；持久化失败必须阻断更新协议。"""
    try:
        from sqlalchemy import select
        from core.db.base import async_session_factory
        from core.db.plugin_update_plan import PluginUpdatePlanModel
        fields = {
            "plugin_name": plan["plugin_id"], "current_version": str(plan.get("current_version") or ""),
            "target_version": str(plan.get("target_version") or ""),
            "package_ref": str(plan.get("package_ref") or ""),
            "package_digest": str(plan.get("package_digest") or ""),
            "package_module": str(plan.get("package_module") or ""),
            "status": plan["status"], "restart_required": bool(plan.get("restart_required")),
            "created_at": datetime.fromisoformat(plan["created_at"]),
            "confirmed_at": datetime.fromisoformat(plan["confirmed_at"]) if plan.get("confirmed_at") else None,
            "applied_at": datetime.fromisoformat(plan["applied_at"]) if plan.get("applied_at") else None,
            "error_reason": str(plan.get("error_reason") or ""), "identity": str(plan.get("identity") or "system"),
            "confirmed_by": str(plan.get("confirmed_by") or ""), "task_id": plan.get("task_id"),
        }
        async with async_session_factory() as db:
            row = (await db.execute(select(PluginUpdatePlanModel).where(
                PluginUpdatePlanModel.operation_id == plan["operation_id"]
            ))).scalar_one_or_none()
            if row is None:
                db.add(PluginUpdatePlanModel(operation_id=plan["operation_id"], **fields))
            else:
                for key, value in fields.items():
                    setattr(row, key, value)
            await db.commit()
            return True
    except Exception as exc:
        raise RuntimeError("插件更新计划持久化失败") from exc


async def find_persisted_pending(platform, plugin_id: str, target_version: str = "", statuses=("awaiting_restart",)) -> dict | None:
    """查找同插件最新计划，用于 prepare/confirm 幂等。"""
    try:
        from sqlalchemy import select
        from core.db.base import async_session_factory
        from core.db.plugin_update_plan import PluginUpdatePlanModel
        async with async_session_factory() as db:
            query = select(PluginUpdatePlanModel).where(
                PluginUpdatePlanModel.plugin_name == plugin_id,
                PluginUpdatePlanModel.status.in_(statuses),
            ).order_by(PluginUpdatePlanModel.created_at.desc())
            row = (await db.execute(query)).scalars().first()
            plan = plan_to_dict(row) if row else None
            if plan:
                platform._persisted_operations.add(plan["operation_id"])
            if plan and (not target_version or plan["target_version"] == target_version):
                return plan
    except Exception:
        return None
    return None


async def update_plan_in_session(db, plan: dict) -> None:
    """在启动应用事务中更新计划。"""
    from sqlalchemy import update
    from core.db.plugin_update_plan import PluginUpdatePlanModel
    await db.execute(update(PluginUpdatePlanModel).where(
        PluginUpdatePlanModel.operation_id == plan["operation_id"]
    ).values(
        status=plan["status"], restart_required=plan.get("restart_required", False),
        applied_at=datetime.fromisoformat(plan["applied_at"]) if plan.get("applied_at") else None,
        error_reason=plan.get("error_reason", ""),
    ))
