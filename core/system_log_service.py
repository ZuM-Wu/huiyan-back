"""系统操作日志服务层

封装对 hy_system_log 表的查询，供 api 层调用。
所有函数返回纯 dict/list，不返回 ORM 实例。

设计要点:
- 支持按日志类型筛选（逗号分隔多类型，如 "config,cert_review"）
- 支持按关键词搜索描述字段
- 支持按日期范围筛选（date_from / date_to）
- 查询结果按 ID 倒序排列（最新日志在前）
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func, desc

from core.db.base import async_session_factory
from core.db.system_log import SystemLog

logger = logging.getLogger(__name__)


def _row_to_dict(row: SystemLog) -> dict:
    """将日志 ORM 行转换为输出字典"""
    return {
        "id": row.id,
        "type": row.type,
        "rel_id": row.rel_id,
        "description": row.description,
        "user_type": row.user_type,
        "user_id": row.user_id,
        "user_name": row.user_name,
        "ip": row.ip,
        "create_time": str(row.create_time) if row.create_time else None,
    }


async def list_logs(
    page: int = 1,
    limit: int = 20,
    keywords: str = "",
    log_type: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """获取操作日志分页列表

    Args:
        page:      页码（从 1 开始）
        limit:     每页条数
        keywords:  搜索关键词（匹配 description 描述字段）
        log_type:  日志类型，支持逗号分隔多类型（如 "config,cert_review"）
        date_from: 起始日期（包含），datetime 对象
        date_to:   截止日期（包含），datetime 对象

    Returns:
        {"total": int, "page": int, "limit": int, "list": [...]}
    """
    async with async_session_factory() as db:
        query = select(SystemLog)

        # 日志类型筛选（支持逗号分隔多类型）
        if log_type:
            types = [t.strip() for t in log_type.split(",") if t.strip()]
            if len(types) == 1:
                query = query.where(SystemLog.type == types[0])
            else:
                query = query.where(SystemLog.type.in_(types))

        # 关键词搜索（匹配描述字段）
        if keywords:
            query = query.where(SystemLog.description.contains(keywords))

        # 日期范围筛选
        if date_from:
            query = query.where(SystemLog.create_time >= date_from)
        if date_to:
            query = query.where(SystemLog.create_time <= date_to)

        # 按 ID 倒序排列
        query = query.order_by(desc(SystemLog.id))

        # 统计总数
        total = (
            await db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar() or 0

        # 分页查询
        rows = (
            await db.execute(
                query.offset((page - 1) * limit).limit(limit)
            )
        ).scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": [_row_to_dict(item) for item in rows],
    }
