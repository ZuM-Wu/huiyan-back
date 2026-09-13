"""稳定文件地址访问接口。

业务表只保存站内地址，访问时依据文件日志和当前存储插件动态生成直链或临时签名。
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from core.config import BASE_DIR
from core.oss_service import oss_service

router = APIRouter(prefix="/api/v1/storage", tags=["稳定文件地址"])


@router.get("/files/{local_path:path}")
async def resolve_storage_file(
    local_path: str,
    action: str = Query("preview", pattern="^(preview|download)$"),
):
    """按 upload/ 相对路径返回当前存储插件地址，失败时回退本地文件。"""
    try:
        normalized = oss_service.normalize_local_path(local_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    url = await oss_service.resolve_file_url(normalized, action=action)
    if not url:
        raise HTTPException(status_code=404, detail="文件访问地址不可用")
    # 远端日志可在本地副本不存在时继续访问；本地回退地址必须再次确认文件存在。
    if url == f"/upload/{normalized}":
        local_file = (BASE_DIR / "upload" / normalized).resolve()
        upload_root = (BASE_DIR / "upload").resolve()
        if upload_root not in local_file.parents or not local_file.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
    return RedirectResponse(url=url, status_code=307)
