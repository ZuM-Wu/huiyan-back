# -*- coding: utf-8 -*-
"""
农业知识库插件 — 勘误业务服务

职责：农户提交勘误、管理员分页查看与处理（采纳/驳回）。
处理时写入 admin_note / admin_id / handle_time，并将 status 置为 1 或 2。
"""
import logging
from core.time_utils import china_now

from sqlalchemy import func, select

from plugins.addon.knowledge.models import KnowledgeCorrection, KnowledgeEntry

logger = logging.getLogger(__name__)


class CorrectionService:
    """勘误业务服务"""

    @staticmethod
    async def get_correction(db, correction_id: int):
        """根据 ID 获取勘误，不存在返回 None"""
        return (await db.execute(
            select(KnowledgeCorrection).where(KnowledgeCorrection.id == correction_id)
        )).scalar_one_or_none()

    @staticmethod
    async def submit(db, knowledge_id: int, farmer_id: int, content: str) -> int:
        """农户提交勘误，返回新记录 ID"""
        row = KnowledgeCorrection(
            knowledge_id=knowledge_id,
            farmer_id=farmer_id,
            content=content,
            status=0,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id

    @staticmethod
    async def list_corrections(db, status=None, page: int = 1, limit: int = 10) -> dict:
        """
        勘误分页列表 — 支持按状态过滤，附带关联知识条目标题。

        标题一次批量查询（IN 集合），无 N+1。
        """
        query = select(KnowledgeCorrection)
        if status is not None:
            query = query.where(KnowledgeCorrection.status == status)
        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            query.order_by(KnowledgeCorrection.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        title_map: dict = {}
        if rows:
            pairs = (await db.execute(
                select(KnowledgeEntry.id, KnowledgeEntry.title)
                .where(KnowledgeEntry.id.in_([r.knowledge_id for r in rows]))
            )).all()
            title_map = {kid: title for kid, title in pairs}

        return {
            "total": total, "page": page, "limit": limit,
            "list": [
                {
                    "id": r.id,
                    "knowledge_id": r.knowledge_id,
                    "knowledge_title": title_map.get(r.knowledge_id, "（条目已删除）"),
                    "farmer_id": r.farmer_id,
                    "content": r.content,
                    "status": r.status,
                    "admin_note": r.admin_note,
                    "create_time": str(r.create_time) if r.create_time else None,
                    "handle_time": str(r.handle_time) if r.handle_time else None,
                }
                for r in rows
            ],
        }

    @staticmethod
    async def handle(db, correction_id: int, status: int, admin_note: str, admin_id: int) -> str:
        """
        处理勘误（采纳=1 / 驳回=2）。返回错误码，空串表示成功：
        - "not_found"：勘误不存在
        - "handled"：已处理过（非待处理状态）
        """
        row = await CorrectionService.get_correction(db, correction_id)
        if not row:
            return "not_found"
        if row.status != 0:
            return "handled"
        row.status = status
        row.admin_note = admin_note or ""
        row.admin_id = admin_id
        row.handle_time = china_now()
        await db.commit()
        return ""
