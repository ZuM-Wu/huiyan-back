# -*- coding: utf-8 -*-
"""
App管理插件 — 管理员端路由（前缀 /api/admin/v1/app_manage）

- 路由级 Depends(check_admin) 登录校验 + 接口级 require_permission 细粒度权限
- 业务逻辑全部委托 services 层，handler 保持简洁
- 写操作记录 active_log 操作日志
- 端点清单：版本 4 个 + 广告 3 个 + 公告 4 个，共 11 个
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

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok
from plugins.addon.app_manage.schemas import (
    AdUpdate,
    NoticeCreate,
    NoticeUpdate,
    VersionUpdate,
)
from plugins.addon.app_manage.services.ad_service import AdService
from plugins.addon.app_manage.services.apk_service import ApkService
from plugins.addon.app_manage.services.notice_service import NoticeService

# router 级依赖 check_admin：登录校验先于各接口的 require_permission 权限校验
router = APIRouter(
    prefix="/api/admin/v1/app_manage",
    tags=["App管理插件"],
    dependencies=[Depends(check_admin)],
)


# ---------------------------------------------------------------------------
# App版本管理（4 个端点）
# ---------------------------------------------------------------------------
@router.get("/versions", dependencies=[Depends(require_permission("app_manage:list"))])
async def list_versions(
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
):
    """版本分页列表 — version_code 倒序，最新在前"""
    async with async_session_factory() as db:
        return ok(await ApkService.list_versions(db, page, limit))


@router.post("/versions", dependencies=[Depends(require_permission("app_manage:version"))])
async def upload_version(
    request: Request,
    apk: UploadFile = File(..., description="APK安装包文件"),
    version_name: str = Form(..., min_length=1, max_length=32, description="版本名(如1.2.0)"),
    version_code: int = Form(..., ge=1, description="版本号(App端数值比较用)"),
    build_number: str = Form("", max_length=32, description="内部构建号(选填)"),
    changelog: str = Form("", max_length=20000, description="更新日志(富文本HTML)"),
    update_policy: int = Form(1, ge=0, le=2, description="更新策略 0可忽略 1提示可稍后 2强制"),
    status: int = Form(1, ge=0, le=1, description="状态 1发布 0下架"),
):
    """上传发版 — APK 流式写盘，大小上限由插件配置控制"""
    async with async_session_factory() as db:
        try:
            version_id = await ApkService.save_apk(
                db, upload_file=apk, version_name=version_name,
                version_code=version_code, build_number=build_number,
                changelog=changelog, update_policy=update_policy,
                status=status, admin_id=request.state.user_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # 记录操作日志
        await active_log(f"发布App版本: {version_name}({version_code})", "app_manage_version_create", rel_id=version_id, db=db)
    return ok({"id": version_id}, msg="发布成功")


@router.put("/versions/{version_id}", dependencies=[Depends(require_permission("app_manage:version"))])
async def update_version(version_id: int, data: VersionUpdate, request: Request):
    """编辑版本元信息（构建号/更新日志/更新策略/上下架），不更换物理APK"""
    async with async_session_factory() as db:
        success = await ApkService.update_version(
            db, version_id, data.build_number, data.changelog,
            data.update_policy, data.status,
        )
        if not success:
            raise HTTPException(status_code=404, detail="版本不存在")
        # 记录操作日志
        await active_log(f"编辑App版本: {version_id}", "app_manage_version_update", rel_id=version_id, db=db)
    return ok(msg="更新成功")


@router.delete("/versions/{version_id}", dependencies=[Depends(require_permission("app_manage:version"))])
async def delete_version(version_id: int, request: Request):
    """删除版本 — 数据记录 + 物理APK文件同步删除"""
    async with async_session_factory() as db:
        success = await ApkService.delete_version(db, version_id)
        if not success:
            raise HTTPException(status_code=404, detail="版本不存在")
        # 记录操作日志
        await active_log(f"删除App版本: {version_id}", "app_manage_version_delete", rel_id=version_id, db=db)
    return ok(msg="删除成功")


# ---------------------------------------------------------------------------
# 开屏广告管理（3 个端点，单行 id=1）
# ---------------------------------------------------------------------------
@router.get("/ad", dependencies=[Depends(require_permission("app_manage:list"))])
async def get_ad():
    """读取广告配置 — 单行完整字段"""
    async with async_session_factory() as db:
        return ok({"ad": await AdService.get_ad(db)})


@router.put("/ad", dependencies=[Depends(require_permission("app_manage:ad"))])
async def update_ad(data: AdUpdate, request: Request):
    """保存广告配置（跳转/投放时间窗/时长/启停）"""
    async with async_session_factory() as db:
        err = await AdService.update_ad(db, data, request.state.user_id)
        if err == "not_found":
            raise HTTPException(status_code=404, detail="广告记录不存在，请重装插件")
        if err == "no_image":
            raise HTTPException(status_code=400, detail="请先上传广告图再启用")
        # 记录操作日志
        enabled_text = "启用" if data.enabled == 1 else "禁用"
        await active_log(f"保存开屏广告配置({enabled_text})", "app_manage_ad_update", rel_id=1, db=db)
    return ok(msg="保存成功")


@router.post("/ad/image", dependencies=[Depends(require_permission("app_manage:ad"))])
async def upload_ad_image(
    request: Request,
    image: UploadFile = File(..., description="广告图(jpg/jpeg/png/webp)"),
):
    """上传广告图 — 新图生成新 cache_key，旧素材同步删除"""
    async with async_session_factory() as db:
        try:
            result = await AdService.save_image(db, image, request.state.user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # 记录操作日志
        await active_log("上传开屏广告图", "app_manage_ad_image", rel_id=1, db=db)
    return ok(result, msg="上传成功")


# ---------------------------------------------------------------------------
# App公告管理（4 个端点）
# ---------------------------------------------------------------------------
@router.get("/notices", dependencies=[Depends(require_permission("app_manage:list"))])
async def list_notices(
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
):
    """公告分页列表 — sort_order 升序"""
    async with async_session_factory() as db:
        return ok(await NoticeService.list_notices(db, page, limit))


@router.post("/notices", dependencies=[Depends(require_permission("app_manage:notice"))])
async def create_notice(data: NoticeCreate, request: Request):
    """新建App公告"""
    async with async_session_factory() as db:
        notice_id = await NoticeService.create_notice(db, data, request.state.user_id)
        # 记录操作日志
        await active_log(f"新建App公告: {data.title}", "app_manage_notice_create", rel_id=notice_id, db=db)
    return ok({"id": notice_id}, msg="创建成功")


@router.put("/notices/{notice_id}", dependencies=[Depends(require_permission("app_manage:notice"))])
async def update_notice(notice_id: int, data: NoticeUpdate, request: Request):
    """编辑App公告"""
    async with async_session_factory() as db:
        success = await NoticeService.update_notice(db, notice_id, data, request.state.user_id)
        if not success:
            raise HTTPException(status_code=404, detail="公告不存在")
        # 记录操作日志
        await active_log(f"编辑App公告: {data.title}", "app_manage_notice_update", rel_id=notice_id, db=db)
    return ok(msg="更新成功")


@router.delete("/notices/{notice_id}", dependencies=[Depends(require_permission("app_manage:notice"))])
async def delete_notice(notice_id: int, request: Request):
    """删除App公告"""
    async with async_session_factory() as db:
        success = await NoticeService.delete_notice(db, notice_id)
        if not success:
            raise HTTPException(status_code=404, detail="公告不存在")
        # 记录操作日志
        await active_log(f"删除App公告: {notice_id}", "app_manage_notice_delete", rel_id=notice_id, db=db)
    return ok(msg="删除成功")
