"""串行、可修改、可拒绝的业务处理管道。"""

import inspect
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

PipelineCallable = Callable[[dict[str, Any]], Awaitable[Any] | Any]


@dataclass(frozen=True)
class PipelineDecision:
    """管道节点决策；拒绝后立即停止后续节点。"""

    allowed: bool
    context: dict[str, Any]
    reason: str = ""


@dataclass(frozen=True)
class PipelineHandler:
    name: str
    owner: str
    handler: PipelineCallable
    priority: int = 100


class PipelineEngine:
    def __init__(self) -> None:
        self._handlers: dict[str, list[PipelineHandler]] = {}

    def register(self, declaration: PipelineHandler) -> None:
        entries = self._handlers.setdefault(declaration.name, [])
        if any(item.owner == declaration.owner for item in entries):
            raise ValueError(f"管道处理器冲突: {declaration.name}/{declaration.owner}")
        entries.append(declaration)
        entries.sort(key=lambda item: item.priority)

    def unregister_owner(self, owner: str) -> None:
        for name in list(self._handlers):
            self._handlers[name] = [
                item for item in self._handlers[name] if item.owner != owner
            ]
            if not self._handlers[name]:
                self._handlers.pop(name, None)

    async def run(self, name: str, context: dict[str, Any]) -> PipelineDecision:
        current = deepcopy(context)
        for declaration in self._handlers.get(name, []):
            try:
                result = declaration.handler(deepcopy(current))
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                return PipelineDecision(False, current, f"{declaration.owner}: {exc}")
            if isinstance(result, PipelineDecision):
                if not result.allowed:
                    return result
                current = deepcopy(result.context)
            elif isinstance(result, dict):
                current = deepcopy(result)
            elif result is False:
                return PipelineDecision(False, current, declaration.owner)
        return PipelineDecision(True, current)

    def handlers(self) -> list[PipelineHandler]:
        return [item for entries in self._handlers.values() for item in entries]


pipeline_engine = PipelineEngine()
