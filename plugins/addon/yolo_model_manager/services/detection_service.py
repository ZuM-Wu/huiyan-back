# -*- coding: utf-8 -*-
"""生长记录仪快捷检测的上下文解析、任务投递和 YOLO 执行。"""

import asyncio
import hashlib
import json
import logging
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import select

from core.config import BASE_DIR
from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from core.hardware_device_service import get_hardware_device_info
from core.hardware_realtime_service import read_latest_device_snapshot
from core.task_query_service import get_task_queue_item, retry_queue_task
from plugins.addon.yolo_model_manager.models import YoloModel, YoloModelPlotBinding
from plugins.addon.yolo_model_manager.services.model_service import YoloModelService
from plugins.addon.yolo_model_manager.services.label_extractor import _resolve_model_label
from plugins.addon.yolo_model_manager.services.image_utils import (
    ImagePreparationError,
    decode_image as _decode_image_impl,
    read_image_bytes as _read_image_bytes_impl,
)
from plugins.addon.yolo_model_manager.services.image_utils import (
    assert_public_http_url as _assert_public_http_url_impl,
)
from plugins.addon.yolo_model_manager.services.recognition_service import RecognitionService
from services.task.queue_worker import submit_task

logger = logging.getLogger(__name__)

TASK_NAME = "yolo_recognition_detect"
PREFERRED_IMAGE_IDENTIFIERS = ("imgurl", "imageurl", "photourl", "pictureurl")
ANNOTATED_IMAGE_DIR = BASE_DIR / "upload" / "yolo_model_manager" / "recognitions"
ANNOTATED_IMAGE_URL_PREFIX = "/upload/yolo_model_manager/recognitions"


class DetectionReferenceError(LookupError):
    """检测引用的设备或任务不存在。"""


class DetectionNotReadyError(ImagePreparationError):
    """设备当前缺少快捷检测所需的业务条件。"""


async def _assert_public_http_url(url: str) -> None:
    try:
        await _assert_public_http_url_impl(url)
    except ImagePreparationError as exc:
        raise DetectionNotReadyError(str(exc)) from exc


async def _read_image_bytes(image_url: str) -> bytes:
    try:
        return await _read_image_bytes_impl(image_url)
    except ImagePreparationError as exc:
        raise DetectionNotReadyError(str(exc)) from exc


def _decode_image(content: bytes):
    try:
        return _decode_image_impl(content)
    except ImagePreparationError as exc:
        raise DetectionNotReadyError(str(exc)) from exc


def _valid_image_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.strip()
    if normalized.startswith("/"):
        return normalized.startswith("/upload/") and "\\" not in normalized
    parsed = urlsplit(normalized)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and not parsed.username
        and not parsed.password
    )


def _select_image(snapshot: dict | None) -> dict | None:
    """按稳定优先级选取当前页面也会展示的第一张有效图片。"""
    if not snapshot:
        return None
    candidates = [
        item for item in snapshot.get("list", [])
        if isinstance(item, dict)
        and item.get("dataType") == "image"
        and _valid_image_url(item.get("value"))
    ]
    if not candidates:
        return None
    by_identifier = {
        str(item.get("identifier") or "").lower().replace("_", ""): item
        for item in candidates
    }
    selected = next(
        (by_identifier[key] for key in PREFERRED_IMAGE_IDENTIFIERS if key in by_identifier),
        candidates[0],
    )
    return {
        "identifier": str(selected.get("identifier") or ""),
        "url": str(selected.get("value") or "").strip(),
        "fetched_at": snapshot.get("fetched_at") or "",
    }


def _task_payload(context: dict, admin_id: int) -> dict:
    return {
        "admin_id": admin_id,
        "device_id": context["device"]["id"],
        "plot_id": context["plot"]["id"],
        "model_id": context["model"]["id"],
        "confidence_threshold": float(context["model"].get("default_confidence", 0.25)),
        "image_identifier": context["image"]["identifier"],
        "image_url": context["image"]["url"],
        "snapshot_fetched_at": context["image"]["fetched_at"],
    }


def _idempotency_key(payload: dict) -> str:
    frozen = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "quick-detect:" + hashlib.sha256(frozen.encode("utf-8")).hexdigest()


