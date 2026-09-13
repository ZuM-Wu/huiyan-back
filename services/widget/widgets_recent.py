# -*- coding: utf-8 -*-
"""插件最近业务记录控制台挂件。"""
import logging
import re
from sqlalchemy import select
from core.db.base import async_session_factory
from services.widget.widget_engine import BaseWidget

logger = logging.getLogger(__name__)
RECENT_LIMIT = 5

class RecentRecognitionWidget(BaseWidget):
    """展示最近完成的智能识别记录。"""
    name = "yolo_recent_records"
    title = "最近检测记录"
    columns = 2
    weight = 55
    widget_type = "list"

    async def get_data(self) -> dict:
        from plugins.addon.yolo_model_manager.models import RecognitionRecord
        async with async_session_factory() as db:
            rows = (await db.execute(select(RecognitionRecord).order_by(
                RecognitionRecord.recognized_at.desc(), RecognitionRecord.id.desc()
            ).limit(RECENT_LIMIT))).scalars().all()
        return {"icon": "visual-recognition", "logs": [
            {"id": int(row.id), "type": "识别",
             "description": f"{row.plot_name or '未命名地块'} · {row.model_name or '未命名模型'} · {int(row.detection_count or 0)}个目标",
             "user_name": row.area_name or "未命名产区",
             "create_time": row.recognized_at.strftime("%Y-%m-%d %H:%M:%S") if row.recognized_at else "未知时间",
             "url": f"/admin/plugin/yolo_model_manager/yolo_model_manager?record_id={row.id}"}
            for row in rows
        ]}

def register_recent_recognition_widget(engine):
    engine.register(RecentRecognitionWidget())

class RecentKnowledgeWidget(BaseWidget):
    """展示最近新增的知识库条目。"""
    name = "knowledge_recent_records"
    title = "最近新增知识库"
    columns = 2
    weight = 56
    widget_type = "list"

    async def get_data(self) -> dict:
        from plugins.addon.knowledge.models import KnowledgeEntry
        async with async_session_factory() as db:
            rows = (await db.execute(select(KnowledgeEntry).order_by(
                KnowledgeEntry.create_time.desc(), KnowledgeEntry.id.desc()
            ).limit(RECENT_LIMIT))).scalars().all()
        return {"icon": "book-1", "logs": [
            {"id": int(row.id), "type": "知识",
             "description": _knowledge_description(row),
             "user_name": row.crop or (f"分类 {row.category_id}" if row.category_id else "未设置作物"),
             "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else "未知时间",
             "url": f"/admin/plugin/knowledge/knowledge?entry_id={row.id}"}
            for row in rows
        ]}

def _first_text(value: object) -> str:
    """归一化知识正文并取首段，避免长文本破坏控制台列表布局。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.split("。", 1)[0].strip() if text else ""


def _knowledge_description(row) -> str:
    """组合知识标题与疾病摘要，按摘要、原因、方案的优先级取值。"""
    title = str(row.title or "未命名知识条目").strip()
    detail = _first_text(row.summary) or _first_text(row.cause) or _first_text(row.solution) or "暂无诊断摘要"
    return f"{title} · {detail}"


def register_recent_knowledge_widget(engine):
    engine.register(RecentKnowledgeWidget())
