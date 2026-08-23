# -*- coding: utf-8 -*-
"""
Hello World 插件业务逻辑层

演示 services/ 目录的职责：将数据库读写从路由层剥离，保持路由函数简洁。
路由层负责鉴权与参数校验，服务层负责业务处理，模型层负责数据结构。
"""
import logging

from sqlalchemy import func, select

from plugins.addon.hello_world.models import HelloMessage

logger = logging.getLogger(__name__)


class MessageService:
    """留言业务服务 — 提供留言的增删改查"""

    @staticmethod
    async def list_messages(db, keyword: str, page: int, limit: int) -> dict:
        """分页查询留言，支持标题/内容关键词搜索"""
        query = select(HelloMessage)
        if keyword:
            query = query.where(
                HelloMessage.title.contains(keyword) | HelloMessage.content.contains(keyword)
            )

        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0

        result = await db.execute(
            query.order_by(HelloMessage.id.desc()).offset((page - 1) * limit).limit(limit)
        )
        rows = result.scalars().all()
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "list": [MessageService.to_dict(r) for r in rows],
        }

    @staticmethod
    async def list_public_messages(db, page: int, limit: int) -> dict:
        """分页查询公开留言（status=1）— 农户端使用"""
        query = select(HelloMessage).where(HelloMessage.status == 1)

        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0

        result = await db.execute(
            query.order_by(HelloMessage.id.desc()).offset((page - 1) * limit).limit(limit)
        )
        rows = result.scalars().all()
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "list": [MessageService.to_dict(r) for r in rows],
        }

    @staticmethod
    async def create_message(db, title: str, content: str, author: str) -> int:
        """新增留言，返回新记录 ID"""
        msg = HelloMessage(title=title, content=content, author=author, status=1)
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return msg.id

    @staticmethod
    async def get_message(db, msg_id: int):
        """根据 ID 获取留言，不存在返回 None"""
        return (await db.execute(
            select(HelloMessage).where(HelloMessage.id == msg_id)
        )).scalar_one_or_none()

    @staticmethod
    async def update_message(db, msg_id: int, data: dict) -> bool:
        """更新留言，成功返回 True，记录不存在返回 False"""
        msg = await MessageService.get_message(db, msg_id)
        if not msg:
            return False
        for key, value in data.items():
            setattr(msg, key, value)
        await db.commit()
        return True

    @staticmethod
    async def toggle_status(db, msg_id: int, status: int) -> bool:
        """切换留言显示状态（0=隐藏, 1=显示），成功返回 True，记录不存在返回 False"""
        msg = await MessageService.get_message(db, msg_id)
        if not msg:
            return False
        msg.status = status
        await db.commit()
        return True

    @staticmethod
    async def delete_message(db, msg_id: int) -> bool:
        """删除留言，成功返回 True，记录不存在返回 False"""
        msg = await MessageService.get_message(db, msg_id)
        if not msg:
            return False
        await db.delete(msg)
        await db.commit()
        return True

    @staticmethod
    def to_dict(row) -> dict:
        """将 ORM 对象序列化为字典"""
        return {
            "id": row.id,
            "title": row.title,
            "content": row.content,
            "author": row.author,
            "status": row.status,
            "create_time": str(row.create_time) if row.create_time else None,
        }
