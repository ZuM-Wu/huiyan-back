"""机器可读的事件定义与订阅注册表。"""

import inspect
import logging
from collections import defaultdict

from core.events.models import EventDefinition, EventSubscription

logger = logging.getLogger(__name__)


class EventRegistry:
    """集中管理事件契约，并按 owner 支持插件运行时注销。"""

    def __init__(self) -> None:
        self._definitions: dict[str, EventDefinition] = {}
        self._subscriptions: dict[str, list[EventSubscription]] = defaultdict(list)

    def register_definition(self, definition: EventDefinition) -> None:
        current = self._definitions.get(definition.name)
        if current and current != definition:
            raise ValueError(f"事件定义冲突: {definition.name}")
        self._definitions[definition.name] = definition

    def register_subscription(self, subscription: EventSubscription) -> None:
        definition = self.require_definition(subscription.event_name)
        entries = self._subscriptions[subscription.event_name]
        if any(item.owner == subscription.owner for item in entries):
            raise ValueError(
                f"事件订阅冲突: {subscription.event_name}/{subscription.owner}"
            )
        entries.append(subscription)
        if definition.delivery == "durable":
            self._register_delivery_task(subscription)

    def unregister_owner(self, owner: str) -> None:
        self._definitions = {
            name: item for name, item in self._definitions.items()
            if item.owner != owner
        }
        for event_name in list(self._subscriptions):
            self._subscriptions[event_name] = [
                item for item in self._subscriptions[event_name]
                if item.owner != owner
            ]
            if not self._subscriptions[event_name]:
                self._subscriptions.pop(event_name, None)
        from services.task.definitions import task_registry
        task_registry.unregister_owner(owner)

    def require_definition(self, name: str) -> EventDefinition:
        definition = self._definitions.get(name)
        if not definition:
            raise ValueError(f"事件未登记: {name}")
        return definition

    def subscriptions(self, name: str) -> list[EventSubscription]:
        return list(self._subscriptions.get(name, []))

    def definitions(self) -> list[EventDefinition]:
        return sorted(self._definitions.values(), key=lambda item: item.name)

    def subscription_items(self) -> list[EventSubscription]:
        return [
            item for name in sorted(self._subscriptions)
            for item in self._subscriptions[name]
        ]

    @staticmethod
    def _register_delivery_task(subscription: EventSubscription) -> None:
        from services.task.definitions import TaskDefinition, task_registry

        async def deliver(context, task_data):
            payload = task_data.get("payload") or {}
            result = subscription.handler(payload)
            if inspect.isawaitable(result):
                await result

        task_registry.register(TaskDefinition(
            name=subscription.task_name,
            title=subscription.title or f"事件投递: {subscription.event_name}",
            owner=subscription.owner,
            group=f"event:{subscription.owner}",
            handler=deliver,
            timeout_seconds=subscription.timeout_seconds,
            max_attempts=subscription.max_attempts,
            concurrency=subscription.concurrency,
            failure_notifications=subscription.failure_notifications,
        ))


event_registry = EventRegistry()
