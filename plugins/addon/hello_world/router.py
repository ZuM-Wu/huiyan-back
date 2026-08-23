# -*- coding: utf-8 -*-
"""
Hello World 插件路由 — 管理员端 API + 农户端 API

演示：
- 路由前缀 /api/admin/v1/{plugin}（管理员端）和 /api/v1/{plugin}（农户端）
- router 级依赖 check_admin：所有接口都要求管理员登录
- farmer_router 级依赖 check_farmer：农户端接口要求农户登录
- 每个接口 require_permission：细粒度权限校验，code 与 auth.py 权限树对应
- 业务逻辑委托 services 层，路由函数保持简洁
- 写操作（create/update/delete/status）记录操作日志 active_log
- 新增留言后发布显式瞬时事件，演示解耦通信
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from core.auth.middleware_chain import check_admin, check_farmer
from core.auth.rbac import require_permission
from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from core.events import event_bus
from core.log.active_log import active_log
from core.response import ok
from plugins.addon.hello_world.schemas import (
    ConfigUpdate,
    MessageCreate,
    MessageUpdate,
    StatusUpdate,
)
from plugins.addon.hello_world.services.message_service import MessageService

# 插件标识（与配置键前缀一致）
PLUGIN_NAME = "hello_world"

# router 级依赖 check_admin：登录校验先于各接口的 require_permission 权限校验
router = APIRouter(
    prefix="/api/admin/v1/plugins/hello_world",
    tags=["Hello World 示例插件"],
    dependencies=[Depends(check_admin)],
)

_config_manager = ConfigManager()


@router.get("/messages", dependencies=[Depends(require_permission("hello_world:list"))])
async def list_messages(
    keyword: str = Query("", description="搜索关键词"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
):
    """留言列表 — 支持关键词搜索与分页"""
    async with async_session_factory() as db:
        return ok(await MessageService.list_messages(db, keyword, page, limit))


@router.post("/messages", dependencies=[Depends(require_permission("hello_world:create"))])
async def create_message(data: MessageCreate, request: Request):
    """新增留言 — 创建后发布自定义瞬时事件"""
    async with async_session_factory() as db:
        msg_id = await MessageService.create_message(db, data.title, data.content, data.author)
        # 记录操作日志
        await active_log(f"创建留言: {data.title}", "hello_world_create", rel_id=msg_id, db=db)
    # EventBus 并行执行只读订阅者，发布方不接受修改或阻断。
    await event_bus.publish_transient(
        "hello_world.message_created", {"message_id": msg_id, "title": data.title}
    )
    return ok({"id": msg_id}, msg="创建成功")


@router.put("/messages/{msg_id}", dependencies=[Depends(require_permission("hello_world:update"))])
async def update_message(msg_id: int, data: MessageUpdate, request: Request):
    """更新留言"""
    async with async_session_factory() as db:
        success = await MessageService.update_message(db, msg_id, data.model_dump(exclude_unset=True))
        if not success:
            raise HTTPException(status_code=404, detail="留言不存在")
        # 记录操作日志
        await active_log(f"更新留言: {msg_id}", "hello_world_update", rel_id=msg_id, db=db)
    return ok(msg="更新成功")


@router.put(
    "/messages/{msg_id}/status",
    dependencies=[Depends(require_permission("hello_world:status"))],
)
async def toggle_status(msg_id: int, data: StatusUpdate, request: Request):
    """状态切换 — 演示 RESTful 状态切换模式（PUT /{id}/status）"""
    async with async_session_factory() as db:
        success = await MessageService.toggle_status(db, msg_id, data.status)
        if not success:
            raise HTTPException(status_code=404, detail="留言不存在")
        # 记录操作日志
        status_text = "显示" if data.status == 1 else "隐藏"
        await active_log(
            f"切换留言状态为{status_text}: {msg_id}",
            "hello_world_status",
            rel_id=msg_id,
            db=db,
        )
    return ok(msg="状态已更新")


@router.delete("/messages/{msg_id}", dependencies=[Depends(require_permission("hello_world:delete"))])
async def delete_message(msg_id: int, request: Request):
    """删除留言"""
    async with async_session_factory() as db:
        success = await MessageService.delete_message(db, msg_id)
        if not success:
            raise HTTPException(status_code=404, detail="留言不存在")
        # 记录操作日志
        await active_log(f"删除留言: {msg_id}", "hello_world_delete", rel_id=msg_id, db=db)
    return ok(msg="删除成功")


@router.get("/config", dependencies=[Depends(require_permission("hello_world:config"))])
async def get_config():
    """读取插件配置（演示 ConfigManager.get_plugin_config 前缀查询）"""
    async with async_session_factory() as db:
        cfg = await _config_manager.get_plugin_config(PLUGIN_NAME, db)
    return ok({
        "welcome_text": cfg.get("welcome_text", ""),
        "page_size": int(cfg.get("page_size", "10") or 10),
        "enable_greeting_hook": cfg.get("enable_greeting_hook", "1"),
    })


@router.put("/config", dependencies=[Depends(require_permission("hello_world:config"))])
async def update_config(data: ConfigUpdate, request: Request):
    """更新插件配置（演示 ConfigManager.set 写入）"""
    async with async_session_factory() as db:
        await _config_manager.set(f"{PLUGIN_NAME}.welcome_text", data.welcome_text, db)
        await _config_manager.set(f"{PLUGIN_NAME}.page_size", str(data.page_size), db)
        # 记录操作日志
        await active_log("更新插件配置", "hello_world_config", db=db)
    return ok(msg="配置已保存")


# ---------------------------------------------------------------------------
# 农户端路由 — 演示 /api/v1/{plugin} 前缀 + check_farmer 认证
# ---------------------------------------------------------------------------
farmer_router = APIRouter(
    prefix="/api/v1/plugins/hello_world",
    tags=["Hello World 示例插件（农户端）"],
    dependencies=[Depends(check_farmer)],
)

legacy_router = APIRouter(tags=["Hello World 兼容端点"])


@legacy_router.api_route(
    "/api/admin/v1/hello_world/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def legacy_admin_endpoint(path: str, request: Request):
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(
        f"/api/admin/v1/plugins/hello_world/{path}{query}", status_code=307,
    )


@legacy_router.api_route(
    "/api/v1/hello_world/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def legacy_farmer_endpoint(path: str, request: Request):
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(
        f"/api/v1/plugins/hello_world/{path}{query}", status_code=307,
    )


@farmer_router.get("/messages")
async def farmer_list_messages(
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
):
    """农户端 — 查看公开留言（仅 status=1）"""
    async with async_session_factory() as db:
        return ok(await MessageService.list_public_messages(db, page, limit))


@farmer_router.post("/messages")
async def farmer_create_message(data: MessageCreate, request: Request):
    """农户端 — 提交留言（演示农户端写操作 + 操作日志）"""
    async with async_session_factory() as db:
        msg_id = await MessageService.create_message(db, data.title, data.content, data.author)
        # 记录操作日志
        await active_log(
            f"农户提交留言: {data.title}",
            "hello_world_farmer_create",
            rel_id=msg_id,
            db=db,
        )
    return ok({"id": msg_id}, msg="提交成功")
