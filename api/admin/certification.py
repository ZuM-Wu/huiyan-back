"""实名认证管理 API（管理员端）"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, update

from core.certification_service import (
    get_certification as svc_get_cert,
    list_certifications as svc_list_certs,
    list_farmer_certifications as svc_list_farmer_certs,
    review_certification as svc_review_cert,
)
from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.config_service import (
    get_config as svc_get_config,
    set_config as svc_set_config,
)
from core.db.base import async_session_factory
from core.db.certification import CertificationChannel, CertificationRecord
from core.farmer_service import update_farmer as svc_update_farmer
from core.events import event_bus
from core.log.active_log import active_log
from core.response import ok
from schemas.certification import (
    CertReviewAction, CertConfigUpdate,
    PluginConfigUpsert, ChannelStatusUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/certification", tags=["实名认证"])

# 实名认证配置项列表
_CERT_CONFIG_KEYS = [
    "cert_enabled", "cert_auto_update_name", "cert_show_id",
    "cert_manual_review", "cert_notify_user", "cert_upload_image",
    "cert_phone_match",
]


# ========== 实名审批 ==========

@router.get("/record/list", dependencies=[Depends(require_admin_permission("cert:list"))])
async def list_records(
    keywords: str = Query("", description="搜索关键词"),
    status: int = Query(None, description="状态筛选 0=待审核 1=已认证 2=未通过"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """认证记录列表 — 支持搜索、分页、状态筛选"""
    return ok(await svc_list_certs(
        page=page, limit=limit, status=status, keywords=keywords,
    ))


@router.get("/record/{record_id}", dependencies=[Depends(require_admin_permission("cert:list"))])
async def get_record(record_id: int, _: None = Depends(check_admin)):
    """认证记录详情"""
    record = await svc_get_cert(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    return ok(record)


@router.get("/record/farmer/{farmer_id}", dependencies=[Depends(require_admin_permission("cert:list"))])
async def get_farmer_records(farmer_id: int, _: None = Depends(check_admin)):
    """获取指定农户的认证记录（供农户详情页联动）"""
    result = await svc_list_farmer_certs(farmer_id, page=1, limit=5)
    # 适配原有响应格式（仅返回关键字段）
    return ok([
        {
            "id": r["id"], "status": r["status"],
            "real_name": r["real_name"],
            "cert_no": r["cert_no"],
            "channel": r["channel"],
            "submit_time": r["submit_time"],
        }
        for r in result["list"]
    ])


@router.put("/record/{record_id}/review", dependencies=[Depends(require_admin_permission("cert:review"))])
async def review_record(record_id: int, data: CertReviewAction, request: Request,
                        _: None = Depends(check_admin)):
    """审批认证申请 — 通过/拒绝"""
    record = await svc_get_cert(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 自动更新姓名
    if data.status == 1:
        auto_update = await svc_get_config("cert_auto_update_name")
        if auto_update == "1" and record["real_name"]:
            await svc_update_farmer(record["farmer_id"], {"nickname": record["real_name"]})

    # 调用 service 完成审核（reviewer_name 由 service 层暂不支持，通过额外更新补充）
    admin_id = request.state.user_id
    admin_name = request.state.user_name
    async with async_session_factory() as db:
        success = await svc_review_cert(
            record_id, data.status, data.review_remark, admin_id,
            db=db, commit=False,
        )
        if not success:
            raise HTTPException(status_code=404, detail="记录不存在")
        await db.execute(
            update(CertificationRecord).where(CertificationRecord.id == record_id)
            .values(reviewer_name=admin_name)
        )
        await event_bus.publish_durable("certification.reviewed", {
            "farmer_id": record["farmer_id"], "status": data.status,
            "real_name": record["real_name"] or "",
            "review_remark": data.review_remark or "",
        }, db)
        await db.commit()

    action_text = "通过" if data.status == 1 else "拒绝"
    await active_log(
        f"实名认证审批{action_text}: 记录ID={record_id}",
        "cert_review", rel_id=record_id, request=request
    )
    return ok(msg=f"已{action_text}该认证申请")


# ========== 实名设置 ==========

@router.get("/config", dependencies=[Depends(require_admin_permission("cert:config"))])
async def get_config(_: None = Depends(check_admin)):
    """获取实名认证配置"""
    result = {}
    for key in _CERT_CONFIG_KEYS:
        val = await svc_get_config(key)
        result[key] = val or "0"
    return ok(result)


@router.put("/config", dependencies=[Depends(require_admin_permission("cert:config"))])
async def save_config(data: CertConfigUpdate, request: Request):
    """保存实名认证配置"""
    for key in _CERT_CONFIG_KEYS:
        value = str(getattr(data, key, "0"))
        await svc_set_config(key, value)

    await active_log("修改实名认证配置", "cert_config", request=request)
    return ok(msg="配置已保存")


# ========== 接口管理 ==========

@router.put("/channel/plugin/{plugin_name}", dependencies=[Depends(require_admin_permission("cert:channel"))])
async def upsert_plugin_config(plugin_name: str, data: PluginConfigUpsert, request: Request):
    """保存插件配置（自动创建或更新渠道，无需弹窗）"""
    async with async_session_factory() as db:
        channel = (await db.execute(
            select(CertificationChannel).where(
                CertificationChannel.plugin_name == plugin_name
            )
        )).scalar_one_or_none()

        config_str = json.dumps(data.config, ensure_ascii=False)
        if channel:
            channel.config = config_str
            if data.channel_name:
                channel.channel_name = data.channel_name
            if data.channel_type:
                channel.channel_type = data.channel_type
        else:
            channel = CertificationChannel(
                plugin_name=plugin_name,
                channel_name=data.channel_name or plugin_name,
                channel_type=data.channel_type or "personal",
                config=config_str,
                status=0,
            )
            db.add(channel)

        await db.commit()
        await active_log(
            f"保存认证插件配置: {plugin_name}", "cert_channel",
            rel_id=channel.id, request=request
        )
        return ok(msg="配置已保存")


@router.put("/channel/plugin/{plugin_name}/status", dependencies=[Depends(require_admin_permission("cert:channel"))])
async def toggle_plugin_status(plugin_name: str, data: ChannelStatusUpdate, request: Request):
    """启用/禁用认证插件"""
    async with async_session_factory() as db:
        channel = (await db.execute(
            select(CertificationChannel).where(
                CertificationChannel.plugin_name == plugin_name
            )
        )).scalar_one_or_none()
        if not channel:
            raise HTTPException(status_code=404, detail="插件未配置")
        await db.execute(
            update(CertificationChannel)
            .where(CertificationChannel.plugin_name == plugin_name)
            .values(status=data.status)
        )
        await db.commit()
        status_text = "启用" if data.status == 1 else "禁用"
        await active_log(
            f"{status_text}认证插件: {plugin_name}", "cert_channel",
            rel_id=channel.id, request=request
        )
        return ok(msg=f"插件已{status_text}")


@router.delete("/channel/plugin/{plugin_name}", dependencies=[Depends(require_admin_permission("cert:channel"))])
async def uninstall_plugin(plugin_name: str, request: Request):
    """卸载认证插件配置（删除渠道记录，不删除插件文件）"""
    async with async_session_factory() as db:
        channel = (await db.execute(
            select(CertificationChannel).where(
                CertificationChannel.plugin_name == plugin_name
            )
        )).scalar_one_or_none()
        if not channel:
            raise HTTPException(status_code=404, detail="插件未配置")
        await db.delete(channel)
        await db.commit()
        await active_log(
            f"卸载认证插件: {plugin_name}", "cert_channel",
            rel_id=None, request=request
        )
        return ok(msg="插件配置已卸载")


@router.get("/channel/plugins", dependencies=[Depends(require_admin_permission("cert:channel"))])
async def list_cert_plugins(request: Request, _: None = Depends(check_admin)):
    """列出可用的 certification 类型插件及其当前配置与状态"""
    pm = request.app.state.plugin_manager
    discovered = pm.discover()
    cert_plugins = [p for p in discovered if p["module"] == "certification"]

    # 查询已存在的渠道记录
    async with async_session_factory() as db:
        result = await db.execute(
            select(CertificationChannel).where(
                CertificationChannel.plugin_name.in_(
                    [p["name"] for p in cert_plugins]
                )
            )
        )
        channels = {c.plugin_name: c for c in result.scalars().all()}

    result_list = []
    for p in cert_plugins:
        meta = {}
        schema = []
        try:
            meta = pm.load_metadata(p["name"])
            import importlib
            module = importlib.import_module(
                f"plugins.certification.{p['name']}.plugin"
            )
            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls:
                instance = plugin_cls(None, meta.get("config", {}))
                schema = instance.get_config_schema() or []
        except Exception as e:
            logger.warning("[认证设置] 加载插件配置 schema 失败 %s: %s", p["name"], e)

        channel = channels.get(p["name"])
        # 从 schema 默认值构建初始配置
        default_config = {}
        for field in schema:
            if "default" in field:
                default_config[field["key"]] = field["default"]

        result_list.append({
            "name": p["name"],
            "title": meta.get("title", p["name"]),
            "description": meta.get("description", ""),
            "version": meta.get("version", ""),
            "config_schema": schema,
            "channel_id": channel.id if channel else None,
            "config": json.loads(channel.config) if channel and channel.config else default_config,
            "status": channel.status if channel else 0,
        })
    return ok({"list": result_list})


@router.post("/channel/plugin/{plugin_name}/test", dependencies=[Depends(require_admin_permission("cert:channel"))])
async def test_plugin_connection(plugin_name: str, _: None = Depends(check_admin)):
    """测试认证插件连接（调用插件的 test_connection 方法）"""
    async with async_session_factory() as db:
        channel = (await db.execute(
            select(CertificationChannel).where(
                CertificationChannel.plugin_name == plugin_name
            )
        )).scalar_one_or_none()
        if not channel:
            raise HTTPException(status_code=404, detail="插件未配置")

        try:
            import importlib
            module = importlib.import_module(
                f"plugins.certification.{plugin_name}.plugin"
            )
            plugin_cls = getattr(module, "Plugin", None)
            if not plugin_cls:
                raise RuntimeError(f"插件 {plugin_name} 缺少 Plugin 类")
            instance = plugin_cls(None, {})
            channel_config = json.loads(channel.config or "{}")
            result = await instance.test_connection(channel_config)
            return ok(result)
        except ImportError:
            raise HTTPException(
                status_code=400,
                detail=f"插件 {plugin_name} 未安装或不可用"
            )
        except Exception as e:
            return ok({"success": False, "message": str(e)})
