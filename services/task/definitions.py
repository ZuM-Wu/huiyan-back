"""任务平台的类型化定义、执行上下文与运行时注册表。"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

TaskHandler = Callable[["TaskContext", dict[str, Any]], Awaitable[Any] | Any]


@dataclass(frozen=True)
class TaskContext:
    """传给任务处理器的稳定执行上下文。"""

    task_id: int
    owner: str
    attempt: int
    max_attempts: int
    idempotency_key: str | None = None
    correlation_id: str = ""
    event_id: int | None = None


@dataclass(frozen=True)
class TaskDefinition:
    """任务执行策略；任务名是全局唯一的稳定协议。"""

    name: str
    title: str
    owner: str
    group: str
    handler: TaskHandler
    timeout_seconds: int = 30
    max_attempts: int = 3
    concurrency: int = 1
    backoff: bool = True
    failure_notifications: bool = True

    def validate(self) -> None:
        if not self.name or not self.owner or not self.group:
            raise ValueError("任务 name、owner、group 不能为空")
        if self.timeout_seconds <= 0:
            raise ValueError(f"任务超时必须大于 0: {self.name}")
        if self.max_attempts <= 0:
            raise ValueError(f"任务最大尝试次数必须大于 0: {self.name}")
        if self.concurrency <= 0:
            raise ValueError(f"任务并发数必须大于 0: {self.name}")
        if not callable(self.handler):
            raise ValueError(f"任务处理器不可调用: {self.name}")


class TaskRegistry:
    """集中管理任务定义，禁止不同 owner 或不同处理器静默覆盖。"""

    def __init__(self) -> None:
        self._definitions: dict[str, TaskDefinition] = {}

    def register(self, definition: TaskDefinition) -> None:
        definition.validate()
        current = self._definitions.get(definition.name)
        if current is not None:
            if current == definition:
                return
            raise ValueError(
                f"任务定义冲突: {definition.name} "
                f"({current.owner} -> {definition.owner})"
            )
        self._definitions[definition.name] = definition

    def require(self, name: str) -> TaskDefinition:
        definition = self._definitions.get(name)
        if definition is None:
            raise ValueError(f"任务定义未注册: {name}")
        return definition

    def get(self, name: str) -> TaskDefinition | None:
        return self._definitions.get(name)

    def unregister_owner(self, owner: str) -> list[str]:
        removed = [
            name for name, item in self._definitions.items()
            if item.owner == owner
        ]
        for name in removed:
            self._definitions.pop(name, None)
        return removed

    def definitions(self) -> list[TaskDefinition]:
        return sorted(self._definitions.values(), key=lambda item: item.name)

    def clear(self) -> None:
        """仅供测试和应用重新装载运行时定义使用。"""
        self._definitions.clear()


task_registry = TaskRegistry()
