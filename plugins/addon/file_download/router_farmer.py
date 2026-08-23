# -*- coding: utf-8 -*-
"""
文件下载插件 — 农户端路由（前缀 /api/v1/file_download）

- 路由级 Depends(check_farmer)：农户登录校验，farmer_id 取 request.state.user_id
- 所有查询与下载均经 file_service.visible_condition 可见性谓词过滤
- 下载顺序：可见性校验 → 原子计数 → 操作日志 → commit 并退出 session →
  事务提交后发布瞬时事件 → FileResponse（下载流不持有数据库连接）
- 端点清单与 spec 6.2 一一对应（3 个）
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from core.auth.middleware_chain import check_farmer
from core.response import ok
from core.db.base import async_session_factory
from core.events import event_bus
from core.log.active_log import active_log
from plugins.addon.file_download.services.file_service import FileService
from plugins.addon.file_download.services.folder_service import FolderService

farmer_router = APIRouter(
    prefix="/api/v1/file_download",
    tags=["文件下载插件（农户端）"],
    dependencies=[Depends(check_farmer)],
)


@farmer_router.get("/folders")
async def farmer_list_folders(request: Request):
    """农户端 — 文件夹列表（仅统计当前农户可见文件数，空夹不返回）"""
    async with async_session_factory() as db:
        folders = await FolderService.list_folders(db)
        counts = await FileService.count_by_folder_for_farmer(db, request.state.user_id)
    return ok({
        "list": [
            {"id": f["id"], "name": f["name"], "file_count": counts[f["id"]]}
            for f in folders
            if counts.get(f["id"], 0) > 0
        ]
    })


@farmer_router.get("/files")
async def farmer_list_files(
    request: Request,
    folder_id: int = Query(0, ge=0, description="文件夹ID，0=全部"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
):
    """农户端 — 可见文件分页列表（hidden=0 且范围命中）"""
    async with async_session_factory() as db:
        data = await FileService.list_files_for_farmer(
            db, request.state.user_id, folder_id, page, limit
        )
    return ok(data)


@farmer_router.get("/files/{file_id}/download")
async def farmer_download_file(file_id: int, request: Request):
    """
    农户端 — 下载文件。
    不可见（隐藏或范围外）返回 403；记录不存在或磁盘文件缺失返回 404。
    """
    farmer_id = request.state.user_id
    async with async_session_factory() as db:
        exists = await FileService.get_file(db, file_id)
        if not exists:
            raise HTTPException(status_code=404, detail="文件不存在")
        row = await FileService.check_farmer_visible(db, file_id, farmer_id)
        if not row:
            raise HTTPException(status_code=403, detail="无权访问该文件")
        path = FileService.get_download_path(row)
        if not path:
            raise HTTPException(status_code=404, detail="文件已失效，请联系管理员")
        origin_name = row.origin_name
        file_name = row.name
        # 下载计数 SQL 端原子自增 + 操作日志，随 session 一并提交
        await FileService.increment_download(db, file_id)
        await active_log(
            f"农户下载文件: {file_name}", "file_download_farmer_download",
            rel_id=file_id, db=db,
        )
        await db.commit()
    # 事务提交后再发布事件，监听方读到的计数一定是已提交数据。
    await event_bus.publish_transient("file_download.downloaded", {
        "file_id": file_id, "farmer_id": farmer_id,
    })
    # session 已退出，下载流不持有数据库连接
    return FileResponse(path, filename=origin_name)
