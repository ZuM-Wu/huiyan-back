"""
短信模板管理 API（管理员端）
提供国内/国际短信模板的增删改查、提交审核与测试发送能力
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.notice_sender import replace_variables
from services.task.notice_worker import (
    sms_template_submit, sms_template_delete, sms_template_query_status, send_test_notice,
)
from core.log.active_log import active_log
from core.response import ok, fail
from schemas.notice import (
    SmsTemplateCreate, SmsTemplateUpdate, SmsTemplateResponse,
    NoticeTestRequest
)
from core.notice_template_service import (
    search_sms_templates,
    get_sms_template,
    create_sms_template,
    update_sms_template,
    delete_sms_template,
    check_sms_template_exists,
    list_sms_templates_by_ids,
    list_sms_templates_by_interface_status,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/notice/sms-templates", tags=["短信模板管理"])


@router.get("/list", dependencies=[Depends(require_permission("notice:list"))])
async def list_templates(
    interface: str = Query(None, description="接口标识筛选"),
    type: int = Query(None, ge=0, le=1, description="类型筛选 0=国内 1=国际"),
    status: int = Query(None, ge=0, le=3, description="状态筛选"),
    action_key: str = Query(None, description="默认关联动作"),
    keywords: str = Query("", description="搜索关键词"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """短信模板列表 — 支持多条件筛选"""
    result = await search_sms_templates(
        page=page, limit=limit,
        interface=interface or "", type=type, status=status,
        action_key=action_key or "", keywords=keywords,
    )
    return ok(result)


@router.post("/", dependencies=[Depends(require_permission("notice:create"))])
async def create_template(
    data: SmsTemplateCreate,
    request: Request,
    _: None = Depends(check_admin),
):
    """创建短信模板"""
    # 检查唯一性（interface + template_id）
    if data.template_id:
        if await check_sms_template_exists(data.interface, data.template_id):
            raise HTTPException(status_code=400, detail="该第三方模板 ID 已存在")

    template_id = await create_sms_template(data.model_dump())
    template = await get_sms_template(template_id)

    logger.info(f"[短信模板] 创建模板：{data.title}, interface={data.interface}")
    await active_log(f"创建短信模板：{data.title}", log_type="notice_sms", rel_id=template_id, request=request)
    return ok(template)


@router.get("/{template_id}", dependencies=[Depends(require_permission("notice:list"))])
async def get_template(
    template_id: int,
    _: None = Depends(check_admin),
):
    """获取单个模板详情"""
    template = await get_sms_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    return ok(template)


@router.put("/{template_id}", dependencies=[Depends(require_permission("notice:update"))])
async def update_template(
    template_id: int,
    data: SmsTemplateUpdate,
    request: Request,
    _: None = Depends(check_admin),
):
    """更新模板内容"""
    update_data = data.model_dump(exclude_unset=True)
    success = await update_sms_template(template_id, update_data)
    if not success:
        raise HTTPException(status_code=404, detail="模板不存在")

    template = await get_sms_template(template_id)
    logger.info(f"[短信模板] 更新模板 ID={template_id}, 更新内容：{update_data}")
    await active_log(f"更新短信模板 ID={template_id}", log_type="notice_sms", rel_id=template_id, request=request)
    return ok(template)


@router.delete("/{template_id}", dependencies=[Depends(require_permission("notice:delete"))])
async def delete_template(
    template_id: int,
    request: Request,
    _: None = Depends(check_admin),
):
    """删除模板"""
    template = await get_sms_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")

    # 如果模板已提交到第三方平台，先经服务层调用插件删除（失败仅告警）
    if template["template_id"] and template["interface"]:
        await sms_template_delete(
            template["interface"], template["template_id"], template.get("type", 0)
        )

    await delete_sms_template(template_id)

    logger.info(f"[短信模板] 删除模板 ID={template_id}")
    await active_log(f"删除短信模板 ID={template_id}", log_type="notice_sms", rel_id=template_id, request=request)
    return ok(msg="已删除")


# ========== 批量提交审核（真正调用插件 API 提交到 SMS 平台） ==========

@router.post("/submit", dependencies=[Depends(require_permission("notice:update"))])
async def submit_for_review(
    request: Request,
    template_ids: list[int] = Query(..., description="模板 ID 列表"),
    _: None = Depends(check_admin),
):
    """
    批量提交审核

    调用插件的 create_cn_template/create_global_template 方法
    将模板真正提交到 SMS 平台审核，并根据平台返回更新本地状态
    """
    templates = await list_sms_templates_by_ids(template_ids)

    submitted = 0
    errors = []
    for template in templates:
        # 只处理未提交(0)和未通过(3)的模板
        if template["status"] not in (0, 3):
            continue

        # 经服务层调用插件提交到平台
        api_result = await sms_template_submit(
            template["interface"], template["title"], template["content"], template["type"] or 0)

        # 根据平台返回更新本地状态
        if api_result.get("status") == "success":
            tpl_data = api_result.get("template", {})
            new_template_id = tpl_data.get("template_id", "")
            update_fields = {}
            if new_template_id:
                update_fields["template_id"] = str(new_template_id)
            raw_status = tpl_data.get("template_status", 1)
            update_fields["status"] = (
                max(0, min(3, int(raw_status)))
                if isinstance(raw_status, (int, float)) else 1
            )
            if update_fields:
                await update_sms_template(template["id"], update_fields)
            submitted += 1
        else:
            errors.append(f"{template['title']}: {api_result.get('msg', '提交失败')}")

    logger.info(f"[短信模板] 批量提交审核：成功{submitted}个，失败{len(errors)}个")

    await active_log(f"批量提交短信模板审核：成功{submitted}个", log_type="notice_sms", request=request)
    return ok({"submitted": submitted, "errors": errors})


# ========== 同步平台审核状态 ==========

async def _sync_one_template(interface: str, tpl: dict) -> int | None:
    """
    同步单个模板的平台审核状态（sync_template_status 的循环体辅助函数）

    返回新的 status 值（发生更新时），或 None（未更新 / 调用失败）。
    插件解析/调用失败仅告警不抛出，非法状态值 / 状态未变化均视为未更新。
    """
    if not tpl.get("template_id"):
        return None
    res = await sms_template_query_status(interface, tpl["template_id"], tpl.get("type", 0))
    if res.get("status") != "success":
        return None
    new_status = res.get("template", {}).get("template_status")
    if new_status is None:
        return None
    try:
        new_val = max(0, min(3, int(new_status)))
    except (TypeError, ValueError):
        return None
    if new_val == tpl.get("status"):
        return None
    return new_val


@router.post("/sync-status", dependencies=[Depends(require_permission("notice:update"))])
async def sync_template_status(
    interface: str = Query(..., description="插件接口标识"),
    _: None = Depends(check_admin),
):
    """
    同步平台审核状态

    查询所有 status=1(审核中) 的模板，经服务层调用插件 get_template_status
    获取平台真实审核结果并回写本地
    """
    pending = await list_sms_templates_by_interface_status(interface, 1)
    updated = 0

    for tpl in pending:
        new_status = await _sync_one_template(interface, tpl)
        if new_status is not None:
            await update_sms_template(tpl["id"], {"status": new_status})
            updated += 1

    logger.info(f"[短信模板] 状态同步完成：更新{updated}个，待审核{len(pending)}个")
    return ok({"synced": updated, "pending": len(pending)})


# ========== 测试发送（调用插件真实发送） ==========

@router.post("/test-send", dependencies=[Depends(require_permission("notice:test"))])
async def test_send(
    data: NoticeTestRequest,
    _: None = Depends(check_admin),
):
    """
    测试发送短信

    加载模板 -> 校验审核状态 -> 变量替换 -> 组装载荷投递任务队列 -> 轮询发送日志返回结果
    """
    template = await get_sms_template(data.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")

    logger.info(
        f"[短信模板] 测试发送检查: tpl_id={template['id']}, status={template['status']}, "
        f"interface={template['interface']}, template_id={template['template_id']}"
    )

    # 审核状态校验：必须审核通过(status=2)才能测试发送
    _STATUS_NAMES = {0: "未提交", 1: "审核中", 2: "已通过", 3: "未通过"}
    if template["status"] != 2:
        raise HTTPException(
            status_code=400,
            detail="模板需通过审核后才能测试发送（当前状态："
                   + _STATUS_NAMES.get(template["status"], f"未知({template['status']})") + "）"
        )

    tpl_content = template["content"]
    tpl_interface = template["interface"]
    tpl_template_id = template["template_id"] or ""
    tpl_type = template["type"] or 0
    tpl_response = SmsTemplateResponse.model_validate(template).model_dump()

    # 变量替换
    content = replace_variables(tpl_content, data.variables or {})

    # 组装发送载荷（与 notice_worker.send_notice_task 兼容），
    # 经 send_test_notice 投递任务队列并轮询发送日志获取即时结果；
    # 签名由插件 send 方法内部处理（从 config 获取 sign 字段自动拼接），此处不再手动拼接
    payload = {
        "channel": "sms",
        "interface": tpl_interface,
        "sms_type": tpl_type,
        "recipient": data.recipient,
        "content": content,
        "template_id": tpl_template_id,
        "local_template_id": data.template_id,
        "variables": data.variables or {},
    }
    logger.info(
        f"[短信模板] 测试发送: interface={tpl_interface}, template_id={tpl_template_id}, "
        f"mobile={data.recipient}, type={'国际' if tpl_type == 1 else '国内'}"
    )
    result = await send_test_notice(payload)
    if result["success"]:
        return ok(
            {"content": content, "template": tpl_response},
            msg=result["msg"] or "发送成功",
        )
    return fail(400, result["msg"] or "发送失败")
