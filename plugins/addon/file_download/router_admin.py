# -*- coding: utf-8 -*-
"""
文件下载插件 — 管理员端路由（前缀 /api/admin/v1/file_download）

- 路由级 Depends(check_admin) 登录校验 + 接口级 require_permission 细粒度权限
- 业务逻辑全部委托 services 层，handler 保持简洁
- 写操作记录 active_log 操作日志
- 端点清单与 spec 6.1 一一对应（11 个）
"""
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok
from plugins.addon.file_download.schemas import (
    FileHiddenToggle,
    FileUpdate,
    FolderCreate,
    FolderRename,
)
from plugins.addon.file_download.services.file_service import FileService
from plugins.addon.file_download.services.folder_service import FolderService

# router 级依赖 check_admin：登录校验先于各接口的 require_permission 权限校验
router = APIRouter(
    prefix="/api/admin/v1/file_download",
    tags=["文件下载插件"],
    dependencies=[Depends(check_admin)],
)


def _parse_area_ids(raw: str) -> list:
    """解析逗号分隔的产区 ID 字符串（multipart 表单无法直接传数组）"""
    return [int(x) for x in raw.split(",") if x.strip().isdigit()] if raw else []


# ---------------------------------------------------------------------------
# 文件夹管理（5 个端点）
# ---------------------------------------------------------------------------
@router.get("/folders", dependencies=[Depends(require_permission("file_download:list"))])
async def list_folders():
    """文件夹列表 — 含各夹文件计数（默认文件夹置顶）"""
    async with async_session_factory() as db:
        return ok({"list": await FolderService.list_folders(db)})


@router.post("/folders", dependencies=[Depends(require_permission("file_download:folder"))])
async def create_folder(data: FolderCreate, request: Request):
    """新建文件夹"""
    async with async_session_factory() as db:
        folder_id = await FolderService.create_folder(db, data.name, request.state.user_id)
        # 记录操作日志
        await active_log(f"新建文件夹: {data.name}", "file_download_folder_create", rel_id=folder_id, db=db)
    return ok({"id": folder_id}, msg="创建成功")


@router.put("/folders/{folder_id}", dependencies=[Depends(require_permission("file_download:folder"))])
async def rename_folder(folder_id: int, data: FolderRename, request: Request):
    """重命名文件夹"""
    async with async_session_factory() as db:
        success = await FolderService.rename_folder(db, folder_id, data.name, request.state.user_id)
        if not success:
            raise HTTPException(status_code=404, detail="文件夹不存在")
        # 记录操作日志
        await active_log(f"重命名文件夹: {data.name}", "file_download_folder_rename", rel_id=folder_id, db=db)
    return ok(msg="重命名成功")


@router.delete("/folders/{folder_id}", dependencies=[Depends(require_permission("file_download:folder"))])
async def delete_folder(folder_id: int, request: Request):
    """删除文件夹 — 夹内文件移入默认文件夹，默认文件夹禁止删除"""
    async with async_session_factory() as db:
        err = await FolderService.delete_folder(db, folder_id)
        if err == "not_found":
            raise HTTPException(status_code=404, detail="文件夹不存在")
        if err == "is_default":
            raise HTTPException(status_code=400, detail="默认文件夹不可删除")
        # 记录操作日志
        await active_log(f"删除文件夹: {folder_id}", "file_download_folder_delete", rel_id=folder_id, db=db)
    return ok(msg="删除成功")


@router.put("/folders/{folder_id}/default", dependencies=[Depends(require_permission("file_download:folder"))])
async def set_default_folder(folder_id: int, request: Request):
    """设为默认文件夹 — 原默认夹标记同事务转移"""
    async with async_session_factory() as db:
        success = await FolderService.set_default(db, folder_id, request.state.user_id)
        if not success:
            raise HTTPException(status_code=404, detail="文件夹不存在")
        # 记录操作日志
        await active_log(f"设为默认文件夹: {folder_id}", "file_download_folder_default", rel_id=folder_id, db=db)
    return ok(msg="设置成功")


