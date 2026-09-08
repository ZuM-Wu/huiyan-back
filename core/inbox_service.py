"""
站内信服务层

封装对 core.db.notice.InboxMessage 的数据库查询逻辑，供 api 层调用。
涵盖农户端和管理端两套读写场景，所有函数返回纯 dict/list/int/bool。
"""
import logging
from core.time_utils import china_now
from typing import Any, Sequence, cast

from sqlalchemy import select, func, and_, delete, update
from sqlalchemy.engine import CursorResult

from core.db.base import async_session_factory
from core.db.admin import Admin
from core.db.farmer import Farmer
from core.db.notice import InboxMessage

logger = logging.getLogger(__name__)


# ========== 内部辅助函数 ==========

def _fmt_dt(dt) -> str:
    """格式化 datetime 为字符串，None 返回空串"""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _message_to_dict(m: InboxMessage, include_receiver: bool = True) -> dict:
    """InboxMessage ORM 实例转 dict

    :param m:                ORM 实例
    :param include_receiver: 是否包含 receiver_type / receiver_id / sender_id 字段
                             （管理端需要，农户端不需要）
    """
    data = {
        "id": m.id,
        "title": m.title,
        "content": m.content or "",
        "is_read": m.is_read,
        "priority": m.priority,
        "create_time": _fmt_dt(m.create_time),
        "read_time": _fmt_dt(m.read_time),
        "extra": m.extra or {},
    }
    if include_receiver:
        data["sender_id"] = m.sender_id
        data["receiver_type"] = m.receiver_type
        data["receiver_id"] = m.receiver_id
    return data


async def _add_receiver_names(db, messages: Sequence[InboxMessage], data: list[dict]) -> list[dict]:
    """批量补充接收者用户名，避免列表按行触发查询。"""
    farmer_ids = {
        m.receiver_id for m in messages if m.receiver_type == "farmer"
    }
    admin_ids = {
        m.receiver_id for m in messages if m.receiver_type == "admin"
    }
    farmer_names = {}
    admin_names = {}
    if farmer_ids:
        rows = (await db.execute(
            select(Farmer.id, Farmer.username).where(Farmer.id.in_(farmer_ids))
        )).all()
        farmer_names = {row[0]: row[1] for row in rows}
    if admin_ids:
        rows = (await db.execute(
            select(Admin.id, Admin.username).where(Admin.id.in_(admin_ids))
        )).all()
        admin_names = {row[0]: row[1] for row in rows}
    for message, item in zip(messages, data):
        names = farmer_names if message.receiver_type == "farmer" else admin_names
        item["receiver_name"] = names.get(message.receiver_id)
    return data


# ========== 农户端 / 通用操作 ==========

