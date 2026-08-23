# -*- coding: utf-8 -*-
"""
App管理插件 — App公告业务服务

关键设计：
- 独立公告表，与核心 notice 模块完全隔离（不 import 核心业务模块）
- 公开API缓存 enabled=1 的原始行列表，生效时间窗在请求时过滤，
  过期公告随请求即时下线，不受缓存TTL滞后影响
- 公开API仅暴露白名单字段，最多返回 20 条
"""
import logging
from datetime import datetime

from sqlalchemy import delete, func, select

from core.config_manager import ConfigManager
from plugins.addon.app_manage.models import AppManageNotice
from plugins.addon.app_manage.services.ad_service import _in_window, _parse_dt
from plugins.addon.app_manage.services.cache_util import (
    CACHE_KEY_NOTICES,
    DEFAULT_CACHE_TTL,
    cache_get,
    cache_set,
    invalidate,
)

logger = logging.getLogger(__name__)

# 插件标识（配置键前缀）
PLUGIN_NAME = "app_manage"
# 公开API单次最多返回公告条数
PUBLIC_MAX_NOTICES = 20

_config_manager = ConfigManager()


class NoticeService:
    """App公告业务服务"""

    # ------------------------------------------------------------------
    # 管理端 CRUD
    # ------------------------------------------------------------------
    @staticmethod
    async def list_notices(db, page: int, limit: int) -> dict:
        """公告分页列表（sort_order 升序，同序按创建时间倒序）"""
        total = (await db.execute(
            select(func.count(AppManageNotice.id))
        )).scalar() or 0
        rows = (await db.execute(
            select(AppManageNotice)
            .order_by(AppManageNotice.sort_order.asc(), AppManageNotice.create_time.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()
        return {"list": [NoticeService.to_dict(r) for r in rows], "total": total}

    @staticmethod
    async def create_notice(db, data, admin_id: int) -> int:
        """新建公告，返回新记录 ID，成功后失效公开缓存"""
        row = AppManageNotice(
            title=data.title,
            content=data.content or "",
            is_popup=data.is_popup,
            start_time=data.start_time,
            end_time=data.end_time,
            enabled=data.enabled,
            sort_order=data.sort_order,
            admin_id=admin_id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        await invalidate(CACHE_KEY_NOTICES)
        return row.id

    @staticmethod
    async def update_notice(db, notice_id: int, data, admin_id: int) -> bool:
        """编辑公告，成功后失效公开缓存；记录不存在返回 False"""
        row = (await db.execute(
            select(AppManageNotice).where(AppManageNotice.id == notice_id)
        )).scalar_one_or_none()
        if not row:
            return False
        row.title = data.title
        row.content = data.content or ""
        row.is_popup = data.is_popup
        row.start_time = data.start_time
        row.end_time = data.end_time
        row.enabled = data.enabled
        row.sort_order = data.sort_order
        row.admin_id = admin_id
        await db.commit()
        await invalidate(CACHE_KEY_NOTICES)
        return True

    @staticmethod
    async def delete_notice(db, notice_id: int) -> bool:
        """删除公告，成功后失效公开缓存；记录不存在返回 False"""
        exists = (await db.execute(
            select(AppManageNotice.id).where(AppManageNotice.id == notice_id)
        )).scalar_one_or_none()
        if not exists:
            return False
        await db.execute(
            delete(AppManageNotice).where(AppManageNotice.id == notice_id)
        )
        await db.commit()
        await invalidate(CACHE_KEY_NOTICES)
        return True

    # ------------------------------------------------------------------
    # 公开API：生效公告列表（走缓存，时间窗请求时过滤）
    # ------------------------------------------------------------------
    @staticmethod
    async def list_active(db) -> list:
        """
        取当前生效公告列表，公开API专用。
        缓存 enabled=1 的原始行（{"rows": [...]}），时间窗在请求时过滤，
        过期公告随请求即时下线；限 PUBLIC_MAX_NOTICES 条。
        """
        cached = await cache_get(CACHE_KEY_NOTICES)
        if cached is not None:
            rows = cached.get("rows") or []
        else:
            db_rows = (await db.execute(
                select(AppManageNotice)
                .where(AppManageNotice.enabled == 1)
                .order_by(AppManageNotice.sort_order.asc(), AppManageNotice.create_time.desc())
            )).scalars().all()
            rows = [
                {
                    "id": r.id,
                    "title": r.title,
                    "content": r.content or "",
                    "is_popup": r.is_popup,
                    "start_time": r.start_time.isoformat() if r.start_time else None,
                    "end_time": r.end_time.isoformat() if r.end_time else None,
                }
                for r in db_rows
            ]
            cfg = await _config_manager.get_plugin_config(PLUGIN_NAME, db)
            ttl = int(cfg.get("cache_ttl") or DEFAULT_CACHE_TTL)
            await cache_set(CACHE_KEY_NOTICES, {"rows": rows}, ttl)

        # 生效时间窗在请求时过滤（实现过期自动下线）
        now = datetime.now()
        result = []
        for raw in rows:
            if not _in_window(_parse_dt(raw.get("start_time")), _parse_dt(raw.get("end_time")), now):
                continue
            result.append({
                "id": raw["id"],
                "title": raw["title"],
                "content": raw["content"],
                "is_popup": raw["is_popup"],
                "start_time": raw["start_time"],
            })
            if len(result) >= PUBLIC_MAX_NOTICES:
                break
        return result

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    @staticmethod
    def to_dict(row) -> dict:
        """管理端序列化（完整字段）"""
        return {
            "id": row.id,
            "title": row.title,
            "content": row.content or "",
            "is_popup": row.is_popup,
            "start_time": str(row.start_time) if row.start_time else None,
            "end_time": str(row.end_time) if row.end_time else None,
            "enabled": row.enabled,
            "sort_order": row.sort_order,
            "create_time": str(row.create_time) if row.create_time else None,
            "update_time": str(row.update_time) if row.update_time else None,
        }
