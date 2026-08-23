# -*- coding: utf-8 -*-
"""
进程内滑动窗口频率限制器

设计说明:
- 单 worker 部署（见 main.py 头部声明），进程内存计数即全局有效
- 滑动窗口: 每个 key 保存窗口内的请求时间戳列表，过期时间戳惰性清理
- 容量上限: 超过 10k 个 key 时两级淘汰（先过期 key，再按活跃度），防内存膨胀
- 禁止引入 Redis（系统缓存策略为纯文件降级，频控用进程内存即可）
"""
import time

# key -> 窗口内请求时间戳列表（按写入顺序，dict 天然保序用于淘汰最旧 key）
_buckets: dict[str, list[float]] = {}

# 最大 key 数量，超限时批量淘汰最早写入的 key（防恶意刷 key 撑爆内存）
_MAX_KEYS = 10000


def check_rate(key: str, limit: int, window: int) -> bool:
    """
    滑动窗口频率检查

    Args:
        key: 限流维度标识（如 "verify:13800138000" / "check-account:1.2.3.4"）
        limit: 窗口内允许的最大次数
        window: 窗口秒数

    Returns:
        True=允许本次请求（并记账），False=超出频率限制
    """
    allowed, _ = check_rate_detail(key, limit, window)
    return allowed


def check_rate_detail(key: str, limit: int, window: int) -> tuple[bool, int]:
    """
    滑动窗口频率检查（带重试等待秒数）

    Args:
        key: 限流维度标识
        limit: 窗口内允许的最大次数
        window: 窗口秒数

    Returns:
        (是否允许, retry_after 秒数)；允许时 retry_after 为 0，
        超限时为最早一条记账离开窗口还需的秒数（供 429 提示复用）
    """
    now = time.time()

    # 容量上限保护（防恶意刷 key 冲掉在用计数器），两级淘汰：
    # 1) 先淘汰“最新时间戳已超过 600 秒”的过期 key（对在用计数器无损）
    # 2) 若仍不足容量 80%，再按最后活跃时间升序淘汰至目标容量
    if key not in _buckets and len(_buckets) >= _MAX_KEYS:
        for stale_key in [k for k, v in _buckets.items() if not v or now - v[-1] > 600]:
            _buckets.pop(stale_key, None)
        target_size = int(_MAX_KEYS * 0.8)
        if len(_buckets) > target_size:
            by_activity = sorted(_buckets.items(), key=lambda kv: kv[1][-1] if kv[1] else 0)
            for stale_key, _stamps in by_activity[: len(_buckets) - target_size]:
                _buckets.pop(stale_key, None)

    # 惰性清理：只保留窗口内的时间戳
    stamps = [t for t in _buckets.get(key, []) if now - t < window]

    if len(stamps) >= limit:
        _buckets[key] = stamps
        # 最早一条记账离开窗口后即可重试
        retry_after = max(1, int(window - (now - stamps[0])) + 1)
        return False, retry_after

    stamps.append(now)
    _buckets[key] = stamps
    return True, 0


def reset(key: str = "") -> None:
    """清空指定 key 的计数（key 为空时清空全部，测试用）"""
    if key:
        _buckets.pop(key, None)
    else:
        _buckets.clear()
