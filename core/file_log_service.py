"""统一文件日志门面，屏蔽插件对 FileLogModel 的直接依赖。"""

import hashlib
import uuid

from sqlalchemy import func, select

from core.db.base import async_session_factory
from core.db.file_log import FileLogModel


def local_path_digest(local_path: str) -> str:
    """返回本地相对路径的固定长度唯一索引值。"""
    return hashlib.sha256(local_path.encode("utf-8")).hexdigest()


async def count_by_oss_method(oss_method: str) -> int:
    """统计指定存储方式下的文件数量。"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count(FileLogModel.id)).where(
                FileLogModel.oss_method == oss_method,
            )
        )
        return int(result.scalar() or 0)


async def ensure_file_log(  # noqa: PLR0917
    save_name: str,
    original_name: str,
    ext: str,
    url: str,
    file_size: int,
    admin_id: int | None,
    source: str,
    oss_method: str = "local_oss",
    local_path: str = "",
    object_key: str = "",
) -> None:
    """幂等写入文件日志。"""
    normalized_local_path = local_path or None
    path_hash = local_path_digest(normalized_local_path) if normalized_local_path else None
    async with async_session_factory() as db:
        existing = (await db.execute(
            select(FileLogModel).where(
                (FileLogModel.local_path_hash == path_hash) if path_hash else
                (FileLogModel.save_name == save_name)
            )
        )).scalar_one_or_none()
        if existing:
            if normalized_local_path and not existing.local_path:
                existing.local_path = normalized_local_path
                existing.local_path_hash = path_hash
                existing.object_key = object_key or existing.object_key
                existing.url = url or existing.url
                existing.oss_method = oss_method or existing.oss_method
                await db.commit()
            return
        db.add(FileLogModel(
            uuid=uuid.uuid4().hex,
            save_name=save_name,
            local_path=normalized_local_path,
            local_path_hash=path_hash,
            object_key=object_key,
            original_name=original_name,
            ext=ext,
            oss_method=oss_method,
            url=url,
            file_size=file_size,
            admin_id=admin_id,
            source=source,
        ))
        await db.commit()


async def update_file_storage(
    local_path: str,
    *,
    oss_method: str,
    object_key: str,
    url: str,
) -> bool:
    """更新文件日志的实际存储归属，返回是否找到记录。"""
    path_hash = local_path_digest(local_path)
    async with async_session_factory() as db:
        row = (await db.execute(
            select(FileLogModel).where(FileLogModel.local_path_hash == path_hash).limit(1)
        )).scalar_one_or_none()
        if not row:
            # 兼容升级前没有 local_path 的日志；URL 精确后缀优先于文件名，
            # 避免不同目录同名文件被错误更新。
            row = (await db.execute(
                select(FileLogModel).where(FileLogModel.url.endswith("/" + local_path))
                    .order_by(FileLogModel.id.desc()).limit(1)
            )).scalar_one_or_none()
        if not row:
            row = (await db.execute(
                select(FileLogModel).where(
                    FileLogModel.local_path.is_(None),
                    FileLogModel.save_name == local_path.rsplit("/", 1)[-1],
                ).order_by(FileLogModel.id.desc()).limit(1)
            )).scalar_one_or_none()
        if not row:
            return False
        row.local_path = local_path
        row.local_path_hash = path_hash
        row.oss_method = oss_method
        row.object_key = object_key
        row.url = url
        await db.commit()
        return True


async def get_file_log(local_path: str):
    """按 upload/ 相对路径查询文件日志。"""
    path_hash = local_path_digest(local_path)
    async with async_session_factory() as db:
        row = (await db.execute(
            select(FileLogModel).where(FileLogModel.local_path_hash == path_hash)
        )).scalar_one_or_none()
        if row:
            return row
        return (await db.execute(
            select(FileLogModel).where(
                FileLogModel.url.contains(f"/upload/{local_path}")
                | FileLogModel.url.contains(f"upload/{local_path}")
            )
                .order_by(FileLogModel.id.desc()).limit(1)
        )).scalar_one_or_none()


async def delete_file_log(object_key: str, oss_method: str | None = None) -> int:
    """按对象键或保存文件名清理文件日志，返回删除条数。"""
    async with async_session_factory() as db:
        query = select(FileLogModel).where(
            (FileLogModel.object_key == object_key)
            | (FileLogModel.save_name == object_key)
            | FileLogModel.url.contains(str(object_key))
        )
        if oss_method:
            query = query.where(FileLogModel.oss_method == oss_method)
        rows = (await db.execute(query)).scalars().all()
        for row in rows:
            await db.delete(row)
        if rows:
            await db.commit()
        return len(rows)


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
