# -*- coding: utf-8 -*-
"""智能识别插件的管理员端 API。"""

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.plugin_query_service import get_plugin_by_name
from core.rate_limiter import check_rate_detail
from core.response import ok
from plugins.addon.yolo_model_manager.schemas import (
    DetectionTaskCreate,
    ModelMetadataUpdate,
    ModelPlotsUpdate,
    RecognitionCreate,
)
from plugins.addon.yolo_model_manager.services.detection_service import (
    DetectionNotReadyError,
    DetectionReferenceError,
    DetectionService,
)
from plugins.addon.yolo_model_manager.services.recognition_service import (
    RecognitionBindingError,
    RecognitionReferenceError,
    RecognitionService,
)
from plugins.addon.yolo_model_manager.services.model_service import (
    BindingConflictError,
    PlotSelectionError,
    YoloModelService,
)

router = APIRouter(
    prefix="/api/admin/v1/plugins/yolo_model_manager",
    tags=["智能识别"],
    dependencies=[Depends(check_admin)],
)


@router.get("/models", dependencies=[Depends(require_permission("yolo_model_manager:list"))])
async def list_models(
    keyword: str = Query("", max_length=128, description="名称、版本或原文件名关键词"),
    model_format: str = Query("", alias="format", pattern="^(|pt|onnx)$"),
    binding_status: str = Query("", pattern="^(|bound|unbound)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    """分页查询模型及其地块绑定摘要。"""
    async with async_session_factory() as db:
        data = await YoloModelService.list_models(
            db,
            keyword=keyword.strip(),
            model_format=model_format,
            binding_status=binding_status,
            page=page,
            limit=limit,
        )
    return ok(data)


@router.post("/models", dependencies=[Depends(require_permission("yolo_model_manager:create"))])
async def upload_model(
    request: Request,
    file: UploadFile = File(..., description="PT或ONNX模型文件"),
    name: str = Form(..., min_length=1, max_length=128, description="模型显示名称"),
    version: str = Form("", max_length=64, description="模型业务版本"),
    description: str = Form("", max_length=1000, description="模型说明"),
    default_confidence: float = Form(
        0.25, ge=0.01, le=1, description="模型默认识别置信度"
    ),
):
    """按统一上传策略流式保存模型并计算 SHA-256。"""
    if not name.strip():
        raise HTTPException(status_code=400, detail="模型名称不能为空")
    async with async_session_factory() as db:
        try:
            model_id, labels = await YoloModelService.save_upload(
                db,
                upload_file=file,
                name=name,
                version=version,
                description=description,
                admin_id=request.state.user_id,
                default_confidence=default_confidence,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await active_log(
            f"上传YOLO模型: {name.strip()}",
            "yolo_model_create",
            rel_id=model_id,
            request=request,
            db=db,
        )
    return ok({"id": model_id, "labels": labels}, msg="模型上传成功")


@router.patch(
    "/models/{model_id}",
    dependencies=[Depends(require_permission("yolo_model_manager:update"))],
)
async def update_model(
    model_id: int,
    data: ModelMetadataUpdate,
    request: Request,
):
    """更新模型名称、版本和说明，不替换物理模型文件。"""
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="模型名称不能为空")
    async with async_session_factory() as db:
        updated = await YoloModelService.update_metadata(
            db,
            model_id,
            name=data.name,
            version=data.version,
            description=data.description,
            default_confidence=data.default_confidence,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="模型不存在")
        await active_log(
            f"编辑YOLO模型: {data.name.strip()}",
            "yolo_model_update",
            rel_id=model_id,
            request=request,
            db=db,
        )
    return ok(msg="模型信息已更新")


@router.get(
    "/models/{model_id}/download",
    dependencies=[Depends(require_permission("yolo_model_manager:download"))],
)
async def download_model(model_id: int, request: Request):
    """鉴权后从插件私有目录下载模型，并还原原始文件名。"""
    async with async_session_factory() as db:
        row = await YoloModelService.get_model(db, model_id)
        if not row:
            raise HTTPException(status_code=404, detail="模型不存在")
        path = YoloModelService.get_download_path(row)
        if not path:
            raise HTTPException(status_code=404, detail="模型文件已丢失")
        origin_name = row.origin_name
        await active_log(
            f"下载YOLO模型: {row.name}",
            "yolo_model_download",
            rel_id=model_id,
            request=request,
            db=db,
        )
    return FileResponse(path, filename=origin_name, media_type="application/octet-stream")


@router.delete(
    "/models/{model_id}",
    dependencies=[Depends(require_permission("yolo_model_manager:delete"))],
)
async def delete_model(model_id: int, request: Request):
    """仅允许删除未绑定任何地块的模型。"""
    async with async_session_factory() as db:
        status, binding_count = await YoloModelService.delete_model(db, model_id)
        if status == "not_found":
            raise HTTPException(status_code=404, detail="模型不存在")
        if status == "bound":
            raise HTTPException(
                status_code=409,
                detail=f"模型仍绑定 {binding_count} 个地块，请先解绑",
            )
        await active_log(
            "删除YOLO模型",
            "yolo_model_delete",
            rel_id=model_id,
            request=request,
            db=db,
        )
    return ok(msg="模型已删除")


@router.get("/plots", dependencies=[Depends(require_permission("yolo_model_manager:list"))])
async def list_plots(
    model_id: int | None = Query(None, ge=1, description="当前编辑模型ID"),
):
    """返回按产区分组的地块及其当前模型占用信息。"""
    async with async_session_factory() as db:
        try:
            data = await YoloModelService.list_plots(db, model_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok({"areas": data})


@router.put(
    "/models/{model_id}/plots",
    dependencies=[Depends(require_permission("yolo_model_manager:bind"))],
)
async def replace_model_plots(
    model_id: int,
    data: ModelPlotsUpdate,
    request: Request,
):
    """整集替换模型地块绑定；空数组表示解绑全部。"""
    async with async_session_factory() as db:
        try:
            updated = await YoloModelService.replace_bindings(
                db,
                model_id,
                data.plot_ids,
                request.state.user_id,
            )
        except PlotSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except BindingConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not updated:
            raise HTTPException(status_code=404, detail="模型不存在")
        await active_log(
            f"更新YOLO模型地块绑定: {len(data.plot_ids)}个",
            "yolo_model_bind",
            rel_id=model_id,
            request=request,
            db=db,
        )
    return ok(msg="模型地块绑定已更新" if data.plot_ids else "模型已解绑全部地块")


@router.get(
    "/recognitions",
    dependencies=[Depends(require_permission("yolo_model_manager:list"))],
)
async def list_recognitions(
    plot_id: int | None = Query(None, ge=1, description="识别地块ID"),
    model_id: int | None = Query(None, ge=1, description="识别模型ID"),
    source_type: str | None = Query(
        None, pattern="^(model_test|quick_detection|external)$", description="识别来源类型"
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    """默认查询全部识别记录，并支持按地块和模型分页筛选。"""
    async with async_session_factory() as db:
        data = await RecognitionService.list_records(
            db,
            plot_id=plot_id,
            model_id=model_id,
            source_type=source_type,
            page=page,
            limit=limit,
        )
    return ok(data)


@router.get(
    "/recognitions/options",
    dependencies=[Depends(require_permission("yolo_model_manager:list"))],
)
async def list_recognition_options():
    """返回当前资源与历史快照合并后的地块、模型筛选选项。"""
    async with async_session_factory() as db:
        data = await RecognitionService.list_options(db)
    return ok(data)


def _supports_detection_capability(plugin: dict | None) -> bool:
    """仅在数据库中的已启用插件版本完成 1.0.5 迁移后暴露检测能力。"""
    if not plugin or plugin.get("status") != 1:
        return False
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", str(plugin.get("version") or ""))
    return bool(match and tuple(map(int, match.groups())) >= (1, 0, 5))


async def require_detection_capability() -> None:
    plugin = await get_plugin_by_name("yolo_model_manager")
    if not _supports_detection_capability(plugin):
        raise HTTPException(status_code=404, detail="快捷检测能力不可用")


_DETECTION_DEPENDENCIES = [
    Depends(require_permission("hardware:data")),
    Depends(require_permission("yolo_model_manager:detect")),
    Depends(require_detection_capability),
]

_TEST_DEPENDENCIES = [Depends(require_permission("yolo_model_manager:detect"))]


@router.get(
    "/recognitions/detection-context",
    dependencies=_DETECTION_DEPENDENCIES,
)
async def get_detection_context(device_id: int = Query(..., ge=1)):
    """解析设备当前地块、绑定模型和最新成功图片快照。"""
    async with async_session_factory() as db:
        try:
            data = await DetectionService.get_context(db, device_id)
        except DetectionReferenceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(data)


@router.post(
    "/recognitions/detection-tasks",
    dependencies=_DETECTION_DEPENDENCIES,
)
async def create_detection_task(data: DetectionTaskCreate, request: Request):
    """仅凭设备ID投递快捷检测，模型和图片由服务端可信解析。"""
    allowed, retry_after = check_rate_detail(
        f"yolo-detect:{request.state.user_id}:{data.device_id}",
        limit=3,
        window=60,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"快捷检测过于频繁，请在 {retry_after} 秒后重试",
            headers={"Retry-After": str(retry_after)},
        )
    async with async_session_factory() as db:
        try:
            context = await DetectionService.get_context(db, data.device_id)
        except DetectionReferenceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        task = await DetectionService.submit(context, request.state.user_id)
    except DetectionNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await active_log(
        "发起生长记录仪快捷检测",
        "yolo_recognition_detect",
        rel_id=task["task_id"],
        request=request,
    )
    return ok(task, msg="检测任务已提交")


@router.post(
    "/recognitions/test-tasks",
    dependencies=_TEST_DEPENDENCIES,
)
async def create_model_test_task(
    request: Request,
    model_id: int = Form(..., ge=1, description="待测试模型ID"),
    file: UploadFile = File(..., description="模型测试图片"),
):
    """按统一图片策略保存测试图片并提交模型测试任务。"""
    from services.image_upload import save_uploaded_image

    try:
        uploaded = await save_uploaded_image(
            file,
            source="yolo_model_test",
            admin_id=request.state.user_id,
            directory="yolo_model_manager/tests",
        )
    except HTTPException:
        raise
    async with async_session_factory() as db:
        try:
            from plugins.addon.yolo_model_manager.services.model_test_service import submit_model_test

            task = await submit_model_test(
                db, model_id, uploaded["url"], request.state.user_id
            )
        except DetectionReferenceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DetectionNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    await active_log(
        "发起YOLO模型识别测试",
        "yolo_recognition_test",
        rel_id=task["task_id"],
        request=request,
    )
    return ok(task, msg="模型测试任务已提交")


@router.get(
    "/recognitions/detection-tasks/{task_id}",
    dependencies=_DETECTION_DEPENDENCIES,
)
async def get_detection_task(task_id: int, request: Request):
    """查询本人快捷检测任务；成功结果由不可变识别记录承载。"""
    data = await DetectionService.get_status(
        task_id, request.state.user_id, source_type="quick_detection"
    )
    if not data:
        raise HTTPException(status_code=404, detail="检测任务不存在")
    return ok(data)


@router.get(
    "/recognitions/test-tasks/{task_id}",
    dependencies=[Depends(require_permission("yolo_model_manager:detect"))],
)
async def get_model_test_task(task_id: int, request: Request):
    """查询本人模型测试任务；与快捷检测共享队列状态和结果结构。"""
    data = await DetectionService.get_status(
        task_id, request.state.user_id, source_type="model_test"
    )
    if not data:
        raise HTTPException(status_code=404, detail="模型测试任务不存在")
    return ok(data)


@router.get(
    "/recognitions/{record_id}",
    dependencies=[Depends(require_permission("yolo_model_manager:list"))],
)
async def get_recognition(record_id: int):
    """查询一条不可变识别记录的完整目标明细。"""
    async with async_session_factory() as db:
        data = await RecognitionService.get_record(db, record_id)
    if not data:
        raise HTTPException(status_code=404, detail="识别记录不存在")
    return ok(data)


@router.post(
    "/recognitions",
    dependencies=[Depends(require_permission("yolo_model_manager:record:create"))],
)
async def create_recognition(data: RecognitionCreate, request: Request):
    """接收识别执行方结果，并在当前模型与地块绑定可信时保存快照。"""
    async with async_session_factory() as db:
        try:
            record = await RecognitionService.create_record(
                db,
                plot_id=data.plot_id,
                model_id=data.model_id,
                image_url=data.image_url,
                detections=[item.model_dump() for item in data.detections],
                recognized_at=data.recognized_at,
                admin_id=request.state.user_id,
                source_type="external",
            )
        except RecognitionReferenceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RecognitionBindingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await active_log(
            f"写入智能识别记录: {record['area_name']} / {record['plot_name']}",
            "yolo_recognition_create",
            rel_id=record["id"],
            request=request,
            db=db,
        )
    return ok({
        "id": record["id"],
        "detection_count": record["detection_count"],
        "max_confidence": record["max_confidence"],
    }, msg="识别记录已保存")
