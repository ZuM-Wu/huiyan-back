"""
通知查询服务层

封装对 core.db.notice 模型的数据库查询逻辑，供 api 层调用。
所有函数返回纯 dict/list，不返回 ORM 实例。
"""
import logging
from datetime import datetime, timedelta
from core.time_utils import china_now
from typing import Any, cast

from sqlalchemy import select, func, or_, and_, delete
from sqlalchemy.engine import CursorResult

from core.db.base import async_session_factory
from core.db.notice import NoticeAction, NoticeLog

logger = logging.getLogger(__name__)


# ========== 内部辅助函数 ==========

def _fmt_dt(dt) -> str:
    """格式化 datetime 为字符串，None 返回空串"""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _action_to_dict(a: NoticeAction) -> dict:
    """NoticeAction ORM 实例转 dict"""
    return {
        "id": a.id,
        "action_key": a.action_key,
        "action_name": a.action_name,
        "action_type": a.action_type,
        "sms_enabled": a.sms_enabled,
        "sms_interface": a.sms_interface,
        "sms_template_id": a.sms_template_id,
        "sms_global_enabled": a.sms_global_enabled,
        "sms_global_interface": a.sms_global_interface,
        "sms_global_template_id": a.sms_global_template_id,
        "email_enabled": a.email_enabled,
        "email_interface": a.email_interface,
        "email_template_id": a.email_template_id,
        "trigger_inbox": a.trigger_inbox,
        "create_time": _fmt_dt(a.create_time),
        "update_time": _fmt_dt(a.update_time),
    }


def _log_to_dict(log: NoticeLog) -> dict:
    """NoticeLog ORM 实例转 dict"""
    return {
        "id": log.id,
        "action_key": log.action_key,
        "recipient": log.recipient,
        "channel": log.channel,
        "template_id": log.template_id,
        "content": log.content,
        "status": log.status,
        "error_msg": log.error_msg,
        "recipient_id": log.recipient_id,
        "extra": log.extra,
        "send_time": _fmt_dt(log.send_time),
        "create_time": _fmt_dt(log.create_time),
    }


# ========== 通知动作 ==========

