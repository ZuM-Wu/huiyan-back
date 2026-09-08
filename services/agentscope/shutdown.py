# -*- coding: utf-8 -*-
"""AgentScope 长连接的进程关闭协调。

Uvicorn 在优雅关闭阶段会先停止接收新连接，再等待现有 HTTP 连接结束。
AgentScope 会话事件流是永久 SSE，如果应用不主动结束该响应，Windows reload
子进程和 Ctrl+C 都会无限停留在等待连接阶段。本模块只负责把进程关闭信号
传递给这些长连接，不改变模型任务的会话中断语义。
"""
from __future__ import annotations

import asyncio
import logging
import signal
import threading
from contextlib import contextmanager
from types import FrameType
from typing import Any, Iterator

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class ProcessShutdownCoordinator:
    """把同步进程信号广播给当前事件循环中的 SSE 请求。"""

    def __init__(self) -> None:
        self._requested = False
        self._waiters: set[asyncio.Event] = set()

    @property
    def requested(self) -> bool:
        """返回当前进程是否已经收到关闭请求。"""
        return self._requested

    def reset(self) -> None:
        """为一次新的应用生命周期清空关闭状态。"""
        self._requested = False

    def request_shutdown(self) -> None:
        """发布关闭状态，并唤醒所有正在等待的长连接。"""
        self._requested = True
        for waiter in tuple(self._waiters):
            waiter.set()

    def subscribe(self) -> asyncio.Event:
        """订阅关闭事件；订阅发生较晚时也不会遗漏既有信号。"""
        waiter = asyncio.Event()
        if self._requested:
            waiter.set()
        self._waiters.add(waiter)
        return waiter

    def unsubscribe(self, waiter: asyncio.Event) -> None:
        """移除已经结束的请求，避免跨生命周期持有事件对象。"""
        self._waiters.discard(waiter)


process_shutdown = ProcessShutdownCoordinator()


class ShutdownSignalProxy:
    """在不替换 Uvicorn 行为的前提下代理进程关闭信号。"""

    def __init__(self, coordinator: ProcessShutdownCoordinator) -> None:
        self._coordinator = coordinator
        self._handlers: dict[int, tuple[Any, Any]] = {}
        self._depth = 0

    @contextmanager
    def installed(self) -> Iterator[None]:
        """安装幂等信号代理，并在最外层生命周期退出时恢复原处理器。"""
        self._depth += 1
        outermost = self._depth == 1
        if outermost:
            self._install()
        try:
            yield
        finally:
            self._depth -= 1
            if outermost:
                self._restore()

    def _install(self) -> None:
        self._coordinator.reset()
        if threading.current_thread() is not threading.main_thread():
            logger.warning("AgentScope 关闭信号代理只能在主线程安装，当前已跳过")
            return

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(signal_number)
            # 正常 Uvicorn 生命周期中原处理器是 Server.handle_exit。若应用在
            # 非服务器环境直接进入 lifespan，不覆盖系统默认动作。
            if not callable(previous):
                logger.warning(
                    "AgentScope 关闭信号代理未安装: signal=%s handler=%r",
                    signal_number,
                    previous,
                )
                continue

            def proxy(
                signum: int,
                frame: FrameType | None,
                original: Any = previous,
            ) -> None:
                self._coordinator.request_shutdown()
                original(signum, frame)

            signal.signal(signal_number, proxy)
            self._handlers[signal_number] = (previous, proxy)

    def _restore(self) -> None:
        for signal_number, (previous, proxy) in self._handlers.items():
            # 只恢复仍由本实例持有的处理器，避免覆盖运行期间由宿主安装的新处理器。
            if signal.getsignal(signal_number) is proxy:
                signal.signal(signal_number, previous)
        self._handlers.clear()
        self._coordinator.reset()


shutdown_signal_proxy = ShutdownSignalProxy(process_shutdown)


def _is_session_stream(scope: Scope) -> bool:
    """限定到 AgentScope 子应用内的会话 SSE，普通接口不参与排空。"""
    if scope.get("type") != "http" or scope.get("method") != "GET":
        return False
    path = str(scope.get("path") or "")
    root_path = str(scope.get("root_path") or "").rstrip("/")
    # Starlette 挂载子应用时会通过 root_path 标记挂载前缀，但不同版本可能
    # 保留完整 scope.path。先剥离前缀，确保 /api/ai 下的真实请求与单独运行
    # AgentScope 子应用时采用同一条路径判定。
    if root_path and (path == root_path or path.startswith(f"{root_path}/")):
        path = path[len(root_path):] or "/"
    path = path.rstrip("/")
    return path.startswith("/sessions/") and path.endswith("/stream")


class ShutdownDrainMiddleware:
    """收到进程关闭信号时立即结束 AgentScope 会话 SSE。"""

    def __init__(
        self,
        app: ASGIApp,
        coordinator: ProcessShutdownCoordinator = process_shutdown,
    ) -> None:
        self.app = app
        self.coordinator = coordinator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_session_stream(scope):
            await self.app(scope, receive, send)
            return

        response_started = False
        response_complete = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started, response_complete
            if message["type"] == "http.response.start":
                response_started = True
            elif (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                response_complete = True
            await send(message)

        waiter = self.coordinator.subscribe()
        async def run_stream() -> None:
            """把通用 ASGI Awaitable 收敛为 asyncio 可跟踪的协程任务。"""
            await self.app(scope, receive, tracked_send)

        stream_task: asyncio.Task[None] = asyncio.create_task(
            run_stream(),
            name="agentscope-sse-response",
        )
        shutdown_task: asyncio.Task[bool] = asyncio.create_task(
            waiter.wait(),
            name="agentscope-sse-shutdown-waiter",
        )
        try:
            done, _ = await asyncio.wait(
                {stream_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stream_task in done:
                shutdown_task.cancel()
                await asyncio.gather(shutdown_task, return_exceptions=True)
                await stream_task
                return

            stream_task.cancel()
            result = (await asyncio.gather(stream_task, return_exceptions=True))[0]
            if isinstance(result, BaseException) and not isinstance(
                result,
                asyncio.CancelledError,
            ):
                logger.warning(
                    "AgentScope SSE 排空时流任务异常: exception_type=%s",
                    type(result).__name__,
                )

            try:
                if response_started and not response_complete:
                    await send({
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    })
                elif not response_started:
                    await send({
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b"Service is shutting down",
                        "more_body": False,
                    })
            except (OSError, RuntimeError):
                # 客户端可能与进程信号同时断开，此时连接已经达到排空目的。
                logger.debug("AgentScope SSE 客户端已在关闭排空期间断开")
        finally:
            for task in (stream_task, shutdown_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stream_task, shutdown_task, return_exceptions=True)
            self.coordinator.unsubscribe(waiter)


__all__ = [
    "ProcessShutdownCoordinator",
    "ShutdownDrainMiddleware",
    "ShutdownSignalProxy",
    "process_shutdown",
    "shutdown_signal_proxy",
]
