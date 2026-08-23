# -*- coding: utf-8 -*-
"""
文件下载插件 — 文件业务服务（安全与性能核心）

关键设计：
- 流式上传：1MB 分块边写边计数，超限立即中断并删除半成品，不整读内存
- 物理存储：插件私有 upload/ 目录（不在 /upload 无鉴权静态挂载范围内），
  文件名 UUID 重命名，下载必须经鉴权接口 FileResponse 返回
- 可见性谓词：visible_condition() 是唯一的可见性判断构造函数，
  农户端列表 / 文件夹计数 / 下载校验三处共用，规则永不发散
- 下载计数：SQL 端原子自增，禁止 Python 层读改写（并发丢失更新）
"""
import logging
import uuid
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select, update

from core.production_area_service import get_area_ids_by_farmer
from plugins.addon.file_download.models import (
    FileDownloadArea,
    FileDownloadFile,
)
from plugins.addon.file_download.upload_policies import get_policy_definition
from services.upload_policy import (
    UploadPolicyError,
    get_effective_policy,
    stream_upload,
    validate_filename,
)

logger = logging.getLogger(__name__)

# 插件标识（配置键前缀）
PLUGIN_NAME = "file_download"
# 物理文件存储目录（插件私有）
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "upload"
def visible_condition(area_ids: set[int]):
    """
    构造农户可见性 SQL 谓词（唯一可见性判断入口）：
    hidden=0 且（visible_range='all' 或 该农户所属产区命中文件产区关联）。

    产区命中通过 IN 子查询实现：文件产区关联表中的 area_id 命中农户绑定的产区集合。
    area_ids 由 production_area_service.get_area_ids_by_farmer() 预获取，
    避免直接依赖 core.db.production_area.AreaFarmer。
    """
    if area_ids:
        area_hit = FileDownloadFile.id.in_(
            select(FileDownloadArea.file_id).where(
                FileDownloadArea.area_id.in_(area_ids)
            )
        )
    else:
        area_hit = False
    return and_(
        FileDownloadFile.hidden == 0,
        or_(FileDownloadFile.visible_range == "all", area_hit),
    )