async def list_notice_actions(page: int = 1, limit: int = 20) -> dict:
    """通知动作分页列表

    :param page:  页码（从 1 开始）
    :param limit: 每页条数
    :return: {"total": int, "page": int, "limit": int, "list": [dict, ...]}
    """
    async with async_session_factory() as db:
        q = select(NoticeAction)

        # 总数统计
        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0

        # 分页查询
        rows = (await db.execute(
            q.order_by(NoticeAction.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        return {
            "total": total, "page": page, "limit": limit,
            "list": [_action_to_dict(r) for r in rows],
        }


async def get_notice_action(action_key: str) -> dict | None:
    """按 action_key 获取单个通知动作详情

    :param action_key: 动作标识
    :return: 动作 dict，不存在返回 None
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(NoticeAction).where(NoticeAction.action_key == action_key)
        )
        action = result.scalar_one_or_none()
        if not action:
            return None
        return _action_to_dict(action)


async def update_notice_action(action_key: str, data: dict) -> bool:
    """更新通知动作配置

    :param action_key: 动作标识
    :param data:       待更新字段字典（仅更新非 None 值）
    :return: 是否更新成功（动作不存在返回 False）
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(NoticeAction).where(NoticeAction.action_key == action_key)
        )
        action = result.scalar_one_or_none()
        if not action:
            return False

        for field, value in data.items():
            if value is not None:
                setattr(action, field, value)

        await db.commit()
        return True


# ========== 通知日志 ==========

async def list_notice_logs(
    page: int = 1, limit: int = 20, filters: dict | None = None
) -> dict:
    """通知日志分页查询

    :param page:    页码
    :param limit:   每页条数
    :param filters: 筛选条件字典，支持:
        - action_key: str  动作标识精确匹配
        - channel:    str  渠道精确匹配 (sms/email)
        - status:     int  状态精确匹配 (0=失败 1=成功)
        - recipient:  str  接收者模糊匹配
        - start_time: str  开始日期 YYYY-MM-DD
        - end_time:   str  结束日期 YYYY-MM-DD（含当天）
    :return: {"total": int, "page": int, "limit": int, "list": [dict, ...]}
    """
    filters = filters or {}
    async with async_session_factory() as db:
        q = select(NoticeLog)
        conditions = []

        if filters.get("action_key"):
            conditions.append(NoticeLog.action_key == filters["action_key"])
        if filters.get("channel"):
            conditions.append(NoticeLog.channel == filters["channel"])
        if filters.get("status") is not None:
            conditions.append(NoticeLog.status == filters["status"])
        if filters.get("recipient"):
            conditions.append(NoticeLog.recipient.contains(filters["recipient"]))
        if filters.get("start_time"):
            try:
                start_dt = datetime.strptime(filters["start_time"], "%Y-%m-%d")
                conditions.append(NoticeLog.create_time >= start_dt)
            except ValueError:
                logger.warning("list_notice_logs: start_time 格式错误: %s", filters["start_time"])
        if filters.get("end_time"):
            try:
                # 结束日期包含当天，向后推一天
                end_dt = datetime.strptime(filters["end_time"], "%Y-%m-%d") + timedelta(days=1)
                conditions.append(NoticeLog.create_time < end_dt)
            except ValueError:
                logger.warning("list_notice_logs: end_time 格式错误: %s", filters["end_time"])

        if conditions:
            q = q.where(and_(*conditions))

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0

        rows = (await db.execute(
            q.order_by(NoticeLog.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        return {
            "total": total, "page": page, "limit": limit,
            "list": [_log_to_dict(r) for r in rows],
        }


# ========== 通知动作扩展 ==========

async def search_notice_actions(
    page: int = 1, limit: int = 20,
    keywords: str = "", action_type: str = "",
) -> dict:
    """通知动作分页列表（支持关键词与类型筛选）

    :param page:        页码
    :param limit:       每页条数
    :param keywords:    搜索关键词（匹配 action_name / action_key）
    :param action_type: 类型精确匹配
    :return: {"total": int, "page": int, "limit": int, "list": [dict]}
    """
    async with async_session_factory() as db:
        q = select(NoticeAction)
        if keywords:
            q = q.where(or_(
                NoticeAction.action_name.contains(keywords),
                NoticeAction.action_key.contains(keywords),
            ))
        if action_type:
            q = q.where(NoticeAction.action_type == action_type)

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0

        rows = (await db.execute(
            q.order_by(NoticeAction.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        return {
            "total": total, "page": page, "limit": limit,
            "list": [_action_to_dict(r) for r in rows],
        }


async def check_action_key_exists(action_key: str) -> bool:
    """检查 action_key 是否已存在"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(NoticeAction).where(NoticeAction.action_key == action_key)
        )
        return result.first() is not None


async def create_notice_action(data: dict) -> dict:
    """创建通知动作，返回创建后的 dict"""
    async with async_session_factory() as db:
        action = NoticeAction(**data)
        db.add(action)
        await db.commit()
        await db.refresh(action)
        return _action_to_dict(action)


async def delete_notice_action(action_key: str) -> bool:
    """按 action_key 删除通知动作"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(NoticeAction).where(NoticeAction.action_key == action_key)
        )
        action = result.scalar_one_or_none()
        if not action:
            return False
        await db.delete(action)
        await db.commit()
        return True


async def batch_set_action_enabled(action_keys: list[str], enabled: bool) -> int:
    """批量启用/禁用动作的短信与邮件开关，返回受影响条数"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(NoticeAction).where(NoticeAction.action_key.in_(action_keys))
        )
        actions = result.scalars().all()
        count = 0
        for action in actions:
            action.sms_enabled = enabled
            action.email_enabled = enabled
            count += 1
        await db.commit()
        return count


async def bulk_update_action_templates(
    action_keys: list[str],
    sms_interface: str | None = None,
    sms_template_id: int | None = None,
    email_interface: str | None = None,
    email_template_id: int | None = None,
) -> int:
    """批量更新动作的模板绑定，返回受影响条数"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(NoticeAction).where(NoticeAction.action_key.in_(action_keys))
        )
        actions = result.scalars().all()
        count = 0
        for action in actions:
            if sms_interface:
                action.sms_interface = sms_interface
            if sms_template_id is not None:
                action.sms_template_id = sms_template_id
            if email_interface:
                action.email_interface = email_interface
            if email_template_id is not None:
                action.email_template_id = email_template_id
            count += 1
        await db.commit()
        return count


# ========== 通知日志扩展 ==========

async def get_notice_log(log_id: int) -> dict | None:
    """获取单条通知日志详情"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(NoticeLog).where(NoticeLog.id == log_id)
        )
        log = result.scalar_one_or_none()
        if not log:
            return None
        return _log_to_dict(log)


async def notice_log_stats(days: int) -> dict:
    """通知发送统计 — 按渠道汇总成功/失败次数"""
    since = china_now() - timedelta(days=days)
    async with async_session_factory() as db:
        result = await db.execute(
            select(
                NoticeLog.channel,
                NoticeLog.status,
                func.count().label("cnt"),
            )
            .where(NoticeLog.create_time >= since)
            .group_by(NoticeLog.channel, NoticeLog.status)
        )
        rows = result.all()
        stats: dict[str, dict[str, int]] = {}
        for channel, status, cnt in rows:
            bucket = stats.setdefault(channel, {"success": 0, "failed": 0})
            if status == 1:
                bucket["success"] += cnt
            else:
                bucket["failed"] += cnt
        return {"days": days, "stats": stats}


async def cleanup_notice_logs(before_days: int) -> int:
    """删除 N 天前的通知日志，返回删除条数"""
    cutoff = china_now() - timedelta(days=before_days)
    async with async_session_factory() as db:
        result = cast(CursorResult[Any], await db.execute(
            delete(NoticeLog).where(NoticeLog.create_time < cutoff)
        ))
        await db.commit()
        return result.rowcount or 0
