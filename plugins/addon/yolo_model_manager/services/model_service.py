# -*- coding: utf-8 -*-
"""YOLO 模型文件、元数据与地块绑定业务服务。"""

import asyncio
import hashlib
import logging
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from core.production_area_service import list_area_tree
from plugins.addon.yolo_model_manager.models import YoloModel, YoloModelPlotBinding
from plugins.addon.yolo_model_manager.services.label_extractor import (
    ModelDependencyError,
    ModelLabelError,
    extract_model_labels,
)
from plugins.addon.yolo_model_manager.upload_policies import get_policy_definition
from services.upload_policy import (
    UploadPolicyError,
    get_effective_policy,
    stream_upload,
    validate_filename,
)

logger = logging.getLogger(__name__)
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "upload"


class BindingConflictError(ValueError):
    """目标地块已被其他模型绑定，或并发写入触发唯一约束。"""


class PlotSelectionError(ValueError):
    """目标地块不存在、已停用或所属产区已停用。"""


def _plot_catalog(tree: list[dict]) -> dict[int, dict]:
    """将公共产区树压平为地块索引，供序列化和绑定校验复用。"""
    result: dict[int, dict] = {}
    for area in tree:
        for plot in area.get("children", []):
            if plot.get("type") != "plot":
                continue
            result[int(plot["id"])] = {
                "plot_id": int(plot["id"]),
                "plot_name": plot.get("name", ""),
                "plot_status": int(plot.get("status", 0)),
                "area_id": int(area["id"]),
                "area_name": area.get("name", ""),
                "area_status": int(area.get("status", 0)),
            }
    return result


