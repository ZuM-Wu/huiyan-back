"""
邮件模板管理 API（管理员端）
提供邮件模板的增删改查、预览与测试发送能力
"""
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.notice_sender import replace_variables
from services.task.notice_worker import send_test_notice
from core.log.active_log import active_log
from core.response import ok, fail
from schemas.notice import (
    EmailTemplateCreate, EmailTemplateUpdate, EmailTemplateResponse,
    NoticeTestRequest
)
from core.notice_template_service import (
    search_email_templates,
    get_email_template,
    create_email_template,
    update_email_template,
    delete_email_template,
    get_email_template_by_name,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/notice/email-templates", tags=["邮件模板管理"])


@router.get("/list", dependencies=[Depends(require_permission("notice:list"))])
async def list_templates(
    action_key: str = Query(None, description="默认关联动作"),
    keywords: str = Query("", description="搜索关键词"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: None = Depends(check_admin),
):
    """邮件模板列表 — 支持多条件筛选（模板不绑定插件，无接口筛选）"""
    result = await search_email_templates(
        page=page, limit=limit,
        keywords=keywords, action_key=action_key or "",
    )
    return ok(result)


@router.post("/", dependencies=[Depends(require_permission("notice:create"))])
async def create_template(
    data: EmailTemplateCreate,
    request: Request,
    _: None = Depends(check_admin),
):
    """创建邮件模板（邮件无审核要求，不绑定插件接口）"""
    if await get_email_template_by_name(data.name):
        raise HTTPException(status_code=400, detail="该名称已存在")

    template_id = await create_email_template(data.model_dump())
    template = await get_email_template(template_id)

    logger.info(f"[邮件模板] 创建模板：{data.name}")
    await active_log(f"创建邮件模板：{data.name}", log_type="notice_email", rel_id=template_id, request=request)
    return ok(template)


@router.get("/{template_id}", dependencies=[Depends(require_permission("notice:list"))])
async def get_template(
    template_id: int,
    _: None = Depends(check_admin),
):
    """获取单个模板详情"""
    template = await get_email_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    return ok(template)


@router.put("/{template_id}", dependencies=[Depends(require_permission("notice:update"))])
async def update_template(
    template_id: int,
    data: EmailTemplateUpdate,
    request: Request,
    _: None = Depends(check_admin),
):
    """更新模板内容"""
    update_data = data.model_dump(exclude_unset=True)
    success = await update_email_template(template_id, update_data)
    if not success:
        raise HTTPException(status_code=404, detail="模板不存在")

    template = await get_email_template(template_id)
    logger.info(f"[邮件模板] 更新模板 ID={template_id}, 更新内容：{update_data}")
    await active_log(f"更新邮件模板 ID={template_id}", log_type="notice_email", rel_id=template_id, request=request)
    return ok(template)


@router.delete("/{template_id}", dependencies=[Depends(require_permission("notice:delete"))])
async def delete_template(
    template_id: int,
    request: Request,
    _: None = Depends(check_admin),
):
    """删除模板"""
    success = await delete_email_template(template_id)
    if not success:
        raise HTTPException(status_code=404, detail="模板不存在")

    logger.info(f"[邮件模板] 删除模板 ID={template_id}")
    await active_log(f"删除邮件模板 ID={template_id}", log_type="notice_email", rel_id=template_id, request=request)
    return ok(msg="删除成功")


# ========== 预览模板 ==========

@router.get("/{template_id}/preview", dependencies=[Depends(require_permission("notice:list"))])
async def preview_template(
    template_id: int,
    variables: str = Query("", description="测试变量 JSON 字符串，如 {\"code\": \"123456\"}"),
    _: None = Depends(check_admin),
):
    """预览邮件模板（带变量替换）"""
    # 解析变量 JSON 字符串
    var_dict = {}
    if variables:
        try:
            var_dict = json.loads(variables)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="variables 必须是合法的 JSON 字符串")

    template = await get_email_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")

    content = template["content"]
    subject = template["subject"]

    # 变量替换：将{name}替换为实际值
    for key, value in var_dict.items():
        placeholder = f"{{{key}}}"
        content = content.replace(placeholder, str(value) if value else "")
        subject = subject.replace(placeholder, str(value) if value else "")

    return ok({
        "subject": subject,
        "content": content,
        "original": template["content"],
        "variables_used": list(var_dict.keys())
    })


# ========== 测试发送（预留接口，由插件实现） ==========

@router.post("/test-send", dependencies=[Depends(require_permission("notice:test"))])
async def test_send(
    data: NoticeTestRequest,
    _: None = Depends(check_admin),
):
    """
    测试发送邮件

    加载模板 -> 变量替换 -> 组装载荷投递任务队列 -> 轮询发送日志返回结果
    （邮件模板不绑定插件，发送接口由核心 Worker 自动路由到当前启用的发送接口）
    """
    template = await get_email_template(data.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")

    tpl_subject = template["subject"]
    tpl_content = template["content"]
    tpl_response = EmailTemplateResponse.model_validate(template).model_dump()

    # 变量替换（主题与正文）
    subject = replace_variables(tpl_subject, data.variables or {})
    content = replace_variables(tpl_content, data.variables or {})

    # 组装发送载荷（与 notice_worker.send_notice_task 兼容），
    # 经 send_test_notice 投递任务队列并轮询发送日志获取即时结果；
    # 邮件模板不绑定插件，interface 留空由 Worker 自动路由到已启用的邮件接口
    payload = {
        "channel": "email",
        "interface": "",
        "recipient": data.recipient,
        "content": content,
        "subject": subject,
        "attachments": [],
        "local_template_id": data.template_id,
        "variables": data.variables or {},
    }
    result = await send_test_notice(payload)
    if result["success"]:
        return ok(
            {"subject": subject, "template": tpl_response},
            msg=result["msg"] or "发送成功",
        )
    return fail(400, result["msg"] or "发送失败")
