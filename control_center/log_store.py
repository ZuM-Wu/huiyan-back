"""控制中心结构化日志与滚动文件。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.time_utils import CHINA_TIMEZONE, china_now_aware


class _ChinaTimeFormatter(logging.Formatter):
    """确保 UTC 主机上的控制中心文件日志仍显示中国标准时间。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        value = datetime.fromtimestamp(record.created, CHINA_TIMEZONE)
        return value.strftime(datefmt) if datefmt else value.isoformat(timespec="seconds")


class ControlLog:
    """保存有限内存日志，同时写入可追溯的滚动文件。"""

    def __init__(self, runtime_dir: Path, max_entries: int = 2000) -> None:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self._entries: deque[dict] = deque(maxlen=max_entries)
        self._sequence = 0
        self._lock = threading.Lock()
        self._logger = logging.getLogger(f"huiyan.control_center.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = RotatingFileHandler(
            runtime_dir / "control-center.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(_ChinaTimeFormatter("%(asctime)s [%(message)s]"))
        self._logger.addHandler(handler)

    def append(self, source: str, message: str) -> dict:
        """写入一条日志，清理终端控制符并返回结构化条目。"""
        clean = "".join(char for char in str(message).rstrip() if char == "\t" or ord(char) >= 32)
        if not clean:
            return {}
        with self._lock:
            self._sequence += 1
            entry = {
                "cursor": self._sequence,
                "time": china_now_aware().isoformat(timespec="seconds"),
                "source": source,
                "message": clean,
            }
            self._entries.append(entry)
        self._logger.info("%s] %s", source, clean)
        return entry

    def read_after(self, cursor: int, limit: int = 500) -> dict:
        """按游标增量返回日志；游标过旧时从当前缓冲首条开始。"""
        safe_limit = max(1, min(int(limit), 1000))
        with self._lock:
            items = [dict(item) for item in self._entries if item["cursor"] > cursor]
            items = items[:safe_limit]
            next_cursor = items[-1]["cursor"] if items else max(cursor, self._sequence)
        return {"items": items, "next_cursor": next_cursor}

    def close(self) -> None:
        """关闭文件句柄，允许控制中心干净退出。"""
        for handler in list(self._logger.handlers):
            handler.close()
            self._logger.removeHandler(handler)
