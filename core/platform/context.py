"""请求、任务和更新操作的轻量追踪上下文。"""

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class PlatformContext:
    """跨请求边界传递的最小审计字段。"""

    request_id: str = ""
    identity: str = ""
    owner: str = ""
    version: str = ""
    operation_id: str = ""
    phase: str = ""


_context: ContextVar[PlatformContext] = ContextVar(
    "huiyan_platform_context", default=PlatformContext()
)


def current_context() -> PlatformContext:
    """读取当前上下文；没有绑定上下文时返回空对象。"""
    return _context.get()


def bind_context(**values: str) -> Token:
    """绑定或覆盖指定字段，返回可传给 clear_context 的令牌。"""
    current = current_context()
    allowed = {
        key: value for key, value in values.items()
        if key in PlatformContext.__dataclass_fields__ and value is not None
    }
    return _context.set(replace(current, **allowed))


def clear_context(token: Token | None = None) -> None:
    """恢复绑定前的上下文，避免请求字段泄露到后续任务。"""
    if token is not None:
        _context.reset(token)
    else:
        _context.set(PlatformContext())
