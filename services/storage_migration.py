# -*- coding: utf-8 -*-
"""公共 upload 目录的对象存储扫描、迁移和引用更新服务。"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import func, select, update

from core.config import BASE_DIR
from core.db.base import async_session_factory
from core.db.storage_migration import StorageMigrationItem, StorageMigrationJob
from core.file_log_service import ensure_file_log, local_path_digest, update_file_storage
from core.oss_service import oss_service
from services.task.queue_worker import submit_task

logger = logging.getLogger(__name__)
UPLOAD_ROOT = (BASE_DIR / "upload").resolve()
SCAN_DEFINITION = "storage_public_scan"
MIGRATE_DEFINITION = "storage_public_migrate"
SCAN_JOB_LOCK = asyncio.Lock()


class StorageConflictError(RuntimeError):
    """远端存在同键但内容不同，禁止覆盖。"""


def _safe_upload_path(value: str) -> Path:
    normalized = oss_service.normalize_local_path(value)
    candidate = UPLOAD_ROOT / normalized
    if candidate.is_symlink():
        raise ValueError("迁移不允许跟随符号链接")
    path = candidate.resolve()
    if UPLOAD_ROOT not in path.parents or path.is_symlink():
        raise ValueError("文件路径超出 upload 目录")
    return path


async def create_scan_job(target_method: str) -> int:
    """创建或复用目标存储的公共文件同步任务。"""
    if not target_method or target_method == "local_oss":
        raise ValueError("目标存储必须是已安装的远程对象存储插件")

    resume_job_id = None
    async with SCAN_JOB_LOCK:
        async with async_session_factory() as db:
            active_job = (await db.execute(
                select(StorageMigrationJob).where(
                    StorageMigrationJob.target_method == target_method,
                    StorageMigrationJob.phase.in_(("scanning", "queued", "running", "awaiting")),
                ).order_by(StorageMigrationJob.id.desc()).limit(1)
            )).scalar_one_or_none()
            if active_job:
                if active_job.phase == "awaiting":
                    resume_job_id = int(active_job.id)
                else:
                    return int(active_job.id)
            if resume_job_id is None:
                job = StorageMigrationJob(target_method=target_method, phase="scanning")
                db.add(job)
                await db.commit()
                await db.refresh(job)
                job_id = int(job.id)

    if resume_job_id is not None:
        # 兼容旧版本在扫描结束后停在 awaiting 的任务；新的页面不再显示确认按钮。
        await start_job(resume_job_id)
        return resume_job_id

    try:
        task_id = await submit_task(
            SCAN_DEFINITION, {"job_id": job_id},
            description=f"扫描公共文件并准备迁移: {target_method}",
            idempotency_key=f"storage-scan:{job_id}",
        )
    except Exception as exc:
        await _mark_job_failed(job_id, f"扫描任务入队失败: {exc}")
        logger.exception("[存储迁移] 扫描任务入队失败: job_id=%s", job_id)
        return job_id

    if not task_id:
        await _mark_job_failed(job_id, "扫描任务入队失败：未返回任务编号")
        return job_id

    async with async_session_factory() as db:
        await db.execute(update(StorageMigrationJob).where(
            StorageMigrationJob.id == job_id,
        ).values(task_id=task_id))
        await db.commit()
    return job_id


async def _mark_job_failed(job_id: int, message: str) -> None:
    """持久化任务级失败，避免页面对无进展任务无限轮询。"""
    async with async_session_factory() as db:
        await db.execute(update(StorageMigrationJob).where(
            StorageMigrationJob.id == job_id,
        ).values(phase="failed", error_msg=message))
        await db.commit()


async def scan_job(job_id: int) -> None:
    """扫描 upload/ 下普通文件，不跟随符号链接。"""
    try:
        await _scan_job(job_id)
    except Exception as exc:
        logger.exception("[存储迁移] 公共文件扫描失败: job_id=%s", job_id)
        await _mark_job_failed(job_id, f"扫描失败: {exc}")
        raise


async def _scan_job(job_id: int) -> None:
    """执行扫描主体；外层负责把异常持久化为可见的失败阶段。"""
    async with async_session_factory() as db:
        job = (await db.execute(select(StorageMigrationJob).where(StorageMigrationJob.id == job_id))).scalar_one()
        files = []
        target_prefix = ""
        try:
            async with async_session_factory() as config_db:
                from core.config_manager import ConfigManager
                target_config = await ConfigManager().get_plugin_config(job.target_method, config_db)
            raw_prefix = str(target_config.get("save_path") or "").strip()
            target_prefix = oss_service.normalize_local_path(raw_prefix) if raw_prefix else ""
        except Exception:
            target_prefix = ""
        if UPLOAD_ROOT.exists():
            for path in UPLOAD_ROOT.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(UPLOAD_ROOT).as_posix()
                stat = path.stat()
                files.append((relative, int(stat.st_size), int(stat.st_mtime_ns)))
        for relative, size, mtime_ns in files:
            object_key = f"{target_prefix}/{relative}" if target_prefix else relative
            db.add(StorageMigrationItem(
                job_id=job.id, local_path=relative, path_hash=local_path_digest(relative), object_key=object_key,
                file_size=size, mtime_ns=mtime_ns,
            ))
        job.total_files = len(files)
        job.total_bytes = sum(item[1] for item in files)
        job.phase = "empty" if not files else "awaiting"
        await db.commit()
    if files:
        await start_job(job_id)


async def start_job(job_id: int, *, retry: bool = False) -> int | None:
    """将扫描完成的任务或失败明细加入迁移队列。

    重试必须使用新的幂等键。原任务即使已经进入 Dead，旧幂等键仍然会
    命中任务队列表唯一索引，导致接口返回旧任务却没有重新执行。
    """
    async with async_session_factory() as db:
        job = (await db.execute(select(StorageMigrationJob).where(StorageMigrationJob.id == job_id))).scalar_one_or_none()
        if not job:
            raise ValueError("迁移任务当前不可启动")
        if retry:
            if job.phase not in {"partial", "failed"}:
                raise ValueError("迁移任务当前没有可重试的失败项")
            pending = await db.scalar(select(func.count(StorageMigrationItem.id)).where(
                StorageMigrationItem.job_id == job.id,
                StorageMigrationItem.status == "failed",
            ))
            if not pending:
                raise ValueError("迁移任务当前没有可重试的失败项")
            await db.execute(update(StorageMigrationItem).where(
                StorageMigrationItem.job_id == job.id,
                StorageMigrationItem.status == "failed",
            ).values(status="pending", attempts=0, error_msg=""))
            idempotency_key = f"storage-migrate:{job.id}:retry:{uuid.uuid4().hex}"
        else:
            if job.phase != "awaiting":
                raise ValueError("迁移任务当前不可启动")
            idempotency_key = f"storage-migrate:{job.id}"
        job.phase = "queued"
        job.error_msg = ""
        await db.commit()
        target_method = job.target_method
    try:
        task_id = await submit_task(
            MIGRATE_DEFINITION, {"job_id": int(job.id)},
            description=f"迁移公共文件到 {target_method}",
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        await _mark_job_failed(job_id, f"迁移任务入队失败: {exc}")
        logger.exception("[存储迁移] 迁移任务入队失败: job_id=%s", job_id)
        return None
    if not task_id:
        await _mark_job_failed(job_id, "迁移任务入队失败：未返回任务编号")
        return None

    async with async_session_factory() as db:
        await db.execute(update(StorageMigrationJob).where(
            StorageMigrationJob.id == job_id,
        ).values(task_id=task_id))
        await db.commit()
    return int(task_id)


async def _target_plugin(method: str):
    async with async_session_factory() as db:
        from core.config_manager import ConfigManager
        config = await ConfigManager().get_plugin_config(method, db)
    import importlib
    module = importlib.import_module(f"plugins.oss.{method}.plugin")
    plugin_cls = getattr(module, "Plugin", None)
    if not plugin_cls:
        raise ValueError("目标存储插件缺少实现")
    return plugin_cls(None, config)


async def _update_known_references(local_path: str, stable_url: str) -> None:
    """按精确旧地址更新已登记的公共业务引用。"""
    old_urls = {f"/upload/{local_path}", f"upload/{local_path}"}
    async with async_session_factory() as db:
        from core.db.certification import CertificationRecord
        from core.db.farmer import Farmer
        from core.db.hardware_device import HardwareDevice
        scalar_fields = (
            (Farmer, Farmer.avatar),
            (CertificationRecord, CertificationRecord.front_image),
            (CertificationRecord, CertificationRecord.back_image),
            (HardwareDevice, HardwareDevice.image_url),
        )
        for model, column in scalar_fields:
            await db.execute(update(model).where(column.in_(old_urls)).values({column.key: stable_url}))
        try:
            from plugins.addon.yolo_model_manager.models import RecognitionRecord
            await db.execute(update(RecognitionRecord).where(
                RecognitionRecord.image_url.in_(old_urls)
            ).values(image_url=stable_url))
            await db.execute(update(RecognitionRecord).where(
                RecognitionRecord.annotated_image_url.in_(old_urls)
            ).values(annotated_image_url=stable_url))
        except Exception:
            logger.debug("YOLO 识别引用更新跳过", exc_info=True)
        try:
            from plugins.addon.knowledge.models import KnowledgeEntry
            rows = (await db.execute(select(KnowledgeEntry))).scalars().all()
            for row in rows:
                try:
                    images = json.loads(str(row.images or "[]"))
                except (TypeError, json.JSONDecodeError):
                    continue
                changed = False
                for index, value in enumerate(images):
                    if value in old_urls:
                        images[index] = stable_url
                        changed = True
                if changed:
                    setattr(row, "images", json.dumps(images, ensure_ascii=False))
        except Exception:
            logger.debug("知识库引用更新跳过", exc_info=True)
        await db.commit()


async def _migrate_item(plugin, target_method: str, item_id: int) -> str:
    """迁移一个文件明细；所有状态和日志更新均使用独立事务。"""
    async with async_session_factory() as db:
        item = (await db.execute(select(StorageMigrationItem).where(
            StorageMigrationItem.id == item_id,
        ))).scalar_one_or_none()
        if not item or item.status not in {"pending", "failed"}:
            return "skipped"
        if item.attempts >= 3:
            return item.status
        local_path, object_key = item.local_path, item.object_key
        expected_size, expected_mtime = item.file_size, item.mtime_ns
        attempts = item.attempts + 1
        await db.execute(update(StorageMigrationItem).where(
            StorageMigrationItem.id == item.id,
        ).values(status="running", attempts=attempts, error_msg=""))
        await db.commit()

    try:
        path = _safe_upload_path(local_path)
        stat = path.stat()
        if stat.st_size != expected_size or stat.st_mtime_ns != expected_mtime:
            raise RuntimeError("文件在扫描后发生变化，请重新扫描")
        check = await plugin.oss_check_file({
            "save_path": str(path), "object_key": object_key, "_trusted_object_key": True,
            "file_size": stat.st_size, "source": "storage_migration",
        })
        check_status = str(check.get("status") or "").lower()
        if check_status == "same":
            result = {"status": "success", "data": {"url": ""}}
        elif check_status == "conflict":
            raise StorageConflictError(check.get("msg") or "远端已存在同名对象且内容不同")
        elif check_status == "missing":
            result = await plugin.oss_upload({
                "save_path": str(path), "save_name": path.name, "object_key": object_key,
                "original_name": path.name, "ext": path.suffix, "_trusted_object_key": True,
                "file_size": stat.st_size, "admin_id": None, "source": "storage_migration",
            })
        else:
            raise RuntimeError(check.get("msg") or "目标对象存储未提供文件一致性检查能力")
        if result.get("status") != "success":
            raise RuntimeError(result.get("msg", "对象上传失败"))
        stable_url = oss_service.stable_url(local_path)
        await update_file_storage(local_path, oss_method=target_method, object_key=object_key, url=stable_url)
        await ensure_file_log(
            path.name, path.name, path.suffix, stable_url, stat.st_size, None,
            "storage_migration", oss_method=target_method,
            local_path=local_path, object_key=object_key,
        )
        await _update_known_references(local_path, stable_url)
        async with async_session_factory() as db:
            await db.execute(update(StorageMigrationItem).where(StorageMigrationItem.id == item_id).values(
                status="success", error_msg=""))
            await db.commit()
        return "success"
    except StorageConflictError as exc:
        logger.warning("[存储迁移] 文件冲突 %s: %s", local_path, exc)
        async with async_session_factory() as db:
            await db.execute(update(StorageMigrationItem).where(StorageMigrationItem.id == item_id).values(
                status="conflict", error_msg=str(exc)))
            await db.commit()
        return "conflict"
    except Exception as exc:
        logger.warning("[存储迁移] 文件失败 %s: %s", local_path, exc)
        async with async_session_factory() as db:
            await db.execute(update(StorageMigrationItem).where(StorageMigrationItem.id == item_id).values(
                status="failed", error_msg=str(exc)))
            await db.commit()
        return "failed"


async def migrate_job(job_id: int) -> None:
    """按批次并发迁移，单文件失败不影响其他文件。"""
    async with async_session_factory() as db:
        job = (await db.execute(select(StorageMigrationJob).where(StorageMigrationJob.id == job_id))).scalar_one()
        target_method = job.target_method
        job.phase = "running"
        # Worker 进程在单文件处理期间退出时，明细可能停留在 running；
        # 下次任务执行前恢复为 pending，保证断点续传而不是永久卡住。
        await db.execute(update(StorageMigrationItem).where(
            StorageMigrationItem.job_id == job_id,
            StorageMigrationItem.status == "running",
        ).values(status="pending", error_msg=""))
        await db.commit()
    try:
        plugin = await _target_plugin(target_method)
    except Exception as exc:
        logger.exception("[存储迁移] 目标插件初始化失败: %s", exc)
        async with async_session_factory() as db:
            await db.execute(update(StorageMigrationJob).where(StorageMigrationJob.id == job_id).values(
                phase="failed", error_msg=str(exc),
            ))
            await db.commit()
        return
    async with async_session_factory() as db:
        item_ids = (await db.execute(select(StorageMigrationItem.id).where(
            StorageMigrationItem.job_id == job_id,
            StorageMigrationItem.status.in_(["pending", "failed"]),
        ).order_by(StorageMigrationItem.id))).scalars().all()
    # 平台任务组限制为一个迁移任务；任务内部按 10 个文件分批、每批最多 2 个并发。
    for offset in range(0, len(item_ids), 10):
        batch = item_ids[offset:offset + 10]
        semaphore = asyncio.Semaphore(2)

        async def run_one(item_id: int) -> None:
            async with semaphore:
                for _ in range(3):
                    result = await _migrate_item(plugin, target_method, int(item_id))
                    if result != "failed":
                        break

        await asyncio.gather(*(run_one(int(item_id)) for item_id in batch))
    await _refresh_job_status(job_id)


async def _refresh_job_status(job_id: int) -> dict:
    async with async_session_factory() as db:
        job = (await db.execute(select(StorageMigrationJob).where(StorageMigrationJob.id == job_id))).scalar_one_or_none()
        if not job:
            return {}
        success = int(await db.scalar(select(func.count(StorageMigrationItem.id)).where(StorageMigrationItem.job_id == job_id, StorageMigrationItem.status == "success")) or 0)
        failed = int(await db.scalar(select(func.count(StorageMigrationItem.id)).where(StorageMigrationItem.job_id == job_id, StorageMigrationItem.status.in_(["failed", "conflict"]))) or 0)
        processed = success + failed
        job.success_files, job.failed_files, job.processed_files = success, failed, processed
        job.processed_bytes = int(await db.scalar(select(func.coalesce(func.sum(StorageMigrationItem.file_size), 0)).where(
            StorageMigrationItem.job_id == job_id,
            StorageMigrationItem.status.in_(["success", "failed", "conflict"]),
        )) or 0)
        if job.phase == "scanning":
            # 扫描任务尚未写入文件总数，不能把初始的 0 误判为空任务。
            job.phase = "scanning"
        elif job.phase == "failed" and job.error_msg:
            # 目标插件初始化等任务级错误不能被进度刷新覆盖为 running。
            job.phase = "failed"
        elif job.phase == "queued" and not job.task_id:
            job.phase = "failed"
            job.error_msg = "迁移任务未成功入队"
        elif job.total_files == 0:
            job.phase = "empty"
        elif processed == job.total_files and failed == 0:
            job.phase = "finish"
        elif processed == job.total_files:
            job.phase = "partial"
        elif job.phase not in {"scanning", "awaiting", "queued"}:
            job.phase = "running"
        await db.commit()
        return {
            "id": job.id, "target_method": job.target_method, "phase": job.phase,
            "total_files": job.total_files, "total_bytes": job.total_bytes,
            "processed_files": job.processed_files, "processed_bytes": job.processed_bytes,
            "success_files": job.success_files, "failed_files": job.failed_files,
            "task_id": job.task_id, "error_msg": job.error_msg or "",
        }


async def get_job(job_id: int, *, page: int = 1, limit: int = 20) -> dict | None:
    """返回任务进度和分页失败项。"""
    result = await _refresh_job_status(job_id)
    if not result:
        return None
    async with async_session_factory() as db:
        job = (await db.execute(select(StorageMigrationJob).where(StorageMigrationJob.id == job_id))).scalar_one_or_none()
        if not job:
            return None
        failure_query = select(StorageMigrationItem).where(
            StorageMigrationItem.job_id == job_id, StorageMigrationItem.status.in_(["failed", "conflict"])
        )
        failure_total = int(await db.scalar(select(func.count()).select_from(failure_query.subquery())) or 0)
        rows = (await db.execute(failure_query.order_by(StorageMigrationItem.id)
            .offset((page - 1) * limit).limit(limit))).scalars().all()
        result["failures"] = [{
            "path": row.local_path, "object_key": row.object_key, "status": row.status,
            "attempts": row.attempts, "error": row.error_msg,
        } for row in rows]
        result["failure_page"] = page
        result["failure_limit"] = limit
        result["failure_total"] = failure_total
        return result


async def get_latest_job(target_method: str | None = None) -> dict | None:
    async with async_session_factory() as db:
        query = select(StorageMigrationJob)
        if target_method:
            query = query.where(StorageMigrationJob.target_method == target_method)
        query = query.order_by(StorageMigrationJob.id.desc()).limit(1)
        row = (await db.execute(query)).scalar_one_or_none()
        job_id = int(row.id) if row else None
    return await get_job(job_id) if job_id else None
