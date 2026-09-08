# -*- coding: utf-8 -*-
"""
App管理插件 — 开屏广告业务服务

关键设计：
- 单张广告：仅维护 id=1 单行，管理端只更新不新增
- 素材缓存标识：每次上传新图生成新 UUID 文件名，同时作为 cache_key，
  App端本地比对 cache_key 决定是否重新下载素材（实现广告缓存）
- 投放时间窗：公开API在请求时按当前时间过滤（缓存的是原始行数据，
  时间判断不缓存，避免过期广告因缓存滞后继续展示）
"""
import logging
import uuid
from datetime import datetime
from core.time_utils import china_now
from pathlib import Path

from sqlalchemy import select

from core.config_manager import ConfigManager
from plugins.addon.app_manage.models import AppManageAd
from plugins.addon.app_manage.upload_policies import get_policy_definition
from plugins.addon.app_manage.services.cache_util import (
    CACHE_KEY_AD,
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

logger = logging.getLogger(__name__)
_config_manager = ConfigManager()

# 插件标识（配置键前缀）
PLUGIN_NAME = "app_manage"
# 物理文件存储目录（项目公开静态目录 upload/app_manage/）
UPLOAD_DIR = Path(__file__).resolve().parents[4] / "upload" / PLUGIN_NAME
def _parse_dt(value):
    """缓存反序列化：字符串还原为 datetime（None 原样返回）"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _in_window(start_time, end_time, now: datetime) -> bool:
    """判断当前时间是否在投放时间窗内（NULL边界=不限）"""
    if start_time and now < start_time:
        return False
    if end_time and now > end_time:
        return False
    return True


class AdService:
    """开屏广告业务服务"""

    @staticmethod
    async def _get_row(db):
        """取 id=1 广告单行（install.sql 已幂等种入，正常必存在）"""
        return (await db.execute(
            select(AppManageAd).where(AppManageAd.id == 1)
        )).scalar_one_or_none()

    # ------------------------------------------------------------------
    # 管理端
    # ------------------------------------------------------------------
    @staticmethod
    async def get_ad(db) -> dict:
        """管理端读取广告配置（单行完整字段）"""
        row = await AdService._get_row(db)
        if not row:
            return {}
        return {
            "image_filename": row.image_filename,
            "image_url": f"/upload/{PLUGIN_NAME}/{row.image_filename}" if row.image_filename else "",
            "cache_key": row.cache_key,
            "link_url": row.link_url,
            "start_time": str(row.start_time) if row.start_time else None,
            "end_time": str(row.end_time) if row.end_time else None,
            "duration": row.duration,
            "enabled": row.enabled,
            "update_time": str(row.update_time) if row.update_time else None,
        }

    @staticmethod
    async def save_image(db, upload_file, admin_id: int) -> dict:
        """
        上传广告图并立即更新 id=1 行的素材字段，返回 {image_url, cache_key}。

        校验失败抛 ValueError（由路由层转 400）：
        - 扩展名不在白名单（jpg/jpeg/png/webp）
        - 大小超上限（app_manage.ad_img_max_size，MB）
        新图 UUID 文件名即新 cache_key；旧素材物理文件同步删除。
        """
        origin_name = upload_file.filename or ""
        policy = await get_effective_policy(get_policy_definition("ad_image"), db)
        try:
            extension = validate_filename(origin_name, policy)
        except UploadPolicyError as exc:
            raise ValueError(str(exc)) from exc
        ext = extension.lstrip(".")

        row = await AdService._get_row(db)
        if not row:
            raise ValueError("广告记录不存在，请重装插件")

        disk_name = f"{uuid.uuid4().hex}.{ext}"
        dest = UPLOAD_DIR / disk_name
        try:
            await stream_upload(upload_file, dest, policy["max_size_mb"])
        except UploadPolicyError as exc:
            raise ValueError(str(exc)) from exc

        # 写库（失败删除新文件，防孤儿）
        old_name = row.image_filename
        try:
            row.image_filename = disk_name
            row.cache_key = Path(disk_name).stem
            row.admin_id = admin_id
            await db.commit()
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        # 数据更新成功后删除旧素材物理文件
        if old_name and old_name != disk_name:
            (UPLOAD_DIR / old_name).unlink(missing_ok=True)
        await invalidate(CACHE_KEY_AD)
        return {
            "image_url": f"/upload/{PLUGIN_NAME}/{disk_name}",
            "cache_key": row.cache_key,
        }

    @staticmethod
    async def update_ad(db, data, admin_id: int) -> str:
        """
        保存广告配置（跳转/时间窗/时长/启停），返回错误码：
        - "" 成功；"not_found" 记录缺失；"no_image" 启用时素材缺失
        """
        row = await AdService._get_row(db)
        if not row:
            return "not_found"
        if data.enabled == 1 and not row.image_filename:
            return "no_image"
        row.link_url = data.link_url or ""
        row.start_time = data.start_time
        row.end_time = data.end_time
        row.duration = data.duration
        row.enabled = data.enabled
        row.admin_id = admin_id
        await db.commit()
        await invalidate(CACHE_KEY_AD)
        return ""

    # ------------------------------------------------------------------
    # 公开API：当前生效广告（走缓存，时间窗请求时判断）
    # ------------------------------------------------------------------
    @staticmethod
    async def get_active_ad(db) -> dict | None:
        """
        取当前生效广告，公开API专用。
        缓存原始行数据（{"row": dict|None}），启用/时间窗在请求时判断，
        过期广告随请求即时下线，不受缓存TTL滞后影响。
        """
        cached = await cache_get(CACHE_KEY_AD)
        if cached is not None:
            raw = cached.get("row")
        else:
            row = await AdService._get_row(db)
            raw = None
            if row and row.image_filename:
                raw = {
                    "image_filename": row.image_filename,
                    "cache_key": row.cache_key,
                    "link_url": row.link_url,
                    "start_time": row.start_time.isoformat() if row.start_time else None,
                    "end_time": row.end_time.isoformat() if row.end_time else None,
                    "duration": row.duration,
                    "enabled": row.enabled,
                }
            cfg = await _config_manager.get_plugin_config(PLUGIN_NAME, db)
            ttl = int(cfg.get("cache_ttl") or DEFAULT_CACHE_TTL)
            await cache_set(CACHE_KEY_AD, {"row": raw}, ttl)

        # 启用与时间窗过滤在请求时执行（实现过期自动下线）
        if not raw or raw.get("enabled") != 1:
            return None
        now = china_now()
        if not _in_window(_parse_dt(raw.get("start_time")), _parse_dt(raw.get("end_time")), now):
            return None
        return {
            "image_url": f"/upload/{PLUGIN_NAME}/{raw['image_filename']}",
            "cache_key": raw.get("cache_key", ""),
            "link_url": raw.get("link_url", ""),
            "duration": raw.get("duration", 3),
        }
