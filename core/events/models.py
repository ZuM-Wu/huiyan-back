"""事件平台的类型化公共契约。"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel

EventHandler = Callable[[dict[str, Any]], Awaitable[Any] | Any]


@dataclass(frozen=True)
class EventDefinition:
    """描述一个稳定业务事件及其数据边界。"""

    name: str
    title: str
    category: str
    version: int
    owner: str
    payload_model: type[BaseModel]
    delivery: Literal["transient", "durable"]
    replayable: bool = False
    sensitive_fields: frozenset[str] = field(default_factory=frozenset)
    export_fields: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class EventSubscription:
    """事件订阅声明；可靠事件会为每个声明建立独立投递任务。"""

    event_name: str
    owner: str
    handler: EventHandler
    title: str = ""
    timeout_seconds: int = 30
    max_attempts: int = 3
    concurrency: int = 1
    failure_notifications: bool = True

    @property
    def task_name(self) -> str:
        safe_event = self.event_name.replace(".", "_")
        return f"event_{self.owner}_{safe_event}"
