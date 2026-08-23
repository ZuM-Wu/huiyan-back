"""统一文件日志门面，屏蔽插件对 FileLogModel 的直接依赖。"""

import uuid

from sqlalchemy import func, select

from core.db.base import async_session_factory
from core.db.file_log import FileLogModel


async def count_by_oss_method(oss_method: str) -> int:
    """统计指定存储方式下的文件数量。"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count(FileLogModel.id)).where(
                FileLogModel.oss_method == oss_method,
            )
        )
        return int(result.scalar() or 0)


async def ensure_file_log(
    save_name: str,
    original_name: str,
    ext: str,
    url: str,
    file_size: int,
    admin_id: int | None,
    source: str,
) -> None:
    """幂等写入文件日志。"""
    async with async_session_factory() as db:
        existing = (await db.execute(
            select(FileLogModel.id).where(FileLogModel.save_name == save_name)
        )).first()
        if existing:
            return
        db.add(FileLogModel(
            uuid=uuid.uuid4().hex,
            save_name=save_name,
            original_name=original_name,
            ext=ext,
            oss_method="local_oss",
            url=url,
            file_size=file_size,
            admin_id=admin_id,
            source=source,
        ))
        await db.commit()


async def find_file_url(file_id: str) -> str | None:
    """按 UUID 或存储文件名查询文件访问地址。"""
    async with async_session_factory() as db:
        record = (await db.execute(
            select(FileLogModel.url).where(FileLogModel.uuid == file_id)
        )).scalar_one_or_none()
        if record:
            return record
        return (await db.execute(
            select(FileLogModel.url).where(FileLogModel.save_name == file_id)
        )).scalar_one_or_none()