# ---------------------------------------------------------------------------
# 文件管理（6 个端点）
# ---------------------------------------------------------------------------
@router.get("/files", dependencies=[Depends(require_permission("file_download:list"))])
async def list_files(
    keyword: str = Query("", description="名称关键词"),
    folder_id: int = Query(0, ge=0, description="文件夹ID，0=全部"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
):
    """文件列表 — 支持文件夹过滤、关键词搜索与分页"""
    async with async_session_factory() as db:
        return ok(await FileService.list_files(db, keyword, folder_id, page, limit))


@router.post("/files", dependencies=[Depends(require_permission("file_download:create"))])
async def upload_file(
    request: Request,
    file: UploadFile = File(..., description="上传的文件"),
    name: str = Form("", max_length=200, description="显示名称，缺省取原文件名"),
    folder_id: int = Form(..., ge=1, description="所属文件夹ID"),
    visible_range: str = Form("all", pattern="^(all|area)$", description="可见范围"),
    area_ids: str = Form("", description="产区ID逗号分隔，visible_range=area 时使用"),
    description: str = Form("", max_length=1000, description="文件描述"),
    hidden: int = Form(0, ge=0, le=1, description="是否隐藏"),
):
    """上传文件 — 流式写盘，白名单/大小上限由插件配置控制"""
    async with async_session_factory() as db:
        if not await FolderService.get_folder(db, folder_id):
            raise HTTPException(status_code=404, detail="文件夹不存在")
        try:
            file_id = await FileService.save_upload(
                db, upload_file=file, name=name, folder_id=folder_id,
                visible_range=visible_range, area_ids=_parse_area_ids(area_ids),
                description=description, admin_id=request.state.user_id, hidden=hidden,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # 记录操作日志
        await active_log(f"上传文件: {file.filename}", "file_download_create", rel_id=file_id, db=db)
    return ok({"id": file_id}, msg="上传成功")


@router.put("/files/{file_id}", dependencies=[Depends(require_permission("file_download:update"))])
async def update_file(file_id: int, data: FileUpdate, request: Request):
    """编辑文件元信息（不更换物理文件），产区关联先清后建"""
    async with async_session_factory() as db:
        if not await FolderService.get_folder(db, data.folder_id):
            raise HTTPException(status_code=404, detail="文件夹不存在")
        success = await FileService.update_file(
            db, file_id, data.name, data.folder_id, data.visible_range,
            data.area_ids, data.description, request.state.user_id,
        )
        if not success:
            raise HTTPException(status_code=404, detail="文件不存在")
        # 记录操作日志
        await active_log(f"编辑文件: {data.name}", "file_download_update", rel_id=file_id, db=db)
    return ok(msg="更新成功")


@router.put("/files/{file_id}/hidden", dependencies=[Depends(require_permission("file_download:update"))])
async def toggle_hidden(file_id: int, data: FileHiddenToggle, request: Request):
    """显示/隐藏切换"""
    async with async_session_factory() as db:
        success = await FileService.toggle_hidden(db, file_id, data.hidden)
        if not success:
            raise HTTPException(status_code=404, detail="文件不存在")
        # 记录操作日志
        hidden_text = "隐藏" if data.hidden == 1 else "显示"
        await active_log(f"切换文件为{hidden_text}: {file_id}", "file_download_hidden", rel_id=file_id, db=db)
    return ok(msg="状态已更新")


@router.delete("/files/{file_id}", dependencies=[Depends(require_permission("file_download:delete"))])
async def delete_file(file_id: int, request: Request):
    """删除文件 — 数据记录 + 产区关联 + 物理文件"""
    async with async_session_factory() as db:
        success = await FileService.delete_file(db, file_id)
        if not success:
            raise HTTPException(status_code=404, detail="文件不存在")
        # 记录操作日志
        await active_log(f"删除文件: {file_id}", "file_download_delete", rel_id=file_id, db=db)
    return ok(msg="删除成功")


@router.get("/files/{file_id}/download", dependencies=[Depends(require_permission("file_download:list"))])
async def download_file(file_id: int):
    """管理员下载 — FileResponse 文件流，下载名还原为原始文件名"""
    async with async_session_factory() as db:
        row = await FileService.get_file(db, file_id)
        if not row:
            raise HTTPException(status_code=404, detail="文件不存在")
        path = FileService.get_download_path(row)
        origin_name = row.origin_name
    if not path:
        raise HTTPException(status_code=404, detail="文件已失效，请联系管理员")
    # session 已退出，下载流不持有数据库连接
    return FileResponse(path, filename=origin_name)
