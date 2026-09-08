"""控制中心进程操作与插件更新状态机。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from control_center.log_store import ControlLog
from control_center.process_manager import BackendProcessManager
from core.time_utils import china_now_aware
from core.platform.plugin_offline_update import load_update_plans


@dataclass
class ControlJob:
    job_id: str
    kind: str
    status: str = "queued"
    phase: str = "queued"
    message: str = ""
    error: str = ""
    result: list[dict] = field(default_factory=list)
    health: dict | None = None
    created_at: str = field(default_factory=lambda: china_now_aware().isoformat(timespec="seconds"))
    finished_at: str | None = None

    def public(self) -> dict:
        return asdict(self)


class ControlCenterService:
    """串行执行后端生命周期与离线更新，确保同一时间只有一个写操作。"""

    def __init__(self, backend_root: Path, log: ControlLog) -> None:
        self.backend_root = backend_root
        self.log = log
        self.process = BackendProcessManager(backend_root, log)
        self.jobs: dict[str, ControlJob] = {}
        self.active_job_id: str | None = None
        self._tasks: set[asyncio.Task] = set()

    async def state(self) -> dict:
        try:
            plans = await load_update_plans()
            pending_error = ""
        except Exception as exc:
            plans, pending_error = [], str(exc)
        current = self.jobs.get(self.active_job_id or "")
        return {
            "backend": self.process.snapshot(),
            "pending_updates": [self._public_plan(plan) for plan in plans],
            "pending_error": pending_error,
            "current_operation": current.public() if current else None,
        }

    def submit_backend_action(self, action: str) -> ControlJob:
        handlers: dict[str, Callable[[ControlJob], Awaitable[None]]] = {
            "start": self._start_backend,
            "stop": self._stop_backend,
            "restart": self._restart_backend,
        }
        if action not in handlers:
            raise ValueError("不支持的后端操作")
        return self._submit(f"backend:{action}", handlers[action])

    def submit_update(self, operation_id: str | None = None, *, apply_all: bool = False) -> ControlJob:
        if bool(operation_id) == bool(apply_all):
            raise ValueError("operation_id 与 apply_all 必须二选一")

        async def handler(job: ControlJob) -> None:
            await self._run_update(job, operation_id=operation_id, apply_all=apply_all)

        return self._submit("plugin-update:all" if apply_all else "plugin-update:single", handler)

    def submit_exit(self, shutdown_callback: Callable[[], None]) -> ControlJob:
        async def handler(job: ControlJob) -> None:
            job.phase = "stopping"
            await asyncio.to_thread(self.process.stop)
            job.message = "控制中心即将退出"
            asyncio.get_running_loop().call_later(0.5, shutdown_callback)

        return self._submit("control-center:exit", handler)

    async def auto_start(self) -> None:
        """首次启动后自动托管后端；外部进程存在时只记录提示。"""
        await asyncio.sleep(0.2)
        if self.process.snapshot()["state"] == "external":
            self.log.append("control", "检测到外部进程占用 8000 端口，等待用户手动切换")
            return
        try:
            self.submit_backend_action("start")
        except RuntimeError as exc:
            self.log.append("control", str(exc))

    async def close(self) -> None:
        """应用关闭时停止受控后端，防止遗留失去所有者的进程。"""
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        try:
            await asyncio.to_thread(self.process.stop, 8.0)
        except Exception as exc:
            self.log.append("control", f"退出时停止后端失败: {exc}")

    def _submit(self, kind: str, handler: Callable[[ControlJob], Awaitable[None]]) -> ControlJob:
        if self.active_job_id:
            active = self.jobs.get(self.active_job_id)
            if active and active.status in {"queued", "running"}:
                raise RuntimeError(f"已有操作正在执行: {active.job_id}")
        job = ControlJob(job_id=f"control-{uuid4().hex}", kind=kind)
        self.jobs[job.job_id] = job
        self.active_job_id = job.job_id
        task = asyncio.create_task(self._execute(job, handler))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._trim_jobs()
        return job

    async def _execute(self, job: ControlJob, handler: Callable[[ControlJob], Awaitable[None]]) -> None:
        job.status = "running"
        self.log.append("control", f"开始操作 {job.kind} ({job.job_id})")
        try:
            await handler(job)
            job.status = "succeeded"
            job.phase = "succeeded"
            job.message = job.message or "操作完成"
            self.log.append("control", f"操作完成 {job.kind}")
        except asyncio.CancelledError:
            job.status, job.phase, job.error = "failed", "failed", "控制中心正在退出"
            raise
        except Exception as exc:
            job.status, job.phase, job.error = "failed", "failed", str(exc)
            self.log.append("control", f"操作失败 {job.kind}: {exc}")
        finally:
            job.finished_at = china_now_aware().isoformat(timespec="seconds")
            if self.active_job_id == job.job_id:
                self.active_job_id = None

    async def _start_backend(self, job: ControlJob) -> None:
        job.phase, job.message = "starting", "正在启动后端"
        await asyncio.to_thread(self.process.start)
        job.health = await self._wait_for_health(job)
        job.message = "后端已启动" if job.health.get("status") == "ok" else "后端已启动，但存在降级组件"

    async def _stop_backend(self, job: ControlJob) -> None:
        job.phase, job.message = "stopping", "正在停止后端"
        await asyncio.to_thread(self.process.stop)
        job.message = "后端已停止"

    async def _restart_backend(self, job: ControlJob) -> None:
        job.phase, job.message = "stopping", "正在停止后端"
        await asyncio.to_thread(self.process.stop)
        job.phase, job.message = "restarting", "正在重新启动后端"
        await asyncio.to_thread(self.process.start)
        job.health = await self._wait_for_health(job)
        job.message = "后端已重启" if job.health.get("status") == "ok" else "后端已重启，但存在降级组件"

    async def _run_update(self, job: ControlJob, *, operation_id: str | None, apply_all: bool) -> None:
        plans = await load_update_plans(operation_id)
        if not plans:
            raise ValueError("没有待应用的插件更新计划")
        if not apply_all and plans[0].get("status") != "awaiting_restart":
            raise ValueError("更新计划当前不可执行")
        if self.process.snapshot()["state"] == "external":
            raise RuntimeError("8000 端口由外部进程占用，无法安全执行离线更新")
        stopped = False
        update_error: Exception | None = None
        try:
            job.phase, job.message = "stopping", "正在停止后端"
            await asyncio.to_thread(self.process.stop)
            stopped = True
            job.phase, job.message = "dry_run", "正在执行离线预检"
            await self._run_updater(operation_id, apply_all, dry_run=True)
            job.phase, job.message = "applying", "正在应用插件更新"
            job.result = await self._run_updater(operation_id, apply_all, dry_run=False)
        except Exception as exc:
            update_error = exc
        finally:
            if stopped:
                try:
                    job.phase, job.message = "restarting", "正在重新启动后端"
                    await asyncio.to_thread(self.process.start)
                    job.health = await self._wait_for_health(job)
                except Exception as restart_exc:
                    if update_error:
                        raise RuntimeError(f"{update_error}；后端重新启动失败: {restart_exc}") from restart_exc
                    raise
        if update_error:
            raise update_error
        job.message = "插件更新已应用，后端运行正常"
        if job.health and job.health.get("status") == "degraded":
            job.message = "插件更新已应用，后端已启动但存在降级组件"

    async def _run_updater(self, operation_id: str | None, apply_all: bool, *, dry_run: bool) -> list[dict]:
        command = [sys.executable, str(self.backend_root / "scripts" / "apply_plugin_updates.py")]
        command.extend(["--all"] if apply_all else ["--operation-id", str(operation_id)])
        if dry_run:
            command.append("--dry-run")
        environment = os.environ.copy()
        environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        process = await asyncio.create_subprocess_exec(
            *command, cwd=str(self.backend_root), env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        output: list[str] = []
        if process.stdout is not None:
            while line := await process.stdout.readline():
                decoded = line.decode("utf-8", errors="replace").rstrip()
                output.append(decoded)
                self.log.append("updater", decoded)
        code = await process.wait()
        rendered = "\n".join(output).strip()
        if code != 0:
            raise RuntimeError(rendered or f"离线更新器退出码 {code}")
        try:
            result = json.loads(rendered or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("离线更新器返回了无法识别的结果") from exc
        return result if isinstance(result, list) else [result]

    async def _wait_for_health(self, job: ControlJob, timeout: float = 60.0) -> dict:
        job.phase, job.message = "verifying", "正在检查后端健康状态"
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            health = await asyncio.to_thread(self.process.probe_health)
            if health:
                self.process.mark_health(health)
                return health
            snapshot = self.process.snapshot()
            if snapshot["state"] == "failed":
                raise RuntimeError(snapshot["last_error"] or "Uvicorn 启动失败")
            await asyncio.sleep(1)
        self.process.mark_failed("后端健康检查超时")
        raise TimeoutError("后端启动后 60 秒内未通过健康检查")

    @staticmethod
    def _public_plan(plan: dict) -> dict:
        return {
            key: plan.get(key) for key in (
                "operation_id", "plugin_id", "current_version", "target_version",
                "status", "restart_required", "created_at", "error_reason",
            )
        }

    def _trim_jobs(self) -> None:
        completed = [item for item in self.jobs.values() if item.status not in {"queued", "running"}]
        for item in sorted(completed, key=lambda value: value.created_at)[:-50]:
            self.jobs.pop(item.job_id, None)
