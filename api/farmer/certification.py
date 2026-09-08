"""农户端实名认证 API

数据归属设计：
- 用户实名数据（姓名、身份证号、状态）存储在核心系统 hy_certification_record 表
- 插件仅作为验证通道（无状态），接收参数 → 调第三方 API → 返回结果
- 插件删除后，认证记录完整保留，仅无法发起新的第三方验证

认证流程：
1. 农户提交姓名+身份证号 → 核心系统创建 CertificationRecord(status=0)
2. 若有启用的插件渠道 → 调用 plugin.certify_initialize 获取认证链接
3. 农户在第三方平台完成认证
4. 农户查询结果 → 调用 plugin.certify_query → 更新 CertificationRecord.status
5. 若开启人工复审 → 插件通过后仍需管理员审批
"""
import json
import logging
from core.time_utils import china_now

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, update

from core.db.base import async_session_factory
from core.db.certification import CertificationRecord, CertificationChannel
from core.auth.middleware_chain import check_farmer
from core.config_service import get_config
from core.log.active_log import active_log
from core.response import ok
from schemas.certification import FarmerCertSubmit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/certification", tags=["农户实名认证"])


def _load_plugin(plugin_name: str):
    """动态加载认证插件类（失败返回 None）"""
    try:
        import importlib
        module = importlib.import_module(
            f"plugins.certification.{plugin_name}.plugin"
        )
        return getattr(module, "Plugin", None)
    except Exception:
        return None


