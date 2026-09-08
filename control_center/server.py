"""控制中心专用 Uvicorn Server。"""

from __future__ import annotations

import signal
import sys
from types import FrameType

import uvicorn


WINDOWS_RELOAD_SIGNALS = {
    signal.SIGINT,
    *([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else []),
}


class ControlCenterServer(uvicorn.Server):
    """隔离业务 Uvicorn 重载时广播到同一 Windows 控制台的信号。"""

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # Windows 的 Uvicorn 重载器通过控制台事件终止工作进程，该事件会广播给
        # 同一控制台的控制中心。控制中心仅响应网页退出动作或 SIGTERM 结束自身。
        if sys.platform == "win32" and sig in WINDOWS_RELOAD_SIGNALS:
            return
        super().handle_exit(sig, frame)
