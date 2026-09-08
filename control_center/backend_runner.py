"""监督支持热重载的业务 Uvicorn。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from watchfiles import PythonFilter, watch

from control_center.process_manager import BACKEND_HOST, BACKEND_PORT


BACKEND_ROOT = Path(__file__).resolve().parent.parent


def build_uvicorn_command() -> list[str]:
    """构造业务服务命令，热重载由本模块的监督进程负责。"""
    return [
        sys.executable, "-m", "uvicorn", "main:app", "--host", BACKEND_HOST,
        "--port", str(BACKEND_PORT), "--timeout-graceful-shutdown", "10",
    ]


def display_changed_paths(backend_root: Path, changes: set[tuple[object, str]]) -> list[str]:
    """将 watchfiles 的字符串路径转换为便于日志阅读的相对路径。"""
    return sorted(str(Path(path).relative_to(backend_root)) for _, path in changes)


class BackendRunner:
    """监督一个 Uvicorn 子进程，并在 Python 源码变化后优雅重启。"""

    def __init__(self, backend_root: Path = BACKEND_ROOT) -> None:
        self.backend_root = backend_root
        self.stop_event = threading.Event()
        self.child: subprocess.Popen | None = None

    def run(self) -> int:
        self._install_signal_handlers()
        self._start_child()
        try:
            changes_iterator = watch(
                *self._watch_paths(),
                watch_filter=PythonFilter(),
                stop_event=self.stop_event,
                rust_timeout=500,
                yield_on_timeout=True,
            )
            for changes in changes_iterator:
                if self.stop_event.is_set():
                    break
                if self.child is None or self.child.poll() is not None:
                    code = self.child.returncode if self.child else 1
                    print(f"ERROR: 业务 Uvicorn 异常退出，退出码 {code}", flush=True)
                    return int(code or 1)
                if changes:
                    paths = display_changed_paths(self.backend_root, changes)
                    print(f"WARNING: 检测到 Python 文件变化，正在重载: {', '.join(paths)}", flush=True)
                    self._stop_child()
                    if not self.stop_event.is_set():
                        self._start_child()
            return 0
        finally:
            self._stop_child()

    def _watch_paths(self) -> tuple[Path, ...]:
        """只监视 Python 源码目录，避免 upload/runtime 消耗 Linux inotify 配额。"""
        candidates = (
            self.backend_root / "main.py",
            *(self.backend_root / name for name in (
                "api", "core", "control_center", "migrations", "plugins", "schemas", "services",
            )),
        )
        return tuple(path for path in candidates if path.exists())

    def _install_signal_handlers(self) -> None:
        handled = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            handled.append(signal.SIGBREAK)
        if hasattr(signal, "SIGHUP"):
            handled.append(signal.SIGHUP)
        for handled_signal in handled:
            signal.signal(handled_signal, self._request_stop)

    def _request_stop(self, _sig: int, _frame: object) -> None:
        self.stop_event.set()

    def _start_child(self) -> None:
        kwargs: dict = {"cwd": str(self.backend_root)}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self.child = subprocess.Popen(build_uvicorn_command(), **kwargs)
        print(f"INFO: 已启动业务 Uvicorn，PID {self.child.pid}", flush=True)

    def _stop_child(self, timeout: float = 15.0) -> None:
        child = self.child
        if child is None:
            return
        if child.poll() is not None:
            self.child = None
            return
        print(f"INFO: 正在优雅停止业务 Uvicorn，PID {child.pid}", flush=True)
        try:
            try:
                if os.name == "nt":
                    child.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    child.terminate()
            except ProcessLookupError:
                return
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                print("WARNING: 业务 Uvicorn 停止超时，正在强制终止", flush=True)
                try:
                    child.kill()
                except ProcessLookupError:
                    return
                child.wait(timeout=5)
        finally:
            self.child = None


def main() -> None:
    raise SystemExit(BackendRunner().run())


if __name__ == "__main__":
    main()
