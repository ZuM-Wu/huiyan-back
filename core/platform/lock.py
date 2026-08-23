"""单进程平台锁门面。

当前项目约束为单 Uvicorn worker，因此使用可诊断的 asyncio.Lock 足够覆盖主题和插件更新。
后续切换多进程时只需替换本门面，不让业务代码直接依赖锁实现。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime


@dataclass
class LockState:
    key: str
    owner: str
    acquired_at: datetime


class PlatformLock:
    """按稳定 key 管理进程内互斥锁。"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._states: dict[str, LockState] = {}

    def _get(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def acquire(self, key: str, owner: str, timeout: float = 10.0) -> bool:
        """尝试获取锁；超时返回 False，不伪装成成功。"""
        if not key or not owner:
            raise ValueError("锁 key 和 owner 不能为空")
        lock = self._get(key)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=max(timeout, 0.01))
        except asyncio.TimeoutError:
            return False
        self._states[key] = LockState(key, owner, datetime.now())
        return True

    def release(self, key: str, owner: str) -> None:
        """释放锁；非持有者不得释放。"""
        state = self._states.get(key)
        if not state or state.owner != owner:
            raise RuntimeError(f"锁不属于 owner: {key}")
        self._states.pop(key, None)
        lock = self._get(key)
        if lock.locked():
            lock.release()

    def inspect(self, key: str) -> dict:
        """返回锁诊断信息，不暴露底层 Lock 对象。"""
        state = self._states.get(key)
        return {
            "key": key,
            "locked": bool(state),
            "owner": state.owner if state else "",
            "acquired_at": state.acquired_at.isoformat() if state else "",
        }


platform_lock = PlatformLock()