class DetectionService:
    """提供前端预检、任务提交和只暴露本人任务的状态查询。"""

    @staticmethod
    async def get_context(db, device_id: int) -> dict:
        device = await get_hardware_device_info(device_id)
        if not device:
            raise DetectionReferenceError("设备不存在")
        context = {
            "ready": False,
            "reason": "",
            "device": {
                "id": int(device["id"]),
                "name": device.get("nickname") or device.get("device_name") or "",
                "available": bool(device.get("available")),
            },
            "plot": None,
            "model": None,
            "image": None,
            "queue_enabled": False,
        }
        if "realtime" not in device.get("capabilities", []):
            context["reason"] = "仅支持实时图片的设备支持快捷检测"
            return context
        if not device.get("plot_id"):
            context["reason"] = "请先在基本设置中绑定地块"
            return context
        context["plot"] = {
            "id": int(device["plot_id"]),
            "name": device.get("plot_name") or "",
            "area_name": device.get("area_name") or "",
        }
        row = (await db.execute(
            select(YoloModelPlotBinding, YoloModel)
            .join(YoloModel, YoloModel.id == YoloModelPlotBinding.model_id)
            .where(YoloModelPlotBinding.plot_id == device["plot_id"])
        )).one_or_none()
        if not row:
            context["reason"] = "请先在智能识别中为该地块绑定模型"
            return context
        _binding, model = row
        context["model"] = {
            "id": int(model.id),
            "name": model.name,
            "version": model.version or "",
            "default_confidence": float(model.default_confidence or 0.25),
        }
        if not YoloModelService.get_download_path(model):
            context["reason"] = "绑定模型文件已丢失，请重新上传模型"
            return context
        context["image"] = _select_image(await read_latest_device_snapshot(device_id))
        if not context["image"]:
            context["reason"] = "暂无可检测的图片，请先刷新或拍照"
            return context
        queue_value = await ConfigManager().get("task_queue_enabled", db)
        context["queue_enabled"] = queue_value in (None, "", "1", 1, True)
        if not context["queue_enabled"]:
            context["reason"] = "任务队列已关闭，请先在任务队列设置中启用"
            return context
        context["ready"] = True
        return context

    @staticmethod
    async def submit(context: dict, admin_id: int) -> dict:
        if not context.get("ready"):
            raise DetectionNotReadyError(context.get("reason") or "当前无法快捷检测")
        payload = _task_payload(context, admin_id)
        key = _idempotency_key(payload)
        task_id = await submit_task(
            TASK_NAME,
            payload,
            description=f"快捷检测设备 {payload['device_id']}",
            idempotency_key=key,
            correlation_id=f"yolo-detect:{payload['device_id']}",
        )
        task = await get_task_queue_item(task_id)
        if task and task.get("status") == "Dead":
            await retry_queue_task(task_id)
            task = await get_task_queue_item(task_id)
        elif task and task.get("status") == "Cancelled":
            task_id = await submit_task(
                TASK_NAME,
                payload,
                description=f"快捷检测设备 {payload['device_id']}",
                idempotency_key=f"{key}:{uuid4().hex}",
                correlation_id=f"yolo-detect:{payload['device_id']}",
            )
            task = await get_task_queue_item(task_id)
        return {"task_id": task_id, "status": _map_task_status(task)}

    @staticmethod
    async def get_status(task_id: int, admin_id: int) -> dict | None:
        async with async_session_factory() as db:
            record = await RecognitionService.get_record_by_task(db, task_id)
        if record:
            if record["admin_id"] != admin_id:
                return None
            return {
                "task_id": task_id,
                "status": "succeeded",
                "message": "检测完成",
                "record": record,
            }
        task = await get_task_queue_item(task_id)
        if not task or task.get("owner") != "yolo_model_manager" or task.get("definition") != TASK_NAME:
            return None
        task_data = task.get("task_data") if isinstance(task.get("task_data"), dict) else {}
        if int(task_data.get("admin_id") or 0) != admin_id:
            return None
        status = _map_task_status(task)
        messages = {
            "queued": "检测任务正在排队",
            "running": "模型正在识别图片",
            "paused": "检测任务已暂停",
            "cancelled": "检测任务已取消",
            "failed": task.get("error_msg") or "检测失败",
        }
        return {
            "task_id": task_id,
            "status": status,
            "message": messages.get(status, "检测任务处理中"),
            "attempt": int(task.get("attempt") or 0),
            "max_attempts": int(task.get("max_attempts") or 1),
            "record": None,
        }


def _map_task_status(task: dict | None) -> str:
    status = (task or {}).get("status")
    return {
        "Wait": "queued",
        "Exec": "running",
        "Paused": "paused",
        "Cancelled": "cancelled",
        "Dead": "failed",
        "Finish": "running",
    }.get(status, "queued")


