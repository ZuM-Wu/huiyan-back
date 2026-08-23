# -*- coding: utf-8 -*-
"""
农业知识库插件 — 分类业务服务

职责：二级分类（大类>子类）的增删改查与树形组装。
保护性规则收敛在本服务内：删除分类时若存在子类或被知识条目引用则拒绝。
"""
import logging

from sqlalchemy import func, select

from plugins.addon.knowledge.models import KnowledgeCategory, KnowledgeEntry

logger = logging.getLogger(__name__)


class CategoryService:
    """分类业务服务"""

    @staticmethod
    async def get_category(db, category_id: int):
        """根据 ID 获取分类，不存在返回 None"""
        return (await db.execute(
            select(KnowledgeCategory).where(KnowledgeCategory.id == category_id)
        )).scalar_one_or_none()

    @staticmethod
    async def list_tree(db, only_enabled: bool = False) -> list:
        """
        分类树（大类 > 子类两级）。

        固定 1 条 SQL 取全量分类，Python 端按 parent_id 组装，无 N+1。
        only_enabled=True 时仅返回 status=1 的启用分类（农户端用）。
        排序：sort_order 升序，其次 id 升序。
        """
        query = select(KnowledgeCategory)
        if only_enabled:
            query = query.where(KnowledgeCategory.status == 1)
        rows = (await db.execute(
            query.order_by(KnowledgeCategory.sort_order.asc(), KnowledgeCategory.id.asc())
        )).scalars().all()

        children_map: dict = {}
        roots = []
        for c in rows:
            if c.parent_id == 0:
                roots.append(c)
            else:
                children_map.setdefault(c.parent_id, []).append(c)

        return [
            {
                "id": r.id, "name": r.name, "parent_id": 0,
                "sort_order": r.sort_order, "status": r.status,
                "children": [
                    {
                        "id": ch.id, "name": ch.name, "parent_id": ch.parent_id,
                        "sort_order": ch.sort_order, "status": ch.status,
                    }
                    for ch in children_map.get(r.id, [])
                ],
            }
            for r in roots
        ]

    @staticmethod
    async def create_category(db, name: str, parent_id: int, sort_order: int) -> int:
        """新建分类，返回新记录 ID"""
        category = KnowledgeCategory(name=name, parent_id=parent_id, sort_order=sort_order)
        db.add(category)
        await db.commit()
        await db.refresh(category)
        return category.id

    @staticmethod
    async def update_category(db, category_id: int, name: str, sort_order: int, status: int) -> bool:
        """编辑分类，成功返回 True，记录不存在返回 False"""
        category = await CategoryService.get_category(db, category_id)
        if not category:
            return False
        category.name = name
        category.sort_order = sort_order
        category.status = status
        await db.commit()
        return True

    @staticmethod
    async def delete_category(db, category_id: int) -> str:
        """
        删除分类。返回错误码字符串，空串表示成功：
        - "not_found"：分类不存在
        - "has_children"：存在子类，需先删除子类
        - "in_use"：被知识条目引用，禁止删除
        """
        category = await CategoryService.get_category(db, category_id)
        if not category:
            return "not_found"
        child_count = (await db.execute(
            select(func.count()).select_from(KnowledgeCategory)
            .where(KnowledgeCategory.parent_id == category_id)
        )).scalar() or 0
        if child_count > 0:
            return "has_children"
        ref_count = (await db.execute(
            select(func.count()).select_from(KnowledgeEntry)
            .where(KnowledgeEntry.category_id == category_id)
        )).scalar() or 0
        if ref_count > 0:
            return "in_use"
        await db.delete(category)
        await db.commit()
        return ""

    @staticmethod
    async def expand_category_ids(db, category_id: int) -> list:
        """
        展开分类过滤 ID 集合，用于列表大类聚合子类：
        - category_id 指向大类（parent_id=0）：返回 [自身, *全部子类ID]
        - category_id 指向子类或不存在：返回 [自身]（精确匹配）
        """
        category = await CategoryService.get_category(db, category_id)
        if not category or category.parent_id != 0:
            return [category_id]
        child_ids = (await db.execute(
            select(KnowledgeCategory.id).where(KnowledgeCategory.parent_id == category_id)
        )).scalars().all()
        return [category_id, *child_ids]

    @staticmethod
    async def name_map(db) -> dict:
        """分类 ID -> 名称映射（列表/详情组装分类名用，一次全量查询）"""
        rows = (await db.execute(
            select(KnowledgeCategory.id, KnowledgeCategory.name)
        )).all()
        return {cid: name for cid, name in rows}
