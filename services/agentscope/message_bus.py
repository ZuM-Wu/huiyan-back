# -*- coding: utf-8 -*-
"""基于 MySQL 的 AgentScope MessageBus 适配器。

MySQL 负责保存队列、回放日志、租约和注册表；进程内订阅者使用 asyncio
队列降低 SSE 延迟。需要跨进程实时通知的场景先写入持久化事件，再由订阅者
轮询回放日志，因此 Redis 不是默认运行时依赖。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from core.time_utils import china_now
from typing import Any

from agentscope.app.message_bus import MessageBus, MessageBusKeys
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError

from core.db.base import async_session_factory


class MySQLMessageBus(MessageBus):
    """AgentScope MessageBus 的 MySQL 8 实现。"""

    def __init__(self, *, poll_interval: float = 0.5) -> None:
        self.poll_interval = poll_interval
        self._subscribers: dict[str, set[asyncio.Queue[dict | None]]] = defaultdict(set)
        self._tokens: dict[str, str] = {}
        self._closed = False

    async def __aenter__(self) -> "MySQLMessageBus":
        self._closed = False
        return self

    async def aclose(self) -> None:
        self._closed = True
        for subscribers in self._subscribers.values():
            for queue in subscribers:
                queue.put_nowait(None)
        self._subscribers.clear()

    async def _append(self, key: str, mode: str, payload: dict, ttl_secs: int | None = None) -> str:
        expires = None
        if ttl_secs:
            expires = china_now() + timedelta(seconds=ttl_secs)
        async with async_session_factory() as db:
            result = await db.execute(text(
                "INSERT INTO hy_agentscope_bus_entry(bus_key, mode, payload, expires_at) "
                "VALUES (:key, :mode, :payload, :expires_at)"
            ), {
                "key": key,
                "mode": mode,
                "payload": json.dumps(payload, ensure_ascii=False),
                "expires_at": expires,
            })
            await db.commit()
            return str(getattr(result, "lastrowid", ""))

    @staticmethod
    def _decode(row: Any) -> dict:
        payload = row[1] if not hasattr(row, "_mapping") else row._mapping["payload"]
        if isinstance(payload, str):
            return json.loads(payload)
        return payload or {}

    async def queue_push(self, key: str, payload: dict, *, ttl_secs: int | None = None) -> str:
        return await self._append(key, "queue", payload, ttl_secs)

    async def queue_drain(self, key: str, max_count: int = 100) -> list[tuple[str, dict]]:
        async with async_session_factory() as db:
            result = await db.execute(text(
                "SELECT id, payload FROM hy_agentscope_bus_entry "
                "WHERE bus_key=:key AND mode='queue' "
                "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP) "
                "ORDER BY id LIMIT :limit FOR UPDATE"
            ), {"key": key, "limit": max_count})
            rows = result.all()
            ids = [row[0] for row in rows]
            if ids:
                stmt = text(
                    "DELETE FROM hy_agentscope_bus_entry WHERE id IN :ids"
                ).bindparams(bindparam("ids", expanding=True))
                await db.execute(stmt, {"ids": ids})
            await db.commit()
        return [(str(row[0]), json.loads(row[1]) if isinstance(row[1], str) else row[1]) for row in rows]

    async def queue_delete(self, key: str) -> None:
        async with async_session_factory() as db:
            await db.execute(text(
                "DELETE FROM hy_agentscope_bus_entry WHERE bus_key=:key AND mode='queue'"
            ), {"key": key})
            await db.commit()

    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
        max_len: int | None = None,
    ) -> str:
        entry_id = await self._append(key, "log", payload, ttl_secs)
        if max_len:
            async with async_session_factory() as db:
                await db.execute(text(
                    "DELETE FROM hy_agentscope_bus_entry WHERE bus_key=:key AND mode='log' "
                    "AND id < (SELECT cutoff FROM (SELECT id AS cutoff FROM hy_agentscope_bus_entry "
                    "WHERE bus_key=:key2 AND mode='log' ORDER BY id DESC LIMIT 1 OFFSET :offset) x)"
                ), {"key": key, "key2": key, "offset": max_len - 1})
                await db.commit()
        return entry_id

    async def log_read(self, key: str, since: str | None = None, max_count: int = 100) -> list[tuple[str, dict]]:
        async with async_session_factory() as db:
            conditions = "bus_key=:key AND mode='log' AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)"
            params: dict[str, Any] = {"key": key, "limit": max_count}
            if since and since.isdigit():
                conditions += " AND id > :since"
                params["since"] = int(since)
            result = await db.execute(text(
                f"SELECT id, payload FROM hy_agentscope_bus_entry WHERE {conditions} "
                "ORDER BY id LIMIT :limit"
            ), params)
            rows = result.all()
        return [(str(row[0]), json.loads(row[1]) if isinstance(row[1], str) else row[1]) for row in rows]

    async def log_trim(self, key: str, before_id: str | None = None) -> None:
        async with async_session_factory() as db:
            if before_id and before_id.isdigit():
                await db.execute(text(
                    "DELETE FROM hy_agentscope_bus_entry WHERE bus_key=:key AND mode='log' AND id < :id"
                ), {"key": key, "id": int(before_id)})
            else:
                await db.execute(text(
                    "DELETE FROM hy_agentscope_bus_entry WHERE bus_key=:key AND mode='log'"
                ), {"key": key})
            await db.commit()

    async def publish(self, key: str, payload: dict) -> None:
        entry_id = await self._append(key, "broadcast", payload, 300)
        payload = {**payload, "_entry_id": entry_id}
        for queue in tuple(self._subscribers.get(key, ())):
            queue.put_nowait(payload)

    async def _broadcast_since(self, key: str, since: int) -> list[tuple[int, dict]]:
        """从 MySQL 轮询当前进程之外发布的广播事件。"""
        async with async_session_factory() as db:
            result = await db.execute(text(
                "SELECT id, payload FROM hy_agentscope_bus_entry "
                "WHERE bus_key=:key AND mode='broadcast' AND id>:since "
                "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP) "
                "ORDER BY id LIMIT 100"
            ), {"key": key, "since": since})
            rows = result.all()
        values = []
        for row in rows:
            payload = json.loads(row[1]) if isinstance(row[1], str) else row[1]
            values.append((int(row[0]), payload or {}))
        return values

    async def _broadcast_cursor(self, key: str) -> int:
        async with async_session_factory() as db:
            result = await db.execute(text(
                "SELECT COALESCE(MAX(id), 0) FROM hy_agentscope_bus_entry "
                "WHERE bus_key=:key AND mode='broadcast'"
            ), {"key": key})
            return int(result.scalar() or 0)

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        # 先记录数据库游标再注册本地队列，随后由轮询覆盖跨进程发布窗口。
        cursor = await self._broadcast_cursor(key)
        self._subscribers[key].add(queue)
        if on_ready:
            on_ready()
        try:
            # AgentScope 会话 SSE 路由没有传入 on_ready，先向该订阅者发送
            # 不落库的标准 CUSTOM 事件，让浏览器确认游标和本地队列均已就绪。
            if on_ready is None and key.startswith(MessageBusKeys.session_events("")):
                yield {
                    "type": "CUSTOM",
                    "name": "huiyan_stream_ready",
                    "value": None,
                }
            while not self._closed:
                try:
                    payload = await asyncio.wait_for(
                        queue.get(), timeout=self.poll_interval,
                    )
                    if payload is None:
                        return
                    raw_id = str(payload.get("_entry_id") or "")
                    if raw_id.isdigit():
                        cursor = max(cursor, int(raw_id))
                    yield payload
                except asyncio.TimeoutError:
                    for entry_id, payload in await self._broadcast_since(key, cursor):
                        cursor = max(cursor, entry_id)
                        yield {**payload, "_entry_id": str(entry_id)}
        finally:
            self._subscribers[key].discard(queue)

    @staticmethod
    def _expired(expires_at: datetime | None) -> bool:
        return expires_at is not None and expires_at <= china_now()

    async def try_lock(self, key: str, *, ttl_secs: int = 600) -> bool:
        token = uuid.uuid4().hex
        expires = china_now() + timedelta(seconds=ttl_secs)
        async with async_session_factory() as db:
            await db.execute(text(
                "DELETE FROM hy_agentscope_bus_lock "
                "WHERE expires_at <= CURRENT_TIMESTAMP"
            ))
            try:
                await db.execute(text(
                    "INSERT INTO hy_agentscope_bus_lock(lock_key, token, expires_at) "
                    "VALUES (:key, :token, :expires)"
                ), {"key": key, "token": token, "expires": expires})
            except IntegrityError:
                await db.rollback()
                return False
            await db.commit()
        self._tokens[key] = token
        return True

    async def unlock(self, key: str) -> None:
        token = self._tokens.pop(key, "")
        async with async_session_factory() as db:
            await db.execute(text(
                "DELETE FROM hy_agentscope_bus_lock WHERE lock_key=:key AND token=:token"
            ), {"key": key, "token": token})
            await db.commit()

    async def is_locked(self, key: str) -> bool:
        async with async_session_factory() as db:
            result = await db.execute(text(
                "SELECT COUNT(*) FROM hy_agentscope_bus_lock "
                "WHERE lock_key=:key AND expires_at > CURRENT_TIMESTAMP"
            ), {"key": key})
            return bool(result.scalar())

    @asynccontextmanager
    async def acquire_lock(self, key: str, *, ttl_secs: int = 600) -> AsyncGenerator[None, None]:
        while not await self.try_lock(key, ttl_secs=ttl_secs):
            await asyncio.sleep(min(self.poll_interval, 1.0))
        token = self._tokens.get(key, "")
        stop_renew = asyncio.Event()

        async def renew() -> None:
            interval = max(1.0, min(ttl_secs / 3, 30.0))
            while not stop_renew.is_set():
                try:
                    await asyncio.wait_for(stop_renew.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    expires = china_now() + timedelta(seconds=ttl_secs)
                    async with async_session_factory() as db:
                        await db.execute(text(
                            "UPDATE hy_agentscope_bus_lock SET expires_at=:expires "
                            "WHERE lock_key=:key AND token=:token"
                        ), {"key": key, "token": token, "expires": expires})
                        await db.commit()

        renew_task = asyncio.create_task(renew(), name=f"agentscope-lock:{key}")
        try:
            yield
        finally:
            stop_renew.set()
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass
            await self.unlock(key)

    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        expires = china_now() + timedelta(seconds=ttl_secs) if ttl_secs else None
        async with async_session_factory() as db:
            await db.execute(text(
                "INSERT INTO hy_agentscope_bus_registry(namespace, field_name, field_value, expires_at) "
                "VALUES (:namespace, :field, :value, :expires) "
                "ON DUPLICATE KEY UPDATE field_value=:value2, expires_at=:expires2"
            ), {
                "namespace": namespace, "field": field, "value": value,
                "expires": expires, "value2": value, "expires2": expires,
            })
            await db.commit()

    async def registry_get(self, namespace: str, field: str) -> str | None:
        async with async_session_factory() as db:
            result = await db.execute(text(
                "SELECT field_value FROM hy_agentscope_bus_registry "
                "WHERE namespace=:namespace AND field_name=:field "
                "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)"
            ), {"namespace": namespace, "field": field})
            row = result.first()
            return row[0] if row else None

    async def registry_getall(self, namespace: str) -> dict[str, str]:
        async with async_session_factory() as db:
            result = await db.execute(text(
                "SELECT field_name, field_value FROM hy_agentscope_bus_registry "
                "WHERE namespace=:namespace AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)"
            ), {"namespace": namespace})
            return {row[0]: row[1] for row in result.all()}

    async def registry_exists(self, namespace: str, field: str) -> bool:
        return await self.registry_get(namespace, field) is not None

    async def registry_del(self, namespace: str, field: str) -> None:
        async with async_session_factory() as db:
            await db.execute(text(
                "DELETE FROM hy_agentscope_bus_registry WHERE namespace=:namespace AND field_name=:field"
            ), {"namespace": namespace, "field": field})
            await db.commit()

    async def registry_drop(self, namespace: str) -> None:
        async with async_session_factory() as db:
            await db.execute(text(
                "DELETE FROM hy_agentscope_bus_registry WHERE namespace=:namespace"
            ), {"namespace": namespace})
            await db.commit()
