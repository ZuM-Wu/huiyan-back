"""慧眼系统统一中国标准时间门面。"""

from datetime import datetime, timedelta, timezone


CHINA_UTC_OFFSET = timedelta(hours=8)
CHINA_TIMEZONE = timezone(CHINA_UTC_OFFSET, name="Asia/Shanghai")
CHINA_DB_TIME_ZONE = "+08:00"


def china_now() -> datetime:
    """返回用于业务计算和 MySQL DATETIME 存储的无时区中国时间。"""
    return datetime.now(CHINA_TIMEZONE).replace(tzinfo=None)


def china_now_aware() -> datetime:
    """返回带 UTC+8 偏移的中国时间，供 JWT 和跨系统时间戳使用。"""
    return datetime.now(CHINA_TIMEZONE)


def china_iso_now() -> str:
    """返回秒级、带 +08:00 偏移的 ISO 8601 中国时间。"""
    return china_now_aware().isoformat(timespec="seconds")
