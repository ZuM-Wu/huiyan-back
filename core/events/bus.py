"""瞬时事件与事务 Outbox 可靠事件发布门面。"""

import asyncio
import inspect
import json
import logging
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from core.db.event_outbox import EventOutbox
from core.events.registry import event_registry

logger = logging.getLogger(__name__)


class EventBus:
    async def publish_transient(self, name: str, payload: dict[str, Any]) -> list[dict]:
        definition = event_registry.require_definition(name)
        if definition.delivery != "transient":
            raise ValueError(f"可靠事件必须使用 publish_durable: {name}")
        clean = self._validate_payload(definition, payload)
        tasks = [
            self._invoke(name, item.owner, item.handler, deepcopy(clean))
            for item in event_registry.subscriptions(name)
        ]
        return await asyncio.gather(*tasks) if tasks else []

    async def publish_durable(
        self, name: str, payload: dict[str, Any], db, correlation_id: str = "",
    ) -> int:
        definition = event_registry.require_definition(name)
        if definition.delivery != "durable":
            raise ValueError(f"瞬时事件必须使用 publish_transient: {name}")
        clean = self._validate_payload(definition, payload)
        row = EventOutbox(
            event_name=name,
            event_version=definition.version,
            owner=definition.owner,
            payload=json.dumps(clean, ensure_ascii=False, sort_keys=True),
            correlation_id=correlation_id,
            status="Pending",
            create_time=datetime.now(),
        )
        db.add(row)
        await db.flush()
        return int(row.id)

    @staticmethod
    async def _invoke(name, owner, handler, payload) -> dict:
        started = time.perf_counter()
        try:
            result = handler(payload)
            if inspect.isawaitable(result):
                result = await result
            return {
                "event": name, "owner": owner, "success": True,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "result": result,
            }
        except Exception as exc:
            logger.exception("[EventBus] 瞬时事件处理失败: event=%s owner=%s", name, owner)
            return {
                "event": name, "owner": owner, "success": False,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "error": str(exc),
            }

    @staticmethod
    def _validate_payload(definition, payload: dict[str, Any]) -> dict[str, Any]:
        model = definition.payload_model.model_validate(payload)
        return model.model_dump(mode="json")


event_bus = EventBus()
