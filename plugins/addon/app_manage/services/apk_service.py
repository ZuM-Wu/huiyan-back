# -*- coding: utf-8 -*-
"""
App管理插件 — APK版本业务服务（安全与性能核心）

关键设计：
- 流式上传：1MB 分块边写边计数边算 MD5，超限立即中断并删除半成品，不整读内存
- 物理存储：项目公开 upload/app_manage/ 目录（/upload 无鉴权静态挂载范围内），
  文件名 UUID 重命名，App端经 /upload/app_manage/{uuid}.apk 免登录直接下载
- 最新版本查询走缓存（值内嵌时间戳自判过期），写操作后主动失效
"""
import hashlib
import logging
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select

from core.config_manager import ConfigManager
from plugins.addon.app_manage.models import AppManageVersion
from plugins.addon.app_manage.upload_policies import get_policy_definition
from plugins.addon.app_manage.services.cache_util import (
    CACHE_KEY_VERSION,
    DEFAULT_CACHE_TTL,
    cache_get,
    cache_set,
    invalidate,
)
from services.upload_policy import (
    UploadPolicyError,
    get_effective_policy,
    stream_upload,
    validate_filename,
)
from core.oss_service import oss_service

logger = logging.getLogger(__name__)
_config_manager = ConfigManager()

