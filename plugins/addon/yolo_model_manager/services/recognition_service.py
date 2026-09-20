# -*- coding: utf-8 -*-
"""智能识别记录的写入、查询和历史筛选选项服务。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from core.production_area_service import list_area_tree
from core.time_utils import china_now
from plugins.addon.yolo_model_manager.models import (
    RecognitionRecord,
    YoloModel,
    YoloModelPlotBinding,
)

CHINA_ZONE = ZoneInfo("Asia/Shanghai")


class RecognitionReferenceError(LookupError):
    """地块或模型不存在，无法形成可信识别快照。"""


class RecognitionBindingError(ValueError):
    """调用方声明的模型不是地块当前绑定模型。"""


def _normalize_recognized_at(value: datetime | None) -> datetime:
    """将带时区时间转为无时区中国时间，兼容项目DATETIME约定。"""
    if value is None:
        return china_now()
    if value.tzinfo is None:
        return value
    return value.astimezone(CHINA_ZONE).replace(tzinfo=None)


def _plot_snapshots(tree: list[dict]) -> dict[int, dict]:
    """从公共产区树生成当前地块快照索引。"""
    snapshots: dict[int, dict] = {}
    for area in tree:
        for plot in area.get("children", []):
            if plot.get("type") != "plot":
                continue
            snapshots[int(plot["id"])] = {
                "area_id": int(area["id"]),
                "area_name": str(area.get("name") or ""),
                "plot_id": int(plot["id"]),
                "plot_name": str(plot.get("name") or ""),
            }
    return snapshots


def _format_time(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class RecognitionService:
    """维护识别记录不可变、绑定可信和历史名称可读三项业务规则。"""

    @staticmethod
    async def create_record(
        db,
        *,
        plot_id: int | None,
        model_id: int,
        image_url: str,
        annotated_image_url: str = "",
        detections: list[dict],
        recognized_at: datetime | None,
        admin_id: int,
        task_id: int | None = None,
        source_device_id: int | None = None,
        image_identifier: str = "",
        image_width: int | None = None,
        image_height: int | None = None,
        source_type: str = "quick_detection",
    ) -> dict:
        """校验引用后写入包含模型、来源和可选地块的完整业务快照。"""
        model = (await db.execute(
            select(YoloModel).where(YoloModel.id == model_id)
        )).scalar_one_or_none()
        if not model:
            raise RecognitionReferenceError("模型不存在")

        plot = None
        if plot_id is not None:
            binding = (await db.execute(select(YoloModelPlotBinding.id).where(
                YoloModelPlotBinding.plot_id == plot_id,
                YoloModelPlotBinding.model_id == model_id,
            ))).scalar_one_or_none()
            if not binding:
                raise RecognitionBindingError("该模型不是地块当前绑定模型")
            plot = _plot_snapshots(await list_area_tree()).get(plot_id)
            if not plot:
                raise RecognitionReferenceError("地块不存在")
        if source_type not in {"model_test", "quick_detection", "external"}:
            raise ValueError("识别来源类型无效")

        confidences = [float(item["confidence"]) for item in detections]
        row = RecognitionRecord(
            **(plot or {
                "area_id": None, "area_name": None,
                "plot_id": None, "plot_name": None,
            }),
            model_id=int(model.id),
            model_name=model.name,
            model_version=model.version or "",
            task_id=task_id,
            source_device_id=source_device_id,
            source_type=source_type,
            image_identifier=image_identifier,
            image_url=image_url,
            annotated_image_url=annotated_image_url,
            image_width=image_width,
            image_height=image_height,
            detections=detections,
            detection_count=len(detections),
            max_confidence=max(confidences) if confidences else None,
            recognized_at=_normalize_recognized_at(recognized_at),
            admin_id=admin_id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return RecognitionService.to_detail(row)

    @staticmethod
    async def list_records(
        db, *, plot_id: int | None, model_id: int | None, page: int, limit: int,
        source_type: str | None = None,
    ) -> dict:
        """按地块、模型筛选并以识别时间倒序分页。"""
        query = select(RecognitionRecord)
        if plot_id:
            query = query.where(RecognitionRecord.plot_id == plot_id)
        if model_id:
            query = query.where(RecognitionRecord.model_id == model_id)
        if source_type:
            query = query.where(RecognitionRecord.source_type == source_type)
        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            query.order_by(RecognitionRecord.recognized_at.desc(), RecognitionRecord.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )).scalars().all()
        return {
            "total": int(total),
            "page": page,
            "limit": limit,
            "list": [RecognitionService.to_summary(row) for row in rows],
        }

    @staticmethod
    async def get_record(db, record_id: int) -> dict | None:
        """读取单条完整记录，不依赖当前模型或地块状态。"""
        row = (await db.execute(
            select(RecognitionRecord).where(RecognitionRecord.id == record_id)
        )).scalar_one_or_none()
        return RecognitionService.to_detail(row) if row else None

    @staticmethod
    async def get_record_by_task(db, task_id: int) -> dict | None:
        """通过检测任务读取最终结果，成功队列项清理后仍可查询。"""
        row = (await db.execute(
            select(RecognitionRecord).where(RecognitionRecord.task_id == task_id)
        )).scalar_one_or_none()
        return RecognitionService.to_detail(row) if row else None

    @staticmethod
    async def list_options(db) -> dict:
        """当前资源优先，并补入已经删除或改名的历史快照选项。"""
        plots = {
            item["plot_id"]: {
                "value": item["plot_id"],
                "label": f'{item["area_name"]} / {item["plot_name"]}',
            }
            for item in _plot_snapshots(await list_area_tree()).values()
        }
        models = {
            int(row.id): {
                "value": int(row.id),
                "label": f"{row.name} {row.version}".strip(),
            }
            for row in (await db.execute(select(YoloModel))).scalars().all()
        }
        historical = (await db.execute(select(
            RecognitionRecord.plot_id,
            RecognitionRecord.area_name,
            RecognitionRecord.plot_name,
            RecognitionRecord.model_id,
            RecognitionRecord.model_name,
            RecognitionRecord.model_version,
        ).order_by(RecognitionRecord.recognized_at.desc()))).all()
        for row in historical:
            if row.plot_id is None:
                continue
            plots.setdefault(int(row.plot_id), {
                "value": int(row.plot_id),
                "label": f"{row.area_name} / {row.plot_name}",
            })
            models.setdefault(int(row.model_id), {
                "value": int(row.model_id),
                "label": f"{row.model_name} {row.model_version}".strip(),
            })
        return {
            "plots": sorted(plots.values(), key=lambda item: item["label"]),
            "models": sorted(models.values(), key=lambda item: item["label"]),
        }

    @staticmethod
    def to_summary(row: RecognitionRecord) -> dict:
        """列表只暴露标签摘要，完整坐标由详情接口按需读取。"""
        labels = list(dict.fromkeys(
            str(item.get("label") or "") for item in (row.detections or [])
            if item.get("label")
        ))
        return {
            "id": int(row.id),
            "area_id": int(row.area_id) if row.area_id is not None else None,
            "area_name": row.area_name,
            "plot_id": int(row.plot_id) if row.plot_id is not None else None,
            "plot_name": row.plot_name,
            "model_id": int(row.model_id),
            "model_name": row.model_name,
            "model_version": row.model_version,
            "source_device_id": row.source_device_id,
            "source_type": row.source_type or "external",
            "image_identifier": row.image_identifier or "",
            "image_url": row.image_url,
            "annotated_image_url": row.annotated_image_url or "",
            "image_width": row.image_width,
            "image_height": row.image_height,
            "labels": labels,
            "detection_count": int(row.detection_count),
            "max_confidence": (
                float(row.max_confidence) if row.max_confidence is not None else None
            ),
            "recognized_at": _format_time(row.recognized_at),
        }

    @staticmethod
    def to_detail(row: RecognitionRecord) -> dict:
        """详情在列表摘要基础上补充完整识别目标和写入信息。"""
        return {
            **RecognitionService.to_summary(row),
            "task_id": row.task_id,
            "detections": list(row.detections or []),
            "admin_id": int(row.admin_id),
            "create_time": _format_time(row.create_time),
        }
