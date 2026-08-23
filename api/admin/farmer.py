"""农户管理 API（管理员端）"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError

from core.auth.jwt_handler import create_farmer_token
from core.auth.middleware_chain import check_admin
from core.auth.password import hash_password
from core.auth.rbac import require_admin_permission
from core.auth.security_policy import validate_password_or_400, get_farmer_session_duration, check_farmer_email_suffix
from core.farmer_service import (
    create_farmer as svc_create_farmer,
    delete_farmer as svc_delete_farmer,
    get_farmer_by_id,
    get_farmer_by_username,
    get_farmer_operation_logs,
    get_farmer_options as svc_get_farmer_options,
    get_simple_farmer_list,
    list_farmers as svc_list_farmers,
    toggle_farmer_status as svc_toggle_farmer_status,
    update_farmer as svc_update_farmer,
)
from core.log.active_log import active_log
from core.response import ok
from schemas.farmer import FarmerCreate, FarmerUpdate, FarmerStatusUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1/farmer", tags=["农户管理"])


@router.get("/options", dependencies=[Depends(require_admin_permission("farmer:list"))])
async def farmer_options(_: None = Depends(check_admin)):
    """农户下拉选项 — 返回全量正常农户的 [{id, name, nickname}]（无分页，供绑定下拉全量加载）"""
    return ok(await svc_get_farmer_options())


@router.get(
    "/list",
    dependencies=[Depends(require_admin_permission("farmer:list"))],
)
async def list_farmers(
    keywords: str = Query("", description="搜索关键词"),
    search_field: str = Query("", description="搜索字段 username/nickname/phone/email/company"),
    status: int = Query(None, description="状态筛选 0=禁用 1=正常"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
    _: None = Depends(check_admin),
):
    """农户列表 — 支持搜索、分页、状态筛选"""
    return ok(await svc_list_farmers(
        keywords=keywords, search_field=search_field,
        status=status, page=page, limit=limit,
    ))


@router.get(
    "/simple-list",
    dependencies=[Depends(require_admin_permission("farmer:list"))],
)
async def simple_list_farmers(_: None = Depends(check_admin)):
    """轻量农户列表 — 返回 id/username/phone/email/nickname，用于用户快捷切换下拉展示"""
    farmers = await get_simple_farmer_list()
    return ok({"list": farmers})


@router.get(
    "/{farmer_id}",
    dependencies=[Depends(require_admin_permission("farmer:list"))],
)
async def get_farmer_detail(farmer_id: int, _: None = Depends(check_admin)):
    """农户详情 — GET /{id}"""
    farmer = await get_farmer_by_id(farmer_id)
    if not farmer:
        raise HTTPException(status_code=404, detail="农户不存在")
    return ok(farmer)


@router.get(
    "/{farmer_id}/logs",
    dependencies=[Depends(require_admin_permission("farmer:list"))],
)
async def get_farmer_logs(
    farmer_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    keyword: str = Query("", description="详情/IP搜索关键词"),
    operator: str = Query("", description="操作人搜索"),
    date_from: str = Query("", description="开始日期 YYYY-MM-DD"),
    date_to: str = Query("", description="结束日期 YYYY-MM-DD"),
    _: None = Depends(check_admin)
):
    """农户操作日志 — GET /{farmer_id}/logs（管理后台查看该农户相关的全部操作日志）"""
    return ok(await get_farmer_operation_logs(
        farmer_id, page=page, limit=limit,
        keyword=keyword, operator=operator,
        date_from=date_from, date_to=date_to,
    ))


@router.post(
    "/create",
    dependencies=[Depends(require_admin_permission("farmer:create"))],
)
async def create_farmer(data: FarmerCreate, request: Request):
    """管理员创建农户"""
    try:
        await validate_password_or_400(data.password)

        # 邮箱后缀限制校验（与农户端注册守卫一致）
        suffix_err = await check_farmer_email_suffix(data.email or "")
        if suffix_err:
            raise HTTPException(status_code=400, detail=suffix_err)

        existing = await get_farmer_by_username(data.username)
        if existing:
            raise HTTPException(status_code=409, detail="用户名已存在")

        pw_hash = hash_password(data.password)
        farmer_id = await svc_create_farmer(
            username=data.username, password=pw_hash,
            nickname=data.nickname or data.username,
            email=data.email, phone=data.phone, company=data.company,
        )

        await active_log(f"创建农户: {data.username}", "farmer_create", rel_id=farmer_id)
        return ok({"id": farmer_id}, msg=f"农户 '{data.username}' 创建成功")
    except HTTPException:
        raise
    except IntegrityError:
        # 唯一索引冲突（手机号/邮箱已被占用）返回 409 而非 500
        raise HTTPException(status_code=409, detail="手机号或邮箱已被其他账号占用")
    except Exception as e:
        logger.error(f"[API Error] create_farmer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.put(
    "/{farmer_id}",
    dependencies=[Depends(require_admin_permission("farmer:update"))],
)
async def update_farmer(farmer_id: int, data: FarmerUpdate, request: Request):
    """管理员更新农户信息"""
    try:
        farmer = await get_farmer_by_id(farmer_id)
        if not farmer:
            raise HTTPException(status_code=404, detail="农户不存在")

        update_data = data.model_dump(exclude_unset=True)
        if update_data.get("password"):
            await validate_password_or_400(update_data["password"])
            update_data["password"] = hash_password(update_data["password"])
        elif "password" in update_data:
            del update_data["password"]

        # 邮箱后缀限制校验（更新邮箱时）
        new_email = update_data.get("email")
        if new_email:
            suffix_err = await check_farmer_email_suffix(new_email)
            if suffix_err:
                raise HTTPException(status_code=400, detail=suffix_err)

        await svc_update_farmer(farmer_id, update_data)
        await active_log(f"更新农户: {farmer['username']}", "farmer_update", rel_id=farmer_id)
        return ok(msg="更新成功")
    except HTTPException:
        raise
    except IntegrityError:
        # 唯一索引冲突（手机号/邮箱已被占用）返回 409 而非 500
        raise HTTPException(status_code=409, detail="手机号或邮箱已被其他账号占用")
    except Exception as e:
        logger.error(f"[API Error] update_farmer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.delete(
    "/{farmer_id}",
    dependencies=[Depends(require_admin_permission("farmer:delete"))],
)
async def delete_farmer(farmer_id: int, request: Request):
    """管理员删除农户"""
    try:
        farmer = await get_farmer_by_id(farmer_id)
        if not farmer:
            raise HTTPException(status_code=404, detail="农户不存在")

        username = farmer["username"]
        await svc_delete_farmer(farmer_id)

        await active_log(f"删除农户: {username}", "farmer_delete", rel_id=farmer_id)
        return ok(msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] delete_farmer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.post(
    "/{farmer_id}/impersonate",
    dependencies=[Depends(require_admin_permission("farmer:list"))],
)
async def impersonate_farmer(farmer_id: int, request: Request, _: None = Depends(check_admin)):
    """管理员快捷登录农户账号 — 签发农户 JWT，前端存入 localStorage 后跳转农户端"""
    farmer = await get_farmer_by_id(farmer_id)
    if not farmer:
        raise HTTPException(status_code=404, detail="农户不存在")
    if farmer["status"] != 1:
        raise HTTPException(status_code=403, detail="该农户已被禁用，无法登录")

    session_duration = await get_farmer_session_duration()
    token = create_farmer_token(farmer["id"], farmer["username"], expire_seconds=session_duration)

    await active_log(
        f"管理员快捷登录农户: {farmer['username']}",
        "farmer_impersonate", rel_id=farmer_id, request=request
    )

    return ok({
        "token": token,
        "username": farmer["username"],
        "farmer_id": farmer["id"]
    })


@router.put(
    "/{farmer_id}/status",
    dependencies=[Depends(require_admin_permission("farmer:status"))],
)
async def toggle_farmer_status(farmer_id: int, data: FarmerStatusUpdate, request: Request):
    """切换农户状态 — PUT /{id}/status"""
    try:
        farmer = await get_farmer_by_id(farmer_id)
        if not farmer:
            raise HTTPException(status_code=404, detail="农户不存在")
        await svc_toggle_farmer_status(farmer_id, data.status)

        status_text = "启用" if data.status == 1 else "禁用"
        await active_log(
            f"切换农户状态为{status_text}: {farmer['username']}",
            "farmer_status", rel_id=farmer_id
        )
        return ok(msg=f"状态已更新为{status_text}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Error] toggle_farmer_status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")