class YoloModelService:
    """集中维护模型和绑定规则，路由层只负责协议映射与审计。"""

    @staticmethod
    async def get_model(db, model_id: int):
        """按主键查询模型，不存在返回 None。"""
        return (await db.execute(
            select(YoloModel).where(YoloModel.id == model_id)
        )).scalar_one_or_none()

    @staticmethod
    async def save_upload(
        db, *, upload_file, name: str, version: str, description: str, admin_id: int,
        default_confidence: float = 0.25,
    ) -> tuple[int, list[str]]:
        """流式保存模型并写库；任何写库异常都会删除本次落盘文件。"""
        policy = await get_effective_policy(get_policy_definition(), db)
        origin_name = upload_file.filename or ""
        try:
            extension = validate_filename(origin_name, policy)
        except UploadPolicyError as exc:
            raise ValueError(str(exc)) from exc

        disk_name = f"{uuid.uuid4().hex}{extension}"
        destination = UPLOAD_DIR / disk_name
        digest = hashlib.sha256()
        try:
            file_size = await stream_upload(
                upload_file,
                destination,
                policy["max_size_mb"],
                on_chunk=digest.update,
            )
        except UploadPolicyError as exc:
            raise ValueError(str(exc)) from exc

        try:
            labels = await asyncio.to_thread(extract_model_labels, destination)
        except ModelLabelError as exc:
            root_error = exc
            while root_error.__cause__ is not None:
                root_error = root_error.__cause__
            try:
                import ultralytics
                ultralytics_version = str(getattr(ultralytics, "__version__", "unknown"))
            except Exception:
                ultralytics_version = "unavailable"
            logger.warning(
                "[yolo_model_manager] 模型标签读取失败: origin=%s format=%s size=%d "
                "python=%s ultralytics=%s error_type=%s error=%s",
                origin_name,
                extension.lstrip("."),
                file_size,
                ".".join(str(item) for item in sys.version_info[:3]),
                ultralytics_version,
                type(root_error).__name__,
                exc,
                exc_info=exc,
            )
            destination.unlink(missing_ok=True)
            if isinstance(exc, ModelDependencyError):
                message = f"无法读取模型标签：{exc}"
            else:
                message = (
                    f"无法读取模型标签：{exc}；可能是不兼容的旧版或自定义检查点，"
                    "请使用当前 Ultralytics 重新导出模型"
                )
            raise ValueError(message) from exc

        try:
            row = YoloModel(
                name=name.strip(),
                version=version.strip(),
                description=description.strip(),
                default_confidence=Decimal(str(default_confidence)),
                filename=disk_name,
                origin_name=origin_name,
                file_format=extension.lstrip("."),
                file_size=file_size,
                sha256=digest.hexdigest(),
                labels=labels,
                admin_id=admin_id,
            )
            db.add(row)
            await db.flush()
            await db.commit()
            await db.refresh(row)
            return int(row.id), labels
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    async def list_models(
        db,
        *,
        keyword: str,
        model_format: str,
        binding_status: str,
        page: int,
        limit: int,
    ) -> dict:
        """分页查询模型，并批量聚合本页地块绑定，避免 N+1 查询。"""
        query = select(YoloModel)
        if keyword:
            query = query.where(or_(
                YoloModel.name.contains(keyword),
                YoloModel.version.contains(keyword),
                YoloModel.origin_name.contains(keyword),
                YoloModel.labels.contains(keyword),
            ))
        if model_format:
            query = query.where(YoloModel.file_format == model_format)
        binding_exists = select(YoloModelPlotBinding.id).where(
            YoloModelPlotBinding.model_id == YoloModel.id
        ).exists()
        if binding_status == "bound":
            query = query.where(binding_exists)
        elif binding_status == "unbound":
            query = query.where(~binding_exists)

        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            query.order_by(YoloModel.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )).scalars().all()

        binding_map: dict[int, list[int]] = {}
        if rows:
            pairs = (await db.execute(
                select(YoloModelPlotBinding.model_id, YoloModelPlotBinding.plot_id)
                .where(YoloModelPlotBinding.model_id.in_([row.id for row in rows]))
                .order_by(YoloModelPlotBinding.plot_id)
            )).all()
            for model_id, plot_id in pairs:
                binding_map.setdefault(int(model_id), []).append(int(plot_id))
        catalog = _plot_catalog(await list_area_tree())
        return {
            "total": int(total),
            "page": page,
            "limit": limit,
            "list": [
                YoloModelService.to_dict(row, binding_map.get(int(row.id), []), catalog)
                for row in rows
            ],
        }

    @staticmethod
    async def update_metadata(
        db, model_id: int, *, name: str, version: str, description: str,
        default_confidence: float = 0.25,
    ) -> bool:
        """更新模型可读元数据，不替换文件与摘要。"""
        row = await YoloModelService.get_model(db, model_id)
        if not row:
            return False
        row.name = name.strip()
        row.version = version.strip()
        row.description = description.strip()
        row.default_confidence = Decimal(str(default_confidence))
        await db.commit()
        return True

    @staticmethod
    async def delete_model(db, model_id: int) -> tuple[str, int]:
        """删除未绑定模型；返回状态和当前绑定数量。"""
        row = await YoloModelService.get_model(db, model_id)
        if not row:
            return "not_found", 0
        binding_count = (await db.execute(
            select(func.count()).select_from(YoloModelPlotBinding)
            .where(YoloModelPlotBinding.model_id == model_id)
        )).scalar() or 0
        if binding_count:
            return "bound", int(binding_count)

        disk_name = row.filename
        await db.delete(row)
        await db.commit()
        try:
            (UPLOAD_DIR / disk_name).unlink(missing_ok=True)
        except OSError:
            logger.warning("[yolo_model_manager] 模型文件删除失败: %s", disk_name)
        return "deleted", 0

    @staticmethod
    def get_download_path(row) -> Path | None:
        """只解析插件私有目录内的 UUID 文件，缺失时返回 None。"""
        path = UPLOAD_DIR / row.filename
        return path if path.is_file() else None

    @staticmethod
    async def list_plots(db, current_model_id: int | None = None) -> list[dict]:
        """返回产区分组地块及占用信息，供绑定弹窗一次加载。"""
        if current_model_id and not await YoloModelService.get_model(db, current_model_id):
            raise LookupError("模型不存在")
        occupied_rows = (await db.execute(
            select(
                YoloModelPlotBinding.plot_id,
                YoloModelPlotBinding.model_id,
                YoloModel.name,
            ).join(YoloModel, YoloModel.id == YoloModelPlotBinding.model_id)
        )).all()
        occupied = {
            int(plot_id): {"model_id": int(model_id), "model_name": model_name}
            for plot_id, model_id, model_name in occupied_rows
        }

        result: list[dict] = []
        for area in await list_area_tree():
            area_active = int(area.get("status", 0)) == 1
            plots = []
            for plot in area.get("children", []):
                if plot.get("type") != "plot":
                    continue
                plot_id = int(plot["id"])
                binding = occupied.get(plot_id)
                plot_active = int(plot.get("status", 0)) == 1
                occupied_by_current = bool(
                    binding and binding["model_id"] == current_model_id
                )
                occupied_by_other = bool(
                    binding and binding["model_id"] != current_model_id
                )
                plots.append({
                    "id": plot_id,
                    "name": plot.get("name", ""),
                    "status": int(plot.get("status", 0)),
                    "model_id": binding["model_id"] if binding else None,
                    "model_name": binding["model_name"] if binding else "",
                    "selectable": (
                        not occupied_by_other
                        and ((area_active and plot_active) or occupied_by_current)
                    ),
                })
            result.append({
                "id": int(area["id"]),
                "name": area.get("name", ""),
                "status": int(area.get("status", 0)),
                "plots": plots,
            })
        return result

    @staticmethod
    async def replace_bindings(
        db, model_id: int, plot_ids: list[int], admin_id: int,
    ) -> bool:
        """原子替换模型绑定；冲突时整笔回滚，不静默抢占其他模型。"""
        if not await YoloModelService.get_model(db, model_id):
            return False
        targets = sorted(plot_ids)
        current_ids = set((await db.execute(
            select(YoloModelPlotBinding.plot_id).where(
                YoloModelPlotBinding.model_id == model_id
            )
        )).scalars().all())
        catalog = _plot_catalog(await list_area_tree())
        missing = [plot_id for plot_id in targets if plot_id not in catalog]
        if missing:
            raise PlotSelectionError(f"地块不存在: {missing}")
        inactive = [
            plot_id for plot_id in targets
            if plot_id not in current_ids and (
                catalog[plot_id]["plot_status"] != 1
                or catalog[plot_id]["area_status"] != 1
            )
        ]
        if inactive:
            raise PlotSelectionError(f"地块或所属产区已停用: {inactive}")

        if targets:
            conflicts = (await db.execute(
                select(
                    YoloModelPlotBinding.plot_id,
                    YoloModel.name,
                ).join(YoloModel, YoloModel.id == YoloModelPlotBinding.model_id)
                .where(
                    YoloModelPlotBinding.plot_id.in_(targets),
                    YoloModelPlotBinding.model_id != model_id,
                )
            )).all()
            if conflicts:
                details = ", ".join(f"{plot_id}（{name}）" for plot_id, name in conflicts)
                raise BindingConflictError(f"地块已被其他模型绑定: {details}")

        await db.execute(delete(YoloModelPlotBinding).where(
            YoloModelPlotBinding.model_id == model_id
        ))
        db.add_all([
            YoloModelPlotBinding(
                model_id=model_id,
                plot_id=plot_id,
                admin_id=admin_id,
            )
            for plot_id in targets
        ])
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise BindingConflictError("地块刚被其他模型绑定，请刷新后重试") from exc
        return True

    @staticmethod
    def to_dict(row, plot_ids: list[int], catalog: dict[int, dict]) -> dict:
        """管理端模型序列化，不暴露私有磁盘文件名。"""
        bindings = []
        for plot_id in plot_ids:
            info = catalog.get(plot_id)
            bindings.append(info or {
                "plot_id": plot_id,
                "plot_name": "地块已不存在",
                "plot_status": 0,
                "area_id": 0,
                "area_name": "",
                "area_status": 0,
            })
        return {
            "id": int(row.id),
            "name": row.name,
            "version": row.version,
            "description": row.description,
            "default_confidence": float(row.default_confidence or 0.25),
            "origin_name": row.origin_name,
            "file_format": row.file_format,
            "file_size": int(row.file_size),
            "sha256": row.sha256,
            "labels": list(row.labels or []),
            "admin_id": int(row.admin_id),
            "binding_count": len(bindings),
            "plot_ids": plot_ids,
            "bindings": bindings,
            "create_time": str(row.create_time) if row.create_time else None,
            "update_time": str(row.update_time) if row.update_time else None,
        }
