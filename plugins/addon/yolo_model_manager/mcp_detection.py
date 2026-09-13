"""系统 MCP 地块识别闭环；插件通过 get_mcp_tools 注册。"""

from sqlalchemy import select

from core.log.active_log import active_log
from core.db.base import async_session_factory
from core.hardware_device_service import get_hardware_device_info, list_hardware_devices_for_plot
from core.rate_limiter import check_rate_detail
from plugins.addon.yolo_model_manager.models import RecognitionRecord
from plugins.addon.yolo_model_manager.services.detection_service import DetectionNotReadyError, DetectionReferenceError, DetectionService
from plugins.addon.yolo_model_manager.services.recognition_service import RecognitionService
from services.mcp.errors import ERROR_MESSAGES, McpToolError
from services.mcp.tool_utils import current_claims, ensure_plot_access, positive_id


async def _device_context(device_id: int, plot_id: int) -> tuple[dict, dict]:
    info = await get_hardware_device_info(device_id)
    if not info or int(info.get("plot_id") or 0) != plot_id:
        raise McpToolError("binding_changed")
    async with async_session_factory() as db:
        try:
            context = await DetectionService.get_context(db, device_id)
        except DetectionReferenceError:
            raise McpToolError("device_unavailable") from None
    if not context.get("plot") or context["plot"]["id"] != plot_id:
        raise McpToolError("binding_changed")
    return info, context


def _not_ready_code(context: dict) -> str:
    if not context.get("recognition_capable"):
        return "no_recognition_devices"
    if (not context.get("model") or not context["model"].get("file_available", True)
            or "模型文件" in context.get("reason", "")):
        return "no_valid_model"
    if not context.get("queue_enabled"):
        return "queue_disabled"
    return "device_unavailable"


async def recognition_plot_preview(plot_id: int) -> dict:
    """只读预览，识别能力由既有实时图片解析规则判定。"""
    plot = await ensure_plot_access(plot_id)
    devices = await list_hardware_devices_for_plot(plot_id)
    candidates, unavailable = [], []
    for device in devices[:10]:
        try:
            info, context = await _device_context(device["device_id"], plot_id)
        except McpToolError as exc:
            unavailable.append({"device_id": device["device_id"], "code": exc.code})
            continue
        if not context.get("recognition_capable"):
            unavailable.append({"device_id": device["device_id"], "code": "no_recognition_devices"})
            continue
        online = bool(info.get("available") and info.get("provider_available"))
        model = context.get("model") or {}
        code = "" if online and context["ready"] else (_not_ready_code(context) if online else "device_unavailable")
        candidates.append({
            "device_id": device["device_id"], "device_name": str(device["name"])[:40],
            "online": online, "model_id": model.get("id"), "model_name": str(model.get("name") or "")[:40],
            "recognition_capable": True, "model_file_available": bool(model.get("file_available", context.get("ready"))),
            "ready": bool(online and context["ready"]),
            "code": code,
        })
    code = "" if any(item["ready"] for item in candidates) else "no_valid_model"
    if not devices:
        code = "no_devices"
    elif not candidates:
        code = "no_recognition_devices"
    elif not any(item["online"] for item in candidates):
        code = "device_unavailable"
    elif code and any(item["code"] == "queue_disabled" for item in candidates):
        code = "queue_disabled"
    return {"plot_id": plot_id, "plot_name": plot["plot_name"], "candidates": candidates,
            "unavailable": unavailable, "code": code, "reason": ERROR_MESSAGES.get(code, ""),
            "truncated": len(devices) > 10, "omitted_count": max(0, len(devices) - 10),
            "next_action": "调用yolo_model_manager_recognition_plot_start请求确认" if not code else "请检查设备、模型绑定和任务队列"}


async def recognition_plot_start(plot_id: int, device_id: int, model_id: int, confirmed: bool = False) -> dict:
    """确认与执行阶段都重验当前绑定；任务冻结图片和默认置信度。"""
    await ensure_plot_access(plot_id)
    positive_id(device_id, "device_id")
    positive_id(model_id, "model_id")
    if not isinstance(confirmed, bool):
        raise McpToolError("invalid_arguments", "confirmed 必须是布尔值")
    claims = current_claims()
    if claims.get("user_type") != "admin":
        raise McpToolError("permission_denied", "仅管理员可发起识别任务")
    info, context = await _device_context(device_id, plot_id)
    if not context.get("model"):
        raise McpToolError("no_valid_model")
    if context["model"].get("id") != model_id:
        raise McpToolError("binding_changed")
    if not info.get("available") or not info.get("provider_available"):
        raise McpToolError("device_unavailable")
    if not context.get("ready"):
        raise McpToolError(_not_ready_code(context))
    selection = {"plot_id": plot_id, "device_id": device_id, "model_id": model_id,
                 "device_name": str(context["device"]["name"])[:40], "model_name": str(context["model"]["name"])[:40]}
    if not confirmed:
        return {**selection, "requires_confirmation": True, "next_action": "请向用户确认后使用相同参数及confirmed=true再次调用"}
    admin_id = int(claims["user_id"])
    allowed, _ = check_rate_detail(f"yolo-detect:{admin_id}:{device_id}", limit=3, window=60)
    if not allowed:
        raise McpToolError("rate_limited", "识别请求过于频繁，请稍后重试")
    try:
        task = await DetectionService.submit(context, admin_id)
    except DetectionNotReadyError:
        raise McpToolError("device_unavailable") from None
    await active_log(f"MCP 管理员 {admin_id} 发起地块识别", "yolo_recognition_detect",
                     rel_id=task["task_id"], owner="yolo_model_manager")
    return {**selection, "task_id": task["task_id"], "status": task["status"],
            "next_action": "稍后按plot_id查询yolo_model_manager_recognition_latest_result"}


async def recognition_latest_result(plot_id: int) -> dict:
    """先验证现有地块归属，即使暂无记录也不能绕过农户范围。"""
    await ensure_plot_access(plot_id)
    async with async_session_factory() as db:
        row = (await db.execute(select(RecognitionRecord).where(RecognitionRecord.plot_id == plot_id)
            .order_by(RecognitionRecord.recognized_at.desc(), RecognitionRecord.id.desc()).limit(1))).scalar_one_or_none()
    if not row:
        return {"plot_id": plot_id, "found": False, "code": "no_data", "reason": "暂无识别记录"}
    summary = RecognitionService.to_summary(row)
    info = await get_hardware_device_info(summary["source_device_id"]) if summary["source_device_id"] else None
    return {"plot_id": plot_id, "found": True, "record_id": summary["id"], "recognized_at": summary["recognized_at"],
            "device_id": summary["source_device_id"], "device_name": str((info or {}).get("nickname") or (info or {}).get("device_name") or "")[:40],
            "model_id": summary["model_id"], "model_name": str(summary["model_name"])[:40],
            "detection_count": summary["detection_count"], "max_confidence": summary["max_confidence"],
            "labels": [str(label)[:32] for label in summary["labels"][:5]]}