@router.get("/channels")
async def list_channels(request: Request, _: None = Depends(check_farmer)):
    """获取可用的认证渠道列表（供农户选择）"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(CertificationChannel)
            .where(CertificationChannel.status == 1)
            .order_by(CertificationChannel.sort_order, CertificationChannel.id)
        )
        channels = result.scalars().all()
        channel_list = [
            {
                "plugin_name": c.plugin_name,
                "channel_name": c.channel_name or c.plugin_name,
                "channel_type": c.channel_type,
                "description": _channel_desc(c.plugin_name),
            }
            for c in channels
        ]
        return ok({
            "channels": channel_list,
            "has_multiple": len(channel_list) > 1,
        })


def _channel_desc(plugin_name: str) -> str:
    """返回认证渠道的中文描述"""
    descs = {
        "zhima_credit": "支付宝芝麻信用认证，支持人脸识别",
    }
    return descs.get(plugin_name, "第三方实名认证")


@router.get("/qrcode")
async def get_qrcode(
    request: Request,
    text: str = Query(..., max_length=2048, description="编码内容"),
    _: None = Depends(check_farmer),
):
    """生成二维码图片（用于第三方认证链接扫码）"""
    import io

    import qrcode
    from fastapi.responses import StreamingResponse

    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@router.get("/status")
async def get_cert_status(request: Request, _: None = Depends(check_farmer)):
    """获取当前农户的认证状态"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(CertificationRecord)
            .where(CertificationRecord.farmer_id == request.state.user_id)
            .order_by(CertificationRecord.id.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        if not record:
            return ok({"cert_status": -1, "message": "未提交认证"})
        data = {
            "cert_status": record.status,
            "real_name": record.real_name,
            "cert_no": record.cert_no,
            "channel": record.channel,
            "submit_time": str(record.submit_time) if record.submit_time else None,
            "review_time": str(record.review_time) if record.review_time else None,
            "review_remark": record.review_remark,
        }
        # 待审核期间回放第三方认证链接，供用户刷新页面后重新扫码
        if record.status == 0 and record.certify_url:
            data["certify_url"] = record.certify_url
        return ok(data)


@router.post("/submit")
async def submit_certification(data: FarmerCertSubmit, request: Request,
                               _: None = Depends(check_farmer)):
    """提交实名认证资料

    数据落库到 hy_certification_record（核心系统持有）。
    若有启用的插件渠道，自动调用插件 certify_initialize 发起第三方验证。
    """
    farmer_id = request.state.user_id

    async with async_session_factory() as db:
        # 检查是否已有待审核记录
        pending = await db.execute(
            select(CertificationRecord).where(
                CertificationRecord.farmer_id == farmer_id,
                CertificationRecord.status == 0,
            )
        )
        if pending.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="您已有待审核的认证记录")

        # 检查是否已认证通过
        certified = await db.execute(
            select(CertificationRecord).where(
                CertificationRecord.farmer_id == farmer_id,
                CertificationRecord.status == 1,
            )
        )
        if certified.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="您已通过实名认证")

        # 查找认证渠道：优先使用农户指定的渠道，否则取第一个启用的渠道
        selected_channel = None
        if data.channel and data.channel != "manual":
            ch_result = await db.execute(
                select(CertificationChannel).where(
                    CertificationChannel.plugin_name == data.channel,
                    CertificationChannel.status == 1,
                )
            )
            selected_channel = ch_result.scalar_one_or_none()
            if not selected_channel:
                raise HTTPException(status_code=400, detail="所选认证渠道不可用")
        elif not data.channel or data.channel == "manual":
            # 未指定渠道时，取第一个启用的渠道（兼容旧逻辑）
            ch_result = await db.execute(
                select(CertificationChannel)
                .where(CertificationChannel.status == 1)
                .order_by(CertificationChannel.sort_order, CertificationChannel.id)
                .limit(1)
            )
            selected_channel = ch_result.scalar_one_or_none()

        channel_name = selected_channel.plugin_name if selected_channel else "manual"

        # 创建认证记录 — 用户数据落库（核心系统持有）
        record = CertificationRecord(
            farmer_id=farmer_id,
            real_name=data.real_name,
            id_card=data.id_card,
            cert_type="personal",
            phone=data.phone,
            front_image=data.front_image,
            back_image=data.back_image,
            status=0,
            channel=channel_name,
            submit_time=china_now(),
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

        await active_log(
            "农户提交实名认证", "farmer_cert",
            rel_id=record.id, request=request
        )

        # 若有启用的插件渠道，调用插件发起第三方认证
        if selected_channel:
            plugin_cls = _load_plugin(selected_channel.plugin_name)
            if plugin_cls:
                try:
                    instance = plugin_cls(None, {})
                    channel_config = json.loads(selected_channel.config or "{}")
                    cert_data = await instance.certify_initialize(
                        real_name=data.real_name,
                        id_card=data.id_card,
                        channel_config=channel_config,
                        phone=data.phone,
                    )
                    # 存储 biz_no 与认证链接（链接持久化，供 /status 回放防丢失）
                    record.cert_no = cert_data.get("biz_no", "")
                    record.certify_url = cert_data.get("certify_url", "")
                    await db.commit()
                    return ok(
                        {"record_id": record.id, "certify_url": cert_data.get("certify_url", "")},
                        msg="认证已提交，请完成第三方验证",
                    )
                except Exception as e:
                    logger.error(f"插件认证初始化失败: {e}", exc_info=True)
                    # 插件调用失败 — 回退到人工审核，降级原因落库便于管理员追溯
                    record.channel = "manual"
                    record.review_remark = f"自动降级人工审核: {e}"[:512]
                    await db.commit()
                    return ok(
                        {"record_id": record.id, "fallback": True},
                        msg="第三方认证服务暂不可用，已转为人工审核，请等待管理员审核",
                    )

        # 无插件渠道 — 走人工审核流程
        return ok(
            {"record_id": record.id},
            msg="认证资料已提交，等待管理员审核",
        )


@router.post("/query")
async def query_cert_result(request: Request, _: None = Depends(check_farmer)):
    """查询认证结果

    若使用了插件认证且有待查询的 biz_no，自动调用插件 certify_query 并更新状态。
    防重入：同一农户的查询请求未结束前拒绝重复触发，
    避免高频点击反复调用支付宝 API 引发限流与并发竞态。
    """
    farmer_id = request.state.user_id
    if farmer_id in _query_inflight:
        return ok({"cert_status": 0, "message": "查询处理中，请稍候"})
    _query_inflight.add(farmer_id)
    try:
        return await _do_query(farmer_id, request)
    finally:
        _query_inflight.discard(farmer_id)


# /query 防重入集合：正在查询中的农户 ID
# 说明：内存方案仅对单实例部署有效（当前部署形态），
# 多实例场景下的状态一致性由 _update_record_status 的条件 UPDATE 兑底
_query_inflight: set = set()


async def _do_query(farmer_id: int, request: Request):
    """执行认证结果查询主流程"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(CertificationRecord).where(
                CertificationRecord.farmer_id == farmer_id,
                CertificationRecord.status == 0,
            ).order_by(CertificationRecord.id.desc()).limit(1)
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=400, detail="没有待审核的认证记录")

        # 若使用了插件认证且有 biz_no — 调用插件查询结果
        if record.channel != "manual" and record.cert_no:
            resp = await _query_via_plugin(record, db, request)
            if resp:
                return resp
            # 插件不可用/查询失败/并发竞态 — 重读最新状态后返回
            await db.refresh(record)

        status_msgs = {
            0: "等待管理员审核",
            1: "认证已通过",
            2: record.review_remark or "认证未通过",
        }
        return ok({
            "cert_status": record.status,
            "message": status_msgs.get(record.status, "等待管理员审核"),
        })


async def _query_via_plugin(record, db, request):
    """调用插件查询第三方认证结果并按结果更新记录状态

    Returns:
        响应字典；插件不可用/查询失败/并发竞态时返回 None，
        由调用方重读记录返回当前状态
    """
    plugin_cls = _load_plugin(record.channel)
    if not plugin_cls:
        return None
    try:
        # 获取渠道配置
        ch = (await db.execute(
            select(CertificationChannel).where(
                CertificationChannel.plugin_name == record.channel
            )
        )).scalar_one_or_none()
        channel_config = json.loads(ch.config or "{}") if ch else {}
        instance = plugin_cls(None, {})
        cert_result = await instance.certify_query(
            biz_no=record.cert_no,
            channel_config=channel_config,
        )
    except Exception as e:
        logger.warning(f"插件认证查询失败: {e}")
        return None

    # 检查是否需要人工复审
    manual_review = await get_config("cert_manual_review")
    if cert_result.get("passed"):
        if manual_review == "1":
            return ok({
                "cert_status": 0,
                "message": "第三方认证已通过，等待人工复审",
            })
        # 直接通过（条件 UPDATE 防并发覆盖）
        if await _update_record_status(
            db, record.id, {"status": 1, "review_time": china_now()}
        ):
            await active_log(
                "实名认证通过", "farmer_cert", rel_id=record.id, request=request
            )
            return ok({"cert_status": 1, "message": "认证已通过"})
        return None
    # 认证未通过
    reason = cert_result.get("failed_reason", "认证未通过")
    if await _update_record_status(
        db, record.id, {"status": 2, "review_remark": reason}
    ):
        await active_log(
            "实名认证未通过", "farmer_cert", rel_id=record.id, request=request
        )
        return ok({"cert_status": 2, "message": reason})
    return None


async def _update_record_status(db, record_id: int, values: dict) -> bool:
    """条件 UPDATE 更新认证记录状态（乐观并发控制）

    仅当记录仍处于待审核状态（status=0）时才更新，
    防止并发查询/管理员审批互相覆盖状态。

    Returns:
        True=更新成功, False=竞态失败（状态已被其他请求更新）
    """
    result = await db.execute(
        update(CertificationRecord).where(
            CertificationRecord.id == record_id,
            CertificationRecord.status == 0,
        ).values(**values)
    )
    await db.commit()
    return result.rowcount > 0
