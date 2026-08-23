# -*- coding: utf-8 -*-
"""
农业知识库插件 — 知识条目业务服务

职责：知识条目的增删改查、浏览计数原子自增与序列化。
典型图片以 JSON 数组（URL 列表）持久化在 images 字段；读取时解析回列表。
管理端 / 农户端 / MCP 三处列表共用 build_query 过滤逻辑，规则不发散。
"""
import json
import logging

from sqlalchemy import func, select, update

from plugins.addon.knowledge.models import KnowledgeEntry
from plugins.addon.knowledge.services.category_service import CategoryService

logger = logging.getLogger(__name__)


def _dump_images(images: list) -> str:
    """图片 URL 列表 -> JSON 字符串（空列表存空串，便于兼容旧数据）"""
    if not images:
        return ""
    return json.dumps([str(u) for u in images if str(u).strip()], ensure_ascii=False)


def _load_images(raw: str) -> list:
    """images 字段 JSON 字符串 -> URL 列表（脏数据/空值兜底空列表）"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(u) for u in data] if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


class EntryService:
    """知识条目业务服务"""

    @staticmethod
    def _apply_filters(query, keyword: str, category_ids: list, crop: str):
        """构造列表过滤条件

        category_ids：分类 ID 集合（大类已展开为自身 + 全部子类），空列表表示不限分类。
        keyword/crop 用 autoescape 转义 LIKE 通配符 %/_，避免用户输入干扰匹配。
        """
        if category_ids:
            query = query.where(KnowledgeEntry.category_id.in_(category_ids))
        if crop:
            query = query.where(KnowledgeEntry.crop.contains(crop, autoescape=True))
        if keyword:
            query = query.where(
                KnowledgeEntry.title.contains(keyword, autoescape=True)
                | KnowledgeEntry.summary.contains(keyword, autoescape=True)
            )
        return query

    @staticmethod
    async def get_entry(db, entry_id: int):
        """根据 ID 获取条目，不存在返回 None"""
        return (await db.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id)
        )).scalar_one_or_none()

    @staticmethod
    async def list_entries(
        db, keyword: str = "", category_id: int = 0, crop: str = "",
        page: int = 1, limit: int = 10,
    ) -> dict:
        """条目分页列表 — 支持关键词/分类/作物过滤，返回摘要字段

        分类过滤大类聚合子类：category_id 指向大类时，展开为大类自身 +
        其全部子类 ID，命中挂在大类或任一子类下的条目；指向子类时精确匹配。
        """
        category_ids = (
            await CategoryService.expand_category_ids(db, category_id)
            if category_id else []
        )
        query = EntryService._apply_filters(
            select(KnowledgeEntry), keyword, category_ids, crop
        )
        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            query.order_by(KnowledgeEntry.sort_order.asc(), KnowledgeEntry.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()
        return {
            "total": total, "page": page, "limit": limit,
            "list": [EntryService.to_brief(r) for r in rows],
        }

    @staticmethod
    async def create_entry(db, data: dict, admin_id: int) -> int:
        """新建知识条目，返回新记录 ID"""
        row = KnowledgeEntry(
            title=data["title"],
            category_id=data["category_id"],
            crop=data.get("crop", ""),
            summary=data.get("summary", ""),
            cause=data.get("cause", ""),
            solution=data.get("solution", ""),
            images=_dump_images(data.get("images", [])),
            sort_order=data.get("sort_order", 0),
            admin_id=admin_id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id

    @staticmethod
    async def batch_create(
        db, category_id: int, titles: list, crop: str, admin_id: int,
    ) -> list:
        """批量新建条目（共享分类/作物，仅录标题），返回新增 id 列表

        空标题过滤已在 schema 层完成；此处逐标题构造记录后单次 commit。
        摘要/原因/方案/图片置空，后续在后台编辑补充。
        """
        rows = [
            KnowledgeEntry(
                title=t, category_id=category_id, crop=crop,
                summary="", cause="", solution="", images="",
                sort_order=0, admin_id=admin_id,
            )
            for t in titles
        ]
        db.add_all(rows)
        await db.commit()
        for row in rows:
            await db.refresh(row)
        return [row.id for row in rows]

    @staticmethod
    async def update_entry(db, entry_id: int, data: dict, admin_id: int) -> bool:
        """编辑知识条目，成功返回 True，记录不存在返回 False"""
        row = await EntryService.get_entry(db, entry_id)
        if not row:
            return False
        row.title = data["title"]
        row.category_id = data["category_id"]
        row.crop = data.get("crop", "")
        row.summary = data.get("summary", "")
        row.cause = data.get("cause", "")
        row.solution = data.get("solution", "")
        row.images = _dump_images(data.get("images", []))
        row.sort_order = data.get("sort_order", row.sort_order)
        row.admin_id = admin_id
        await db.commit()
        return True

    @staticmethod
    async def delete_entry(db, entry_id: int) -> bool:
        """删除知识条目，成功返回 True，记录不存在返回 False"""
        row = await EntryService.get_entry(db, entry_id)
        if not row:
            return False
        await db.delete(row)
        await db.commit()
        return True

    @staticmethod
    async def increment_view(db, entry_id: int) -> None:
        """浏览计数 SQL 端原子自增（禁止 ORM 读改写，避免并发丢失更新）"""
        await db.execute(
            update(KnowledgeEntry)
            .where(KnowledgeEntry.id == entry_id)
            .values(view_count=KnowledgeEntry.view_count + 1)
        )
        await db.commit()

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    @staticmethod
    def to_brief(row) -> dict:
        """列表序列化（摘要字段，不含长文本 cause/solution）"""
        return {
            "id": row.id,
            "title": row.title,
            "category_id": row.category_id,
            "crop": row.crop,
            "summary": row.summary,
            "view_count": row.view_count,
            "images": _load_images(row.images),
            "create_time": str(row.create_time) if row.create_time else None,
            "update_time": str(row.update_time) if row.update_time else None,
        }

    @staticmethod
    def to_detail(row) -> dict:
        """详情序列化（完整字段，含 images 列表 / cause / solution）"""
        data = EntryService.to_brief(row)
        data["cause"] = row.cause or ""
        data["solution"] = row.solution or ""
        data["sort_order"] = row.sort_order
        return data
