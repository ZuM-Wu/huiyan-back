"""七牛对象存储管理员 API。"""

import logging

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.file_log_service import delete_file_log
from core.log.active_log import active_log
from core.plugin_query_service import get_single_plugin_config
from core.response import fail, ok

from .plugin import Plugin
from .schemas import AccessUrlRequest, DeleteFileRequest
from .service import QiniuService, build_object_key, normalize_save_path

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin/v1/plugins/qiniu_oss",
    tags=["七牛对象存储"],
    dependencies=[Depends(check_admin)],
)


async def _plugin() -> Plugin:
    """从配置门面创建无状态请求实例。"""
    return Plugin(None, await get_single_plugin_config("qiniu_oss"))


@router.post("/test", dependencies=[Depends(require_admin_permission("qiniu_oss:config"))])
async def test_connection():
    """测试已保存的七牛配置。"""
    result = await (await _plugin()).oss_link()
    if result.get("status") == "success":
        return ok({"success": True}, msg=result.get("msg", "连接正常"))
    return fail(400, result.get("msg", "连接失败"))


@router.get("/files", dependencies=[Depends(require_admin_permission("qiniu_oss:list"))])
async def list_files(
    prefix: str = Query("", max_length=512),
    marker: str = Query("", max_length=1024),
    limit: int = Query(100, ge=1, le=1000),
):
    """按前缀分页列举七牛对象。"""
    try:
        service = QiniuService(await get_single_plugin_config("qiniu_oss"))
        return ok(await service.list_files(prefix, marker, limit))
    except (ValueError, RuntimeError) as exc:
        return fail(400, str(exc))


@router.post("/files", dependencies=[Depends(require_admin_permission("qiniu_oss:upload"))])
async def upload_file(
    file: UploadFile = File(..., description="待上传文件"),
    key: str = Form(""),
    prefix: str = Form(""),
    request: Request = None,
):
    """上传文件到七牛保存路径。"""
    temp_path = None
    try:
        plugin = await _plugin()
        service = plugin._service()
        config_prefix = normalize_save_path(service.save_path)
        requested_prefix = normalize_save_path(prefix) if prefix else config_prefix
        if requested_prefix and config_prefix and not (
            requested_prefix == config_prefix or requested_prefix.startswith(config_prefix + "/")
        ):
            return fail(400, "上传前缀必须位于保存路径下")
        object_key = build_object_key(requested_prefix, key or file.filename or "")
        temp_dir = service_temp_dir()
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / object_key.replace("/", "_")
        temp_path.write_bytes(await file.read())
        await service.upload_file(str(temp_path), object_key, file.content_type)
        url = await service.access_url(object_key)
        await active_log(f"上传七牛文件：{object_key}", "qiniu_oss_upload", request=request)
        return ok({"key": object_key, "url": url}, msg="文件上传成功")
    except (ValueError, RuntimeError) as exc:
        return fail(400, str(exc))
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


@router.post("/files/access-url", dependencies=[Depends(require_admin_permission("qiniu_oss:access"))])
async def access_url(data: AccessUrlRequest):
    """生成七牛公开直链或私有临时地址。"""
    try:
        service = QiniuService(await get_single_plugin_config("qiniu_oss"))
        key = service.object_key(data.key)
        return ok({"key": key, "url": await service.access_url(key, data.expires, data.action), "action": data.action})
    except (ValueError, RuntimeError) as exc:
        return fail(400, str(exc))


@router.delete("/files", dependencies=[Depends(require_admin_permission("qiniu_oss:delete"))])
async def delete_file(data: DeleteFileRequest, request: Request = None):
    """删除单个七牛对象及对应文件日志。"""
    try:
        service = QiniuService(await get_single_plugin_config("qiniu_oss"))
        key = service.object_key(data.key)
        await service.delete_file(key)
        await delete_file_log(key, oss_method="qiniu_oss")
        await active_log(f"删除七牛文件：{key}", "qiniu_oss_delete", request=request)
        return ok({"key": key}, msg="文件已删除")
    except (ValueError, RuntimeError) as exc:
        return fail(400, str(exc))


def service_temp_dir():
    """返回插件临时目录，避免把上传中间文件暴露为静态资源。"""
    from pathlib import Path
    import tempfile

    return Path(tempfile.gettempdir()) / "huiyan-qiniu-oss"
