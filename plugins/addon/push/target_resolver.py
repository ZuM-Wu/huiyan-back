# -*- coding: utf-8 -*-
"""
目标条件解析器

将前端传来的 push_target JSON 配置转为 farmer_service 查询条件，
返回符合条件的农户列表。

支持三种模式：
- all: 全部农户（status=1）
- specified: 指定农户 ID 列表
- filtered: 按条件筛选（注册时间范围、地区、公司、状态等）

耦合修复：不再直接 import core.db.farmer，改用 core.farmer_service 公开门面。
"""
import logging
from typing import Any, Dict, List, Tuple

from core.farmer_service import query_farmers_by_filters, count_farmers_by_filters

logger = logging.getLogger(__name__)


async def resolve_targets(push_target: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    """
    解析目标条件，返回目标农户列表

    Args:
        push_target: 目标条件 JSON
            mode: all / specified / filtered
            farmer_ids: 指定农户 ID 列表（mode=specified）
            filters: 筛选条件 dict（mode=filtered）

    Returns:
        (农户列表[{id, username, phone, email}], 总数)
    """
    mode = push_target.get("mode", "all")

    if mode == "specified":
        farmer_ids = push_target.get("farmer_ids", [])
        if not farmer_ids:
            return [], 0
        # 指定 ID 列表模式：构造 filters 只按 ID 筛选
        # farmer_service.query_farmers_by_filters 内部已限定 status=1
        filters = {"farmer_ids": farmer_ids}
        farmers = await query_farmers_by_filters(filters)
        return farmers, len(farmers)

    elif mode == "filtered":
        filters = push_target.get("filters", {})
        farmers = await query_farmers_by_filters(filters)
        return farmers, len(farmers)

    else:
        # mode=all：无额外筛选条件
        farmers = await query_farmers_by_filters({})
        return farmers, len(farmers)


async def count_targets(
    push_target: Dict[str, Any],
) -> int:
    """仅统计符合条件的农户数量（用于预览）"""
    mode = push_target.get("mode", "all")

    if mode == "specified":
        farmer_ids = push_target.get("farmer_ids", [])
        if not farmer_ids:
            return 0
        filters = {"farmer_ids": farmer_ids}
        return await count_farmers_by_filters(filters)

    elif mode == "filtered":
        filters = push_target.get("filters", {})
        return await count_farmers_by_filters(filters)

    else:
        return await count_farmers_by_filters({})
