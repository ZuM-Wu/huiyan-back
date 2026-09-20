"""YOLO 模型测试任务的提交服务。"""

import hashlib
import json

from sqlalchemy import select

from core.config_manager import ConfigManager
from core.task_query_service import get_task_queue_item
from plugins.addon.yolo_model_manager.models import YoloModel
from plugins.addon.yolo_model_manager.services.detection_service import (
    DetectionNotReadyError,
    DetectionReferenceError,
    TASK_NAME,
    _map_task_status,
)
from plugins.addon.yolo_model_manager.services.model_service import YoloModelService
from services.task.queue_worker import submit_task


async def submit_model_test(db, model_id: int, image_url: str, admin_id: int) -> dict:
    """校验模型测试条件并将上传图片加入同一检测队列。"""
    model = (await db.execute(
        select(YoloModel).where(YoloModel.id == model_id)
    )).scalar_one_or_none()
    if not model:
        raise DetectionReferenceError("模型不存在")
    if not YoloModelService.get_download_path(model):
        raise DetectionNotReadyError("模型文件已丢失，请重新上传模型")
    queue_value = await ConfigManager().get("task_queue_enabled", db)
    if queue_value not in (None, "", "1", 1, True):
        raise DetectionNotReadyError("任务队列已关闭，请先在任务队列设置中启用")
    payload = {
        "admin_id": int(admin_id), "model_id": int(model_id),
        "confidence_threshold": float(model.default_confidence or 0.25),
        "image_url": image_url, "image_identifier": "model_test_upload",
        "source_type": "model_test",
    }
    frozen = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    task_id = await submit_task(
        TASK_NAME, payload, description=f"模型测试 {model.name}",
        idempotency_key="model-test:" + hashlib.sha256(frozen.encode("utf-8")).hexdigest(),
        correlation_id=f"yolo-model-test:{model_id}",
    )
    task = await get_task_queue_item(task_id)
    return {"task_id": task_id, "status": _map_task_status(task)}