async def list_inbox_messages(
    receiver_id: int,
    receiver_type: str,
    page: int = 1,
    limit: int = 20,
    is_read: int | None = None,
) -> dict:
    """站内信列表（按接收者隔离）

    :param receiver_id:   接收者 ID
    :param receiver_type: 接收者类型 (farmer/admin)
    :param page:          页码
    :param limit:         每页条数
    :param is_read:       已读筛选: None=全部 0=未读 1=已读
    :return: {"total": int, "unread_total": int, "page": int, "limit": int, "list": [dict]}
    """
    async with async_session_factory() as db:
        # 基础查询：当前接收者的全部消息
        base_cond = and_(
            InboxMessage.receiver_id == receiver_id,
            InboxMessage.receiver_type == receiver_type,
        )

        # 列表查询（可叠加 is_read 筛选）
        q = select(InboxMessage).where(base_cond)
        if is_read is not None:
            q = q.where(InboxMessage.is_read == is_read)

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0

        # 未读总数始终基于完整收件箱，不受 is_read 筛选影响
        unread_total = (await db.execute(
            select(func.count()).select_from(InboxMessage).where(
                base_cond, InboxMessage.is_read == 0,
            )
        )).scalar() or 0

        rows = (await db.execute(
            q.order_by(InboxMessage.create_time.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        return {
            "total": total,
            "unread_total": unread_total,
            "page": page,
            "limit": limit,
            "list": [_message_to_dict(m, include_receiver=False) for m in rows],
        }


async def get_unread_count(receiver_id: int, receiver_type: str) -> int:
    """获取未读站内信数量

    :param receiver_id:   接收者 ID
    :param receiver_type: 接收者类型
    :return: 未读条数
    """
    async with async_session_factory() as db:
        count = (await db.execute(
            select(func.count()).select_from(InboxMessage).where(
                InboxMessage.receiver_id == receiver_id,
                InboxMessage.receiver_type == receiver_type,
                InboxMessage.is_read == 0,
            )
        )).scalar() or 0
        return count


async def mark_read(
    message_id: int, receiver_id: int, receiver_type: str
) -> bool:
    """标记单条站内信为已读（校验归属，越权返回 False）

    :param message_id:    消息 ID
    :param receiver_id:   接收者 ID
    :param receiver_type: 接收者类型
    :return: 是否标记成功
    """
    async with async_session_factory() as db:
        result = cast(CursorResult[Any], await db.execute(
            update(InboxMessage).where(
                InboxMessage.id == message_id,
                InboxMessage.receiver_id == receiver_id,
                InboxMessage.receiver_type == receiver_type,
            ).values(is_read=1, read_time=china_now())
        ))
        if result.rowcount == 0:
            return False
        await db.commit()
        return True


async def mark_all_read(receiver_id: int, receiver_type: str) -> int:
    """将接收者的所有未读站内信标记为已读

    :param receiver_id:   接收者 ID
    :param receiver_type: 接收者类型
    :return: 实际标记的条数
    """
    async with async_session_factory() as db:
        result = cast(CursorResult[Any], await db.execute(
            update(InboxMessage).where(
                InboxMessage.receiver_id == receiver_id,
                InboxMessage.receiver_type == receiver_type,
                InboxMessage.is_read == 0,
            ).values(is_read=1, read_time=china_now())
        ))
        await db.commit()
        return result.rowcount


async def delete_message(
    message_id: int, receiver_id: int, receiver_type: str
) -> bool:
    """删除单条站内信（校验归属，越权返回 False）

    :param message_id:    消息 ID
    :param receiver_id:   接收者 ID
    :param receiver_type: 接收者类型
    :return: 是否删除成功
    """
    async with async_session_factory() as db:
        msg = (await db.execute(
            select(InboxMessage).where(
                InboxMessage.id == message_id,
                InboxMessage.receiver_id == receiver_id,
                InboxMessage.receiver_type == receiver_type,
            )
        )).scalar_one_or_none()
        if not msg:
            return False
        await db.delete(msg)
        await db.commit()
        return True


# ========== 管理端操作 ==========

async def bulk_delete_messages(
    message_ids: list[int], receiver_type: str | None = None
) -> int:
    """批量删除站内信

    :param message_ids:   消息 ID 列表
    :param receiver_type: 可选，限定接收者类型
    :return: 实际删除条数
    """
    if not message_ids:
        return 0
    async with async_session_factory() as db:
        stmt = delete(InboxMessage).where(InboxMessage.id.in_(message_ids))
        if receiver_type:
            stmt = stmt.where(InboxMessage.receiver_type == receiver_type)
        result = cast(CursorResult[Any], await db.execute(stmt))
        await db.commit()
        return result.rowcount


async def list_all_inbox(
    page: int = 1, limit: int = 20, filters: dict | None = None
) -> dict:
    """管理端全站站内信列表（支持多条件筛选）

    :param page:    页码
    :param limit:   每页条数
    :param filters: 筛选条件字典，支持:
        - receiver_type: str  接收者类型 (farmer/admin)
        - receiver_id:   int  接收者 ID
        - is_read:       int  已读筛选 (0/1)
        - keyword:       str  标题关键词模糊匹配
    :return: {"total": int, "page": int, "limit": int, "list": [dict]}
    """
    filters = filters or {}
    async with async_session_factory() as db:
        q = select(InboxMessage)
        conditions = []

        if filters.get("receiver_type"):
            conditions.append(InboxMessage.receiver_type == filters["receiver_type"])
        if filters.get("receiver_id") is not None:
            conditions.append(InboxMessage.receiver_id == filters["receiver_id"])
        if filters.get("is_read") is not None:
            conditions.append(InboxMessage.is_read == filters["is_read"])
        if filters.get("keyword"):
            conditions.append(InboxMessage.title.contains(filters["keyword"]))

        if conditions:
            q = q.where(and_(*conditions))

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0

        rows = (await db.execute(
            q.order_by(InboxMessage.create_time.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        data = [_message_to_dict(m, include_receiver=True) for m in rows]
        await _add_receiver_names(db, rows, data)
        return {
            "total": total, "page": page, "limit": limit,
            "list": data,
        }


async def get_inbox_stats() -> dict:
    """站内信统计 — 总数 / 未读数 / 按 receiver_type 分组

    :return: {"total": int, "unread": int, "by_type": [{"receiver_type": str, "count": int}]}
    """
    async with async_session_factory() as db:
        total = (await db.execute(
            select(func.count()).select_from(InboxMessage)
        )).scalar() or 0

        unread = (await db.execute(
            select(func.count()).select_from(InboxMessage)
            .where(InboxMessage.is_read == 0)
        )).scalar() or 0

        group_rows = (await db.execute(
            select(InboxMessage.receiver_type, func.count())
            .group_by(InboxMessage.receiver_type)
        )).all()

        return {
            "total": total,
            "unread": unread,
            "by_type": [
                {"receiver_type": r[0], "count": r[1]} for r in group_rows
            ],
        }


# ========== 管理端扩展 ==========

async def get_inbox_message(message_id: int) -> dict | None:
    """管理端获取单条站内信详情（含 receiver 字段）

    :param message_id: 消息 ID
    :return: 消息 dict，不存在返回 None
    """
    async with async_session_factory() as db:
        msg = (await db.execute(
            select(InboxMessage).where(InboxMessage.id == message_id)
        )).scalar_one_or_none()
        if not msg:
            return None
        data = [_message_to_dict(msg, include_receiver=True)]
        await _add_receiver_names(db, [msg], data)
        return data[0]


async def delete_inbox_message_admin(message_id: int) -> bool:
    """管理端删除单条站内信（无接收者校验）

    :param message_id: 消息 ID
    :return: 是否删除成功
    """
    async with async_session_factory() as db:
        msg = (await db.execute(
            select(InboxMessage).where(InboxMessage.id == message_id)
        )).scalar_one_or_none()
        if not msg:
            return False
        await db.delete(msg)
        await db.commit()
        return True


async def get_farmer_message_detail(farmer_id: int, message_id: int) -> dict | None:
    """读取农户自己的站内信及相邻阅读导航。"""
    async with async_session_factory() as db:
        base = select(InboxMessage).where(
            InboxMessage.receiver_id == farmer_id,
            InboxMessage.receiver_type == "farmer",
        )
        current = (await db.execute(
            base.where(InboxMessage.id == message_id)
        )).scalar_one_or_none()
        if not current:
            return None

        def adjacent(newer: bool):
            order: tuple = ()
            if current.create_time is None:
                condition = (
                    InboxMessage.id > current.id
                    if newer else InboxMessage.id < current.id
                )
                order = (InboxMessage.id.asc(),) if newer else (InboxMessage.id.desc(),)
            elif newer:
                condition = (
                    (InboxMessage.create_time > current.create_time)
                    | ((InboxMessage.create_time == current.create_time)
                       & (InboxMessage.id > current.id))
                )
                order = (InboxMessage.create_time.asc(), InboxMessage.id.asc())
            else:
                condition = (
                    (InboxMessage.create_time < current.create_time)
                    | ((InboxMessage.create_time == current.create_time)
                       & (InboxMessage.id < current.id))
                )
                order = (InboxMessage.create_time.desc(), InboxMessage.id.desc())
            return base.where(condition).order_by(*order).limit(1)

        newer = (await db.execute(adjacent(True))).scalar_one_or_none()
        older = (await db.execute(adjacent(False))).scalar_one_or_none()

    def summary(message: InboxMessage | None) -> dict | None:
        if not message:
            return None
        return {
            "id": message.id,
            "title": message.title,
            "priority": message.priority,
            "create_time": _fmt_dt(message.create_time),
        }

    data = _message_to_dict(current, include_receiver=False)
    data["prev"] = summary(newer)
    data["next"] = summary(older)
    return data
