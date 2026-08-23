# -*- coding: utf-8 -*-
"""
文件下载插件 — 文件夹业务服务

职责：文件夹的增删改查、默认文件夹保护、删除时文件迁移。
所有保护性业务规则（默认夹禁删、设默认时转移标记、删除时文件移入默认夹）
均收敛在本服务内，路由层不做业务判断。
"""
import logging

from sqlalchemy import func, select, update

from plugins.addon.file_download.models import FileDownloadFile, FileDownloadFolder

logger = logging.getLogger(__name__)


class FolderService:
    """文件夹业务服务"""

    @staticmethod
    async def list_folders(db) -> list:
        """
        文件夹列表（含各夹文件计数）。

        固定 2 条 SQL：文件夹全量 + GROUP BY folder_id 一次聚合计数，无 N+1。
        排序：默认文件夹置顶，其余按 ID 升序。
        """
        folders = (await db.execute(
            select(FileDownloadFolder).order_by(
                FileDownloadFolder.is_default.desc(), FileDownloadFolder.id.asc()
            )
        )).scalars().all()

        counts = dict((await db.execute(
            select(FileDownloadFile.folder_id, func.count())
            .group_by(FileDownloadFile.folder_id)
        )).all())

        return [
            {
                "id": f.id,
                "name": f.name,
                "is_default": f.is_default,
                "file_count": counts.get(f.id, 0),
                "create_time": str(f.create_time) if f.create_time else None,
            }
            for f in folders
        ]

    @staticmethod
    async def get_folder(db, folder_id: int):
        """根据 ID 获取文件夹，不存在返回 None"""
        return (await db.execute(
            select(FileDownloadFolder).where(FileDownloadFolder.id == folder_id)
        )).scalar_one_or_none()

    @staticmethod
    async def get_default_folder(db):
        """获取默认文件夹（install.sql 种子保证存在）"""
        return (await db.execute(
            select(FileDownloadFolder).where(FileDownloadFolder.is_default == 1)
        )).scalars().first()

    @staticmethod
    async def create_folder(db, name: str, admin_id: int) -> int:
        """新建文件夹，返回新记录 ID"""
        folder = FileDownloadFolder(name=name, is_default=0, admin_id=admin_id)
        db.add(folder)
        await db.commit()
        await db.refresh(folder)
        return folder.id

    @staticmethod
    async def rename_folder(db, folder_id: int, name: str, admin_id: int) -> bool:
        """重命名文件夹，成功返回 True，记录不存在返回 False"""
        folder = await FolderService.get_folder(db, folder_id)
        if not folder:
            return False
        folder.name = name
        folder.admin_id = admin_id
        await db.commit()
        return True

    @staticmethod
    async def set_default(db, folder_id: int, admin_id: int) -> bool:
        """
        设置默认文件夹（单事务转移标记）：
        先清除全部 is_default 标记，再将目标文件夹置为默认。
        成功返回 True，目标不存在返回 False。
        """
        folder = await FolderService.get_folder(db, folder_id)
        if not folder:
            return False
        await db.execute(update(FileDownloadFolder).values(is_default=0))
        folder.is_default = 1
        folder.admin_id = admin_id
        await db.commit()
        return True

    @staticmethod
    async def delete_folder(db, folder_id: int) -> str:
        """
        删除文件夹。返回错误码字符串，空串表示成功：
        - "not_found"：文件夹不存在
        - "is_default"：默认文件夹禁止删除
        夹内文件批量移入默认文件夹（一条 UPDATE），与删除同事务。
        """
        folder = await FolderService.get_folder(db, folder_id)
        if not folder:
            return "not_found"
        if folder.is_default == 1:
            return "is_default"

        default_folder = await FolderService.get_default_folder(db)
        if default_folder:
            # 夹内文件批量移入默认文件夹，避免出现孤儿文件记录
            await db.execute(
                update(FileDownloadFile)
                .where(FileDownloadFile.folder_id == folder_id)
                .values(folder_id=default_folder.id)
            )
        await db.delete(folder)
        await db.commit()
        return ""
