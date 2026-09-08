# -*- coding: utf-8 -*-
"""
App管理插件 — 公开API缓存工具

设计（参照 core/weather_service.py 的缓存模式）：
- 缓存值内嵌 cached_at 时间戳与 ttl，读取时自判过期，
  兼容文件缓存降级模式下 cache_manager.set 不落实 TTL 的限制
- 管理端写操作成功后调用 invalidate() 主动失效对应键
- 插件卸载时调用 clear_all_cache() 删除全部键，防止幽灵数据
"""
import logging
from datetime import datetime
from core.time_utils import china_now

from core.cache.cache_manager import cache_manager

logger = logging.getLogger(__name__)

# 公开API缓存键（App启动高频调用的三个接口）
# 版本键带 v2 后缀：1.1.0 新增 build_number/update_policy 字段，
# 升版避免升级后 TTL 窗口期内旧缓存缺新字段
CACHE_KEY_VERSION = "hy:app_manage:latest_version:v2"
CACHE_KEY_AD = "hy:app_manage:ad"
CACHE_KEY_NOTICES = "hy:app_manage:notices"
# 缓存时长兜底默认值（秒），与 plugin.json 的 app_manage.cache_ttl 保持一致
DEFAULT_CACHE_TTL = 300


async def cache_get(key: str):
    """
    读取缓存 — 内嵌时间戳自判过期。
    命中且未过期返回业务数据；未命中或已过期返回 None（调用方回源查库）。
    """
    cached = await cache_manager.get(key)
    if not cached or not cached.get("cached_at"):
        return None
    try:
        age = (china_now() - datetime.fromisoformat(cached["cached_at"])).total_seconds()
    except (TypeError, ValueError):
        return None
    if age >= cached.get("ttl", DEFAULT_CACHE_TTL):
        return None
    return cached.get("data")


async def cache_set(key: str, data, ttl: int = DEFAULT_CACHE_TTL) -> None:
    """写入缓存 — 值内嵌 cached_at 与 ttl（data 允许为 None，表示已确认无数据）"""
    await cache_manager.set(
        key,
        {"cached_at": china_now().isoformat(), "ttl": ttl, "data": data},
        expire=ttl,
    )


async def invalidate(key: str) -> None:
    """主动失效单个缓存键（管理端写操作成功后调用）"""
    await cache_manager.delete(key)


async def clear_all_cache() -> None:
    """删除本插件全部公开API缓存键（插件卸载时调用）"""
    for key in (CACHE_KEY_VERSION, CACHE_KEY_AD, CACHE_KEY_NOTICES):
        await cache_manager.delete(key)