def run_yolo_inference(
    model_path: Path, image, confidence_threshold: float
) -> tuple[list[dict], int, int, bytes]:
    """同步执行 Ultralytics 检测，由任务处理器放入工作线程。"""
    from ultralytics import YOLO

    results = YOLO(str(model_path), task="detect").predict(
        source=image,
        conf=confidence_threshold,
        imgsz=640,
        device="cpu",
        verbose=False,
    )
    result = results[0]
    names = result.names
    detections = []
    if result.boxes is not None:
        coordinates = result.boxes.xyxy.cpu().tolist()
        confidences = result.boxes.conf.cpu().tolist()
        classes = result.boxes.cls.cpu().tolist()
        for bbox, confidence, class_id in zip(coordinates, confidences, classes):
            index = int(class_id)
            label = _resolve_model_label(names, index)
            detections.append({
                "label": str(label),
                "confidence": round(float(confidence), 6),
                "bbox": [round(float(value), 3) for value in bbox],
            })
    try:
        annotated = result.plot(labels=True, conf=True)
    except (IndexError, KeyError, TypeError, AttributeError) as exc:
        logger.warning(
            "[yolo_model_manager] 标注图标签渲染失败，改用无文字标注: error_type=%s",
            type(exc).__name__,
        )
        annotated = result.plot(labels=False, conf=True)
    from PIL import Image

    if isinstance(annotated, Image.Image):
        annotated_image = annotated.convert("RGB")
    else:
        annotated_image = Image.fromarray(annotated[..., ::-1]).convert("RGB")
    output = BytesIO()
    annotated_image.save(output, format="JPEG", quality=90)
    return detections, int(image.width), int(image.height), output.getvalue()


def _write_annotated_image(task_id: int, content: bytes) -> str:
    """在公开上传目录中原子写入任务唯一的标注结果图。"""
    upload_root = (BASE_DIR / "upload").resolve()
    plugin_dir = (upload_root / "yolo_model_manager").resolve()
    result_dir = ANNOTATED_IMAGE_DIR.resolve()
    if plugin_dir.parent != upload_root or result_dir.parent != plugin_dir:
        raise RuntimeError("标注图保存目录校验失败")
    result_dir.mkdir(parents=True, exist_ok=True)
    target = result_dir / f"{int(task_id)}.jpg"
    temporary = result_dir / f".{int(task_id)}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return f"{ANNOTATED_IMAGE_URL_PREFIX}/{target.name}"


def _delete_annotated_image(task_id: int) -> None:
    """仅删除当前任务按固定命名生成的标注图。"""
    target = (ANNOTATED_IMAGE_DIR / f"{int(task_id)}.jpg").resolve()
    if target.parent == ANNOTATED_IMAGE_DIR.resolve():
        target.unlink(missing_ok=True)


async def handle_detection_task(context, data: dict) -> None:
    """重验绑定、执行推理并以任务ID幂等写入不可变识别记录。"""
    async with async_session_factory() as db:
        existing = await RecognitionService.get_record_by_task(db, context.task_id)
        if existing:
            return
        device = await get_hardware_device_info(int(data["device_id"]))
        if not device or int(device.get("plot_id") or 0) != int(data["plot_id"]):
            raise DetectionNotReadyError("设备绑定地块已变化，请重新发起检测")
        row = (await db.execute(
            select(YoloModelPlotBinding, YoloModel)
            .join(YoloModel, YoloModel.id == YoloModelPlotBinding.model_id)
            .where(
                YoloModelPlotBinding.plot_id == int(data["plot_id"]),
                YoloModelPlotBinding.model_id == int(data["model_id"]),
            )
        )).one_or_none()
        if not row:
            raise DetectionNotReadyError("地块绑定模型已变化，请重新发起检测")
        model_path = YoloModelService.get_download_path(row[1])
        if not model_path:
            raise DetectionNotReadyError("绑定模型文件已丢失")

    try:
        content = await _read_image_bytes(str(data["image_url"]))
        image = await asyncio.to_thread(_decode_image, content)
    except Exception as exc:
        logger.exception(
            "[yolo_model_manager] 检测图片准备失败: task_id=%s model=%s error_type=%s",
            context.task_id,
            model_path,
            type(exc).__name__,
        )
        raise
    try:
        detections, width, height, annotated_content = await asyncio.to_thread(
            run_yolo_inference,
            model_path,
            image,
            float(data.get("confidence_threshold", 0.25)),
        )
    except Exception as exc:
        logger.exception(
            "[yolo_model_manager] 模型推理失败: task_id=%s model=%s error_type=%s",
            context.task_id,
            model_path,
            type(exc).__name__,
        )
        raise
    annotated_image_url = await asyncio.to_thread(
        _write_annotated_image, context.task_id, annotated_content
    )
    try:
        async with async_session_factory() as db:
            await RecognitionService.create_record(
                db,
                plot_id=int(data["plot_id"]),
                model_id=int(data["model_id"]),
                image_url=str(data["image_url"]),
                annotated_image_url=annotated_image_url,
                detections=detections,
                recognized_at=None,
                admin_id=int(data["admin_id"]),
                task_id=context.task_id,
                source_device_id=int(data["device_id"]),
                image_identifier=str(data.get("image_identifier") or ""),
                image_width=width,
                image_height=height,
            )
    except Exception:
        await asyncio.to_thread(_delete_annotated_image, context.task_id)
        raise