# 插件标识（配置键前缀）
PLUGIN_NAME = "app_manage"
# 物理文件存储目录（项目公开静态目录 upload/app_manage/）
UPLOAD_DIR = Path(__file__).resolve().parents[4] / "upload" / PLUGIN_NAME
class ApkService:
    """APK版本业务服务"""

    # ------------------------------------------------------------------
    # 上传发版（流式写盘）
    # ------------------------------------------------------------------
    @staticmethod
    async def save_apk(
        db, *, upload_file, version_name: str, version_code: int, build_number: str,
        changelog: str, update_policy: int, status: int, admin_id: int,
    ) -> int:
        """
        保存上传APK并写入版本记录，返回新版本记录 ID。

        校验失败抛 ValueError（由路由层转 400）：
        - 扩展名非 .apk
        - 版本号 version_code 已存在
        - 文件大小超上限（app_manage.apk_max_size，MB）——
          流式分块边写边计数，超限立即中断并删除半成品
        INSERT 失败时删除已落盘文件，防止孤儿文件。
        """
        # 版本号唯一性校验（前置于写盘，避免白写大文件）
        exists = (await db.execute(
            select(AppManageVersion.id).where(AppManageVersion.version_code == version_code)
        )).scalar_one_or_none()
        if exists:
            raise ValueError(f"版本号 {version_code} 已存在，请更换")

        origin_name = upload_file.filename or ""
        policy = await get_effective_policy(get_policy_definition("apk"), db)
        try:
            validate_filename(origin_name, policy)
        except UploadPolicyError as exc:
            raise ValueError(str(exc)) from exc

        # UUID 重命名落盘，避免文件名冲突与路径注入
        disk_name = f"{uuid.uuid4().hex}.apk"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / disk_name

        md5 = hashlib.md5()
        try:
            size = await stream_upload(
                upload_file, dest, policy["max_size_mb"], on_chunk=md5.update,
            )
        except UploadPolicyError as exc:
            raise ValueError(str(exc)) from exc

        # 写库（失败删除已落盘文件，防孤儿）
        try:
            row = AppManageVersion(
                version_name=version_name,
                version_code=version_code,
                build_number=build_number or "",
                apk_filename=disk_name,
                apk_size=size,
                apk_md5=md5.hexdigest(),
                changelog=changelog or "",
                update_policy=update_policy,
                status=status,
                admin_id=admin_id,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        try:
            await oss_service.upload(
                save_path=str(dest), save_name=disk_name, original_name=origin_name,
                ext=".apk", file_size=size, admin_id=admin_id, source="app_manage",
            )
        except Exception as exc:
            logger.warning("[app_manage] APK 对象存储上传失败，保留本地文件: %s", exc)
        # 写操作成功后失效最新版本缓存
        await invalidate(CACHE_KEY_VERSION)
        return row.id

    # ------------------------------------------------------------------
    # 管理端查询与维护
    # ------------------------------------------------------------------
    @staticmethod
    async def list_versions(db, page: int, limit: int) -> dict:
        """版本分页列表（version_code 倒序，最新在前）"""
        total = (await db.execute(
            select(func.count(AppManageVersion.id))
        )).scalar() or 0
        rows = (await db.execute(
            select(AppManageVersion)
            .order_by(AppManageVersion.version_code.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()
        return {"list": [ApkService.to_dict(r) for r in rows], "total": total}

    @staticmethod
    async def update_version(
        db, version_id: int, build_number: str, changelog: str,
        update_policy: int, status: int,
    ) -> bool:
        """编辑版本元信息（不更换物理APK），成功后失效缓存"""
        row = (await db.execute(
            select(AppManageVersion).where(AppManageVersion.id == version_id)
        )).scalar_one_or_none()
        if not row:
            return False
        row.build_number = build_number or ""
        row.changelog = changelog or ""
        row.update_policy = update_policy
        row.status = status
        await db.commit()
        await invalidate(CACHE_KEY_VERSION)
        return True

    @staticmethod
    async def delete_version(db, version_id: int) -> bool:
        """删除版本记录并同步删除物理APK文件（防孤儿文件堆积）"""
        row = (await db.execute(
            select(AppManageVersion).where(AppManageVersion.id == version_id)
        )).scalar_one_or_none()
        if not row:
            return False
        disk_name = row.apk_filename
        await db.execute(
            delete(AppManageVersion).where(AppManageVersion.id == version_id)
        )
        await db.commit()
        # 数据删除成功后再删物理文件
        if disk_name:
            (UPLOAD_DIR / disk_name).unlink(missing_ok=True)
        await invalidate(CACHE_KEY_VERSION)
        return True

    # ------------------------------------------------------------------
    # 公开API：最新发布版本（走缓存）
    # ------------------------------------------------------------------
    @staticmethod
    async def get_latest_published(db) -> dict | None:
        """
        取最新发布版本（status=1 中 version_code 最大者），公开API专用。
        缓存包装为 {"row": dict|None}，区分「无发布版本」与「缓存未命中」。
        """
        cached = await cache_get(CACHE_KEY_VERSION)
        if cached is not None:
            return cached.get("row")

        row = (await db.execute(
            select(AppManageVersion)
            .where(AppManageVersion.status == 1)
            .order_by(AppManageVersion.version_code.desc())
            .limit(1)
        )).scalar_one_or_none()
        data = ApkService.to_public_dict(row) if row else None

        cfg = await _config_manager.get_plugin_config(PLUGIN_NAME, db)
        ttl = int(cfg.get("cache_ttl") or DEFAULT_CACHE_TTL)
        await cache_set(CACHE_KEY_VERSION, {"row": data}, ttl)
        return data

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    @staticmethod
    def to_dict(row) -> dict:
        """管理端序列化（含磁盘文件名与操作管理员）"""
        return {
            "id": row.id,
            "version_name": row.version_name,
            "version_code": row.version_code,
            "build_number": row.build_number or "",
            "apk_filename": row.apk_filename,
            "apk_size": row.apk_size,
            "apk_md5": row.apk_md5,
            "changelog": row.changelog or "",
            "update_policy": row.update_policy,
            "status": row.status,
            "download_url": oss_service.stable_url(f"{PLUGIN_NAME}/{row.apk_filename}"),
            "create_time": str(row.create_time) if row.create_time else None,
            "update_time": str(row.update_time) if row.update_time else None,
        }

    @staticmethod
    def to_public_dict(row) -> dict:
        """公开API序列化（仅暴露App端需要的白名单字段）"""
        return {
            "version_name": row.version_name,
            "version_code": row.version_code,
            "build_number": row.build_number or "",
            "changelog": row.changelog or "",
            "update_policy": row.update_policy,
            # 兼容保留的派生字段(deprecated)：仅强制策略视为旧版强制更新
            "force_update": 1 if row.update_policy == 2 else 0,
            "apk_size": row.apk_size,
            "apk_md5": row.apk_md5,
            "download_url": oss_service.stable_url(f"{PLUGIN_NAME}/{row.apk_filename}"),
        }
