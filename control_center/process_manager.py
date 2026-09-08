"""由控制中心独占管理的 Uvicorn 进程树。"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from control_center.log_store import ControlLog


BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
HEALTH_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}/health"


def build_backend_command() -> list[str]:
    """构造受控后端命令，固定使用控制中心当前 Python 环境。"""
    return [sys.executable, "-m", "control_center.backend_runner"]


def port_is_listening(host: str = BACKEND_HOST, port: int = BACKEND_PORT) -> bool:
    """只检测目标端口，不尝试识别或终止未知进程。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.25)
        return client.connect_ex((host, port)) == 0


class _WindowsJob:
    """将 Uvicorn 重载进程树限制在控制中心创建的 Windows Job 中。"""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64), ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64), ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64), ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _ExtendedLimits(ctypes.Structure):
        pass

    _ExtendedLimits._fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self, process: subprocess.Popen) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = self._ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            handle, self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        assigned = configured and kernel32.AssignProcessToJobObject(
            handle, ctypes.c_void_p(process._handle),
        )
        if not assigned:
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = handle

    def terminate(self) -> None:
        if self._handle:
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class BackendProcessManager:
    """仅操作自己启动的后端进程，外部端口占用始终交给用户处理。"""

    def __init__(self, backend_root: Path, log: ControlLog) -> None:
        self.backend_root = backend_root
        self.log = log
        self.process: subprocess.Popen | None = None
        self._job: _WindowsJob | None = None
        self._reader: threading.Thread | None = None
        self._state = "stopped"
        self._last_error = ""
        self._expected_stop = False
        self._lock = threading.RLock()

    def snapshot(self) -> dict:
        with self._lock:
            self._refresh_state()
            return {
                "state": self._state,
                "ownership": "managed" if self._is_alive() else (
                    "external" if port_is_listening() else "none"
                ),
                "pid": self.process.pid if self._is_alive() else None,
                "last_error": self._last_error,
            }

    def start(self) -> int:
        with self._lock:
            self._refresh_state()
            if self._is_alive():
                return int(self.process.pid)
            if port_is_listening():
                self._state = "external"
                raise RuntimeError("8000 端口由外部进程占用，请先在原终端停止后端")
            self._state = "starting"
            self._last_error = ""
            self._expected_stop = False
            command = build_backend_command()
            environment = os.environ.copy()
            environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
            kwargs = {
                "cwd": str(self.backend_root), "env": environment,
                "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                "text": True, "encoding": "utf-8", "errors": "replace", "bufsize": 1,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            try:
                process = subprocess.Popen(command, **kwargs)
            except Exception as exc:
                self._state = "failed"
                self._last_error = f"启动受控后端失败: {exc}"
                self._expected_stop = False
                raise
            try:
                job = _WindowsJob(process) if os.name == "nt" else None
            except Exception:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                self._state = "failed"
                self._last_error = "受控后端进程组初始化失败"
                raise
            self.process, self._job = process, job
            self._reader = threading.Thread(target=self._read_output, args=(process,), daemon=True)
            self._reader.start()
            self.log.append("backend", f"已启动 Uvicorn，PID {process.pid}")
            return int(process.pid)

    def stop(self, timeout: float = 15.0) -> None:
        with self._lock:
            self._refresh_state()
            if not self._is_alive():
                if port_is_listening():
                    self._state = "external"
                    raise RuntimeError("8000 端口由外部进程占用，控制中心不会终止该进程")
                self._state = "stopped"
                return
            process = self.process
            self._state = "stopping"
            self._expected_stop = True
            self.log.append("backend", f"正在停止 Uvicorn，PID {process.pid}")
            stop_error: Exception | None = None
            try:
                self._send_stop_signal(process)
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.log.append("backend", "优雅停止超时，正在终止受控进程树")
                try:
                    self._force_stop(process)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    stop_error = RuntimeError(f"受控进程强制停止超时，PID {process.pid} 仍存活")
                    self._last_error = str(stop_error)
                    self._state = "failed"
                    stop_error.__cause__ = exc
                except OSError as exc:
                    stop_error = exc
                    self._last_error = f"强制停止受控后端失败: {exc}"
                    self._state = "failed"
                except Exception as exc:
                    stop_error = exc
                    self._last_error = f"强制停止受控后端失败: {exc}"
                    self._state = "failed"
            except OSError as exc:
                stop_error = exc
                self._state = "failed"
                self._last_error = f"停止受控后端失败: {exc}"
            finally:
                self._close_job()
                # 无论发送信号、等待或强停是否成功，都断开失效进程引用。
                self.process = None
                self._reader = None
                self._expected_stop = False
            if stop_error is not None:
                raise stop_error
            deadline = time.monotonic() + 5
            while port_is_listening() and time.monotonic() < deadline:
                time.sleep(0.1)
            if port_is_listening():
                self._state = "failed"
                raise RuntimeError("受控进程已退出，但 8000 端口仍在监听")
            self._state = "stopped"
            self.process = None
            self.log.append("backend", "Uvicorn 已停止")

    def probe_health(self, timeout: float = 2.0) -> dict | None:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if response.status == 200 and data.get("code") == 200:
                return data
        except (OSError, ValueError, urllib.error.URLError):
            return None
        return None

    def mark_health(self, health: dict) -> None:
        with self._lock:
            if self._is_alive():
                self._state = "degraded" if health.get("status") == "degraded" else "running"

    def mark_failed(self, message: str) -> None:
        with self._lock:
            self._last_error = message
            self._state = "failed"

    def _is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _refresh_state(self) -> None:
        if self._is_alive():
            return
        if self.process is not None:
            code = self.process.poll()
            self._close_job()
            self.process = None
            self._reader = None
            if not self._expected_stop and code:
                self._last_error = f"Uvicorn 异常退出，退出码 {code}"
                self._state = "failed"
                return
            self._expected_stop = False
        if port_is_listening():
            self._state = "external"
        elif self._state not in {"failed", "starting"}:
            self._state = "stopped"

    def _read_output(self, process: subprocess.Popen) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self.log.append("uvicorn", line)

    def _force_stop(self, process: subprocess.Popen) -> None:
        if self._job is not None:
            self._job.terminate()
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    @staticmethod
    def _send_stop_signal(process: subprocess.Popen) -> None:
        """发送优雅停止信号；目标已退出时将竞态视为已停止。"""
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

    def _close_job(self) -> None:
        if self._job is not None:
            self._job.close()
            self._job = None