class FileService:
    """文件业务服务"""

    # ------------------------------------------------------------------
    # 上传（流式写盘）
    # ------------------------------------------------------------------
    @staticmethod
    async def save_upload(
        db, *, upload_file, name: str, folder_id: int, visible_range: str,
        area_ids: list, description: str, admin_id: int, hidden: int = 0,
    ) -> int:
        """
        保存上传文件并写入数据库记录，返回新文件 ID。

        校验失败抛 ValueError（由路由层转 400）：
        - 扩展名不在白名单（file_download.extensions）
        - 文件大小超上限（file_download.max_size，MB）——
          流式分块边写边计数，超限立即中断并删除半成品
        INSERT 失败时删除已落盘文件，防止孤儿文件。
        """
        policy = await get_effective_policy(get_policy_definition(), db)
        origin_name = upload_file.filename or ""
        try:
            extension = validate_filename(origin_name, policy)
        except UploadPolicyError as exc:
            raise ValueError(str(exc)) from exc
        ext = extension.lstrip(".")

        # UUID 重命名落盘，避免文件名冲突与路径注入
        disk_name = f"{uuid.uuid4().hex}.{ext}"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / disk_name

        try:
            size = await stream_upload(upload_file, dest, policy["max_size_mb"])
        except UploadPolicyError as exc:
            raise ValueError(str(exc)) from exc

        # 写库（失败删除已落盘文件，防孤儿）
        try:
            row = FileDownloadFile(
                folder_id=folder_id,
                name=name or Path(origin_name).stem,
                filename=disk_name,
                origin_name=origin_name,
                filetype=ext,
                filesize=size,
                visible_range=visible_range,
                hidden=hidden,
                download_count=0,
                description=description or "",
                admin_id=admin_id,
            )
            db.add(row)
            await db.flush()
            if visible_range == "area" and area_ids:
                # 批量写产区关联（去重）
                db.add_all([
                    FileDownloadArea(file_id=row.id, area_id=aid)
                    for aid in sorted(set(area_ids))
                ])
            await db.commit()
            await db.refresh(row)
            return row.id
        except Exception:
            dest.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @staticmethod
    async def get_file(db, file_id: int):
        """根据 ID 获取文件记录，不存在返回 None"""
        return (await db.execute(
            select(FileDownloadFile).where(FileDownloadFile.id == file_id)
        )).scalar_one_or_none()

    @staticmethod
    async def list_files(db, keyword: str, folder_id: int, page: int, limit: int) -> dict:
        """管理端文件分页列表 — 支持文件夹过滤与名称关键词搜索，附带产区关联"""
        query = select(FileDownloadFile)
        if folder_id:
            query = query.where(FileDownloadFile.folder_id == folder_id)
        if keyword:
            query = query.where(
                FileDownloadFile.name.contains(keyword)
                | FileDownloadFile.origin_name.contains(keyword)
            )

        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            query.order_by(FileDownloadFile.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        # 一次批量取本页文件的产区关联（编辑弹窗回显用），无 N+1
        area_map: dict = {}
        if rows:
            pairs = (await db.execute(
                select(FileDownloadArea.file_id, FileDownloadArea.area_id)
                .where(FileDownloadArea.file_id.in_([r.id for r in rows]))
            )).all()
            for fid, aid in pairs:
                area_map.setdefault(fid, []).append(aid)

        return {
            "total": total, "page": page, "limit": limit,
            "list": [FileService.to_dict(r, area_map.get(r.id, [])) for r in rows],
        }

    @staticmethod
    async def list_files_for_farmer(
        db, farmer_id: int, folder_id: int, page: int, limit: int,
    ) -> dict:
        """农户端文件分页列表 — 仅返回该农户可见文件（共用可见性谓词）"""
        area_ids = set(await get_area_ids_by_farmer(farmer_id))
        query = select(FileDownloadFile).where(visible_condition(area_ids))
        if folder_id:
            query = query.where(FileDownloadFile.folder_id == folder_id)

        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            query.order_by(FileDownloadFile.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()
        return {
            "total": total, "page": page, "limit": limit,
            "list": [FileService.to_public_dict(r) for r in rows],
        }

    @staticmethod
    async def count_by_folder_for_farmer(db, farmer_id: int) -> dict:
        """农户端各文件夹可见文件计数 — 一次 GROUP BY 聚合（共用可见性谓词）"""
        area_ids = set(await get_area_ids_by_farmer(farmer_id))
        pairs = (await db.execute(
            select(FileDownloadFile.folder_id, func.count())
            .where(visible_condition(area_ids))
            .group_by(FileDownloadFile.folder_id)
        )).all()
        return dict(pairs)

    # ------------------------------------------------------------------
    # 编辑 / 删除 / 显隐
    # ------------------------------------------------------------------
    @staticmethod
    async def update_file(
        db, file_id: int, name: str, folder_id: int, visible_range: str,
        area_ids: list, description: str, admin_id: int,
    ) -> bool:
        """
        编辑文件元信息（不更换物理文件）。
        产区关联采用先 DELETE 后重建策略，与字段更新同事务提交。
        成功返回 True，记录不存在返回 False。
        """
        row = await FileService.get_file(db, file_id)
        if not row:
            return False
        row.name = name
        row.folder_id = folder_id
        row.visible_range = visible_range
        row.description = description or ""
        row.admin_id = admin_id

        # 先清后建产区关联（单事务，避免残留旧关联）
        await db.execute(
            delete(FileDownloadArea).where(FileDownloadArea.file_id == file_id)
        )
        if visible_range == "area" and area_ids:
            db.add_all([
                FileDownloadArea(file_id=file_id, area_id=aid)
                for aid in sorted(set(area_ids))
            ])
        await db.commit()
        return True

    @staticmethod
    async def delete_file(db, file_id: int) -> bool:
        """删除文件：数据记录 + 产区关联 + 物理文件。成功 True，不存在 False"""
        row = await FileService.get_file(db, file_id)
        if not row:
            return False
        disk_name = row.filename
        await db.execute(
            delete(FileDownloadArea).where(FileDownloadArea.file_id == file_id)
        )
        await db.delete(row)
        await db.commit()
        # 事务提交成功后再删物理文件（删失败仅告警，不影响数据一致性）
        try:
            (UPLOAD_DIR / disk_name).unlink(missing_ok=True)
        except OSError:
            logger.warning("[file_download] 物理文件删除失败: %s", disk_name)
        return True

    @staticmethod
    async def toggle_hidden(db, file_id: int, hidden: int) -> bool:
        """切换文件显示/隐藏，成功返回 True，记录不存在返回 False"""
        row = await FileService.get_file(db, file_id)
        if not row:
            return False
        row.hidden = hidden
        await db.commit()
        return True

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------
    @staticmethod
    def get_download_path(row) -> Path:
        """返回文件物理路径，物理文件缺失返回 None（由路由层转 404）"""
        path = UPLOAD_DIR / row.filename
        return path if path.is_file() else None

    @staticmethod
    async def check_farmer_visible(db, file_id: int, farmer_id: int):
        """
        农户下载前校验：文件存在且对该农户可见（共用可见性谓词）。
        返回文件记录，无权或不存在返回 None。
        """
        area_ids = set(await get_area_ids_by_farmer(farmer_id))
        return (await db.execute(
            select(FileDownloadFile).where(
                FileDownloadFile.id == file_id, visible_condition(area_ids)
            )
        )).scalar_one_or_none()

    @staticmethod
    async def increment_download(db, file_id: int) -> None:
        """下载计数 SQL 端原子自增（禁止 ORM 读改写，避免并发丢失更新）"""
        await db.execute(
            update(FileDownloadFile)
            .where(FileDownloadFile.id == file_id)
            .values(download_count=FileDownloadFile.download_count + 1)
        )

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    @staticmethod
    def to_dict(row, area_ids: list) -> dict:
        """管理端序列化（含产区关联与磁盘文件名）"""
        return {
            "id": row.id,
            "folder_id": row.folder_id,
            "name": row.name,
            "origin_name": row.origin_name,
            "filetype": row.filetype,
            "filesize": row.filesize,
            "visible_range": row.visible_range,
            "area_ids": area_ids,
            "hidden": row.hidden,
            "download_count": row.download_count,
            "description": row.description,
            "create_time": str(row.create_time) if row.create_time else None,
            "update_time": str(row.update_time) if row.update_time else None,
        }

    @staticmethod
    def to_public_dict(row) -> dict:
        """农户端序列化（不暴露磁盘文件名 / 可见范围等管理字段）"""
        return {
            "id": row.id,
            "folder_id": row.folder_id,
            "name": row.name,
            "filetype": row.filetype,
            "filesize": row.filesize,
            "download_count": row.download_count,
            "description": row.description,
            "create_time": str(row.create_time) if row.create_time else None,
        }
