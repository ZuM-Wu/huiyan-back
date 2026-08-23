"""一次性清理未上线环境的历史站内信数据。

本脚本不会被迁移注册表或应用启动流程调用，只能显式执行：
    python -m migrations.clear_inbox_messages
"""
import asyncio

from sqlalchemy import delete, func, select

from core.db.base import async_session_factory, engine
from core.db.notice import InboxMessage


async def clear_inbox_messages() -> int:
    """事务删除全部站内信，并在提交前确认剩余数量为零。"""
    async with async_session_factory() as db:
        before = (await db.execute(
            select(func.count()).select_from(InboxMessage)
        )).scalar() or 0
        print(f"[站内信清理] 清理前记录数：{before}")
        try:
            result = await db.execute(delete(InboxMessage))
            remaining = (await db.execute(
                select(func.count()).select_from(InboxMessage)
            )).scalar() or 0
            if remaining != 0:
                raise RuntimeError(f"站内信清理校验失败，仍有 {remaining} 条记录")
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    print(f"[站内信清理] 已删除 {result.rowcount} 条，清理后记录数：0")
    return result.rowcount


if __name__ == "__main__":
    async def main() -> None:
        try:
            await clear_inbox_messages()
        finally:
            await engine.dispose()

    asyncio.run(main())
