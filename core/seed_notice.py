"""
通知模块种子数据
预置常用通知动作与验证码短信模板，幂等写入 hy_notice_action / hy_sms_template 表
"""
import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)

# 邮件模板基础结构（logo/二维码/页脚固定不变，正文和CTA按钮可替换）
# 基于用户提供的标准 HTML 邮件模板，仅替换公司名称为「慧眼护农」
_EMAIL_BASE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>body,table,td,a{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}table,td{mso-table-lspace:0pt;mso-table-rspace:0pt}img{-ms-interpolation-mode:bicubic;border:0;height:auto;line-height:100%;outline:none;text-decoration:none}body{height:100%!important;margin:0;padding:0;width:100%!important;background-color:#f4f7f6;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif}@media screen and (max-width:600px){.email-container{width:100%!important;margin:auto!important}.fluid-pad{padding:20px 15px!important}.body-text{font-size:15px!important;line-height:1.6!important}.cta-button{width:100%!important;display:block!important;box-sizing:border-box!important}}</style>
</head><body style="margin:0;padding:0;background-color:#f4f7f6;"><center style="width:100%;background-color:#f4f7f6;"><div class="email-container" style="max-width:600px;margin:0 auto;">
<table style="margin:auto;max-width:600px;background-color:#ffffff;overflow:hidden;box-shadow:0 4px 10px rgba(0,0,0,0.05);margin-top:20px;margin-bottom:20px;" role="presentation" border="0" width="100%" cellspacing="0" cellpadding="0" align="center"><tbody>
<tr><td style="padding:20px 30px;" align="left" bgcolor="#ffffff"><a style="text-decoration:none;display:inline-block;" href="/admin/dashboard"><img style="display:block;border:0;max-width:100%;height:auto;" src="https://console.xunmao.net/upload/common/default/d1cca489c9e1ed1b15bb130cd2cff6151765618174%5Eea438de20a51c5474e3df3755718a10b175488289.png" alt="慧眼护农" width="130" /></a></td></tr>
<tr><td style="height:4px;background:linear-gradient(90deg,#ff416c 0%,#ff4b2b 50%,#0052cc 100%);background-color:#ff4b2b;line-height:4px;font-size:4px;">&nbsp;</td></tr>
<tr><td class="fluid-pad" style="padding:40px 30px;color:#333333;">{body}<table style="margin:0 0 0 auto;" role="presentation" border="0" cellspacing="0" cellpadding="0" align="right"><tbody><tr><td style="background:#0052cc;text-align:center;border-radius:2px;"><a class="cta-button" style="background:#0052cc;border:1px solid #0052cc;font-family:sans-serif;font-size:14px;line-height:1.5;text-decoration:none;padding:10px 24px;color:#ffffff;display:inline-block;font-weight:bold;border-radius:2px;" href="{cta_link}">{cta_text}</a></td></tr></tbody></table><div style="clear:both;">&nbsp;</div><p class="body-text" style="margin:25px 0 0 0;font-size:16px;line-height:1.5;text-align:right;">慧眼护农智慧农业系统<br/><span style="color:#333333;">{send_time}</span></p></td></tr>
<tr><td class="fluid-pad" style="padding:30px 20px;background-color:#f9f9f9;border-top:1px solid #eeeeee;" align="center"><div style="margin-bottom:20px;"><img style="display:block;margin:0 auto;border:1px solid #dddddd;border-radius:4px;padding:5px;background:#ffffff;" src="https://console.xunmao.net/upload/common/default/0a9f8c87b04932a05d37e98a840bce761778232295^qrcode-302.png" alt="二维码" width="100" height="100" /><p style="margin:10px 0 0 0;font-size:13px;color:#555555;font-weight:bold;">扫码加入QQ用户群，获取最新资讯</p></div><p style="margin:0 0 10px 0;font-size:12px;line-height:1.5;color:#888888;">2023-2026 &copy; 慧眼护农. 保留所有权利.</p><p style="margin:0;font-size:12px;line-height:1.5;color:#888888;">如果您不想继续接收此类邮件，请点击 <a style="color:#0f172a;text-decoration:underline;" href="/admin/notice-config">取消订阅</a>。</p></td></tr>
</tbody></table></div></center></body></html>"""


def _build_email_content(body: str, cta_text: str, cta_link: str) -> str:
    """基于标准模板生成完整邮件 HTML（{send_time} 保留为占位符，发送时替换）"""
    html = _EMAIL_BASE.replace("{body}", body)
    html = html.replace("{cta_text}", cta_text)
    html = html.replace("{cta_link}", cta_link)
    return html

# 需要预创建短信模板并自动启用 SMS 的动作（action_key → 模板标题、内容）
_VERIFY_CODE_TEMPLATES = {
    "verify_code_login": {
        "title": "验证码登录",
        "content": "您的登录验证码是 @var(code)，@var(expire) 分钟内有效，请勿泄露给他人。",
    },
    "verify_code_register": {
        "title": "注册验证码",
        "content": "您的注册验证码是 @var(code)，@var(expire) 分钟内有效，请勿泄露给他人。",
    },
    "password_reset": {
        "title": "密码重置验证码",
        "content": "您正在重置密码，验证码是 @var(code)，@var(expire) 分钟内有效。如非本人操作请忽略。",
    },
}


async def seed_notice_actions(db):
    """
    写入预置通知动作种子数据（hy_notice_action 表）

    幂等设计：以 action_key 为唯一键，已存在则跳过，不覆盖管理员配置。
    验证码类动作自动预创建短信模板并启用 SMS，管理员只需配置 SMS 插件接口即可使用。
    """
    from core.db.notice import NoticeAction

    # 字段: (action_key, action_name, action_type, trigger_inbox)
    actions = [
        ("user_registered",  "用户注册",      "user",       True),
        ("password_reset",   "密码重置",      "user",       False),
        ("cert_approved",    "实名认证通过",   "user",       True),
        ("cert_rejected",    "实名认证驳回",   "user",       True),
        ("order_created",     "订单创建",      "order",      False),
        ("order_completed",  "订单完成",      "order",      True),
        ("verify_code_login",    "验证码登录",    "user",   False),
        ("verify_code_register", "注册验证码",    "user",   False),
        # 业务操作通知
        ("area_farmer_bindied",  "产区绑定农户",    "area",   True),
        ("plot_created",         "新增地块通知",    "area",   True),
        ("batch_status_change",  "批次状态变更",    "area",   True),
        ("system_maintenance",   "系统维护通知",    "system", True),
        # 气象预警通知（农户+管理员双动作，各走各的动作配置）
        ("weather_alert",        "气象预警通知",   "weather", True),
        ("weather_alert_admin",  "气象预警-管理员", "weather", True),
    ]

    inserted = 0
    for action_key, action_name, action_type, trigger_inbox in actions:
        existing = (await db.execute(
            select(NoticeAction).where(NoticeAction.action_key == action_key)
        )).scalar_one_or_none()
        if not existing:
            db.add(NoticeAction(
                action_key=action_key,
                action_name=action_name,
                action_type=action_type,
                sms_enabled=False,
                email_enabled=False,
                trigger_inbox=trigger_inbox,
            ))
            inserted += 1

    # 先提交动作，确保后续查询能取到 ID
    await db.flush()
    logger.info("[通知动作种子] 新增 %d 条预置动作（跳过已存在）", inserted)

    # 为验证码类动作预创建短信模板并关联启用
    await _seed_verify_code_sms_templates(db)
    # 为验证码类动作预创建邮件模板并关联启用
    await _seed_verify_code_email_templates(db)
    # 业务通知邮件模板
    await _seed_business_email_templates(db)
    # 任务失败告警邮件模板
    await _seed_task_alert_email_templates(db)


async def _seed_verify_code_sms_templates(db):
    """
    为验证码类动作（登录/注册/密码重置）预创建默认短信模板，
    并将动作的 sms_enabled 设为 True、sms_template_id 关联到模板。

    幂等规则：
    - 模板已存在（按 action_key 查）则跳过创建
    - 动作的 sms_template_id > 0（管理员已手动配置）则跳过关联
    """
    from core.db.notice import NoticeAction, SmsTemplate

    for action_key, tpl_info in _VERIFY_CODE_TEMPLATES.items():
        # 检查是否已有该 action_key 对应的模板
        existing_tpl = (await db.execute(
            select(SmsTemplate).where(SmsTemplate.action_key == action_key)
        )).scalar_one_or_none()

        if not existing_tpl:
            # 创建系统预置模板：interface 留空表示发送时使用通知动作配置的 SMS 插件
            # template_id 用 action_key 区分，避免触发 uk_interface_template 唯一索引冲突
            tpl = SmsTemplate(
                interface="",
                type=0,
                template_id=action_key,
                title=tpl_info["title"],
                content=tpl_info["content"],
                signature="",
                status=2,  # 默认已通过（本地模板无需第三方审核）
                action_key=action_key,
                remark="系统预置验证码模板",
            )
            db.add(tpl)
            await db.flush()  # 获取自增 ID
            tpl_id = tpl.id
            logger.info("[通知种子] 创建验证码短信模板: %s (id=%d)", action_key, tpl_id)
        else:
            tpl_id = existing_tpl.id

        # 关联到对应动作（仅当动作尚未手动配置模板时）
        action = (await db.execute(
            select(NoticeAction).where(NoticeAction.action_key == action_key)
        )).scalar_one_or_none()
        if action and action.sms_template_id == 0:
            action.sms_enabled = True
            action.sms_template_id = tpl_id
            logger.info(
                "[通知种子] 动作 %s 已启用 SMS 并关联模板 id=%d",
                action_key, tpl_id,
            )


# 验证码邮件模板内容
_VERIFY_CODE_EMAIL_TEMPLATES = {
    "verify_code_login": {
        "name": "验证码登录",
        "subject": "【{site_name}】登录验证码",
        "content": "<p>您好，您的登录验证码是：<strong>{code}</strong>，{expire} 分钟内有效，请勿泄露给他人。</p>",
    },
    "verify_code_register": {
        "name": "注册验证码",
        "subject": "【{site_name}】注册验证码",
        "content": "<p>您好，您的注册验证码是：<strong>{code}</strong>，{expire} 分钟内有效，请勿泄露给他人。</p>",
    },
    "password_reset": {
        "name": "密码重置验证码",
        "subject": "【{site_name}】密码重置验证码",
        "content": "<p>您正在重置密码，验证码是：<strong>{code}</strong>，{expire} 分钟内有效。如非本人操作请忽略。</p>",
    },
}


async def _seed_verify_code_email_templates(db):
    """
    为验证码类动作预创建默认邮件模板，
    并将动作的 email_enabled 设为 True、email_template_id 关联到模板。

    幂等规则：
    - 模板已存在（按 action_key 查）则跳过创建
    - 动作的 email_template_id > 0（管理员已手动配置）则跳过关联
    """
    from core.db.notice import NoticeAction, EmailTemplate

    for action_key, tpl_info in _VERIFY_CODE_EMAIL_TEMPLATES.items():
        existing_tpl = (await db.execute(
            select(EmailTemplate).where(EmailTemplate.action_key == action_key)
        )).scalar_one_or_none()

        if not existing_tpl:
            tpl = EmailTemplate(
                name=tpl_info["name"],
                subject=tpl_info["subject"],
                content=tpl_info["content"],
                action_key=action_key,
            )
            db.add(tpl)
            await db.flush()
            tpl_id = tpl.id
            logger.info("[通知种子] 创建验证码邮件模板: %s (id=%d)", action_key, tpl_id)
        else:
            tpl_id = existing_tpl.id

        # 关联到对应动作（仅当动作尚未手动配置模板时）
        action = (await db.execute(
            select(NoticeAction).where(NoticeAction.action_key == action_key)
        )).scalar_one_or_none()
        if action and action.email_template_id == 0:
            action.email_enabled = True
            action.email_template_id = tpl_id
            logger.info(
                "[通知种子] 动作 %s 已启用 Email 并关联模板 id=%d",
                action_key, tpl_id,
            )


# 业务通知邮件模板配置
# 已有模板（area_farmer_bindied）使用 content 字段；新增模板使用 body+cta_text+cta_link
# 新增模板基于标准 HTML 邮件模板结构，通过 _build_email_content 生成完整 HTML
_BUSINESS_EMAIL_TEMPLATES = {
    # 已有模板（保持不变）
    "area_farmer_bindied": {
        "name": "产区绑定通知",
        "subject": "【{site_name}】产区绑定通知",
        "content": "<p>管理员已将您绑定到产区「{area_name}」，您可以在农户端查看该产区的地块与种植信息。</p>",
    },
    # 新增模板（基于标准 HTML 邮件模板）
    "user_registered": {
        "name": "用户注册通知", "subject": "【慧眼护农】用户注册通知",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的用户</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;">新用户已完成注册，请及时登录管理后台查看用户信息。</p>',
        "cta_text": "前往查看", "cta_link": "/admin/user",
    },
    "cert_approved": {
        "name": "实名认证通过", "subject": "【慧眼护农】实名认证通过",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的用户</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;">您的实名认证已通过审核，现在可以使用全部功能。</p>',
        "cta_text": "前往查看", "cta_link": "/admin/certification",
    },
    "cert_rejected": {
        "name": "实名认证驳回", "subject": "【慧眼护农】实名认证驳回",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的用户</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;">您的实名认证未通过审核，请重新提交认证信息。</p>',
        "cta_text": "前往查看", "cta_link": "/admin/certification",
    },
    "order_created": {
        "name": "订单创建通知", "subject": "【慧眼护农】订单创建通知",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的用户</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;">新订单已创建，请及时查看订单详情。</p>',
        "cta_text": "前往查看", "cta_link": "/admin/dashboard",
    },
    "order_completed": {
        "name": "订单完成通知", "subject": "【慧眼护农】订单完成通知",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的用户</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;">订单已完成，请查看订单详情。</p>',
        "cta_text": "前往查看", "cta_link": "/admin/dashboard",
    },
    "plot_created": {
        "name": "新增地块通知", "subject": "【慧眼护农】新增地块通知",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的用户</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;">产区已新增地块信息，请及时查看。</p>',
        "cta_text": "前往查看", "cta_link": "/admin/production-area",
    },
    "batch_status_change": {
        "name": "批次状态变更", "subject": "【慧眼护农】批次状态变更",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的用户</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;">种植批次状态已更新，请查看最新状态。</p>',
        "cta_text": "前往查看", "cta_link": "/admin/planting-batch",
    },
    "system_maintenance": {
        "name": "系统维护通知", "subject": "【慧眼护农】系统维护通知",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的用户</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;">系统将于指定时间进行维护升级，届时可能暂时无法访问，请提前做好准备。</p>',
        "cta_text": "前往查看", "cta_link": "/admin/dashboard",
    },
    # 气象预警通知（农户版，通知产区绑定农户）
    "weather_alert": {
        "name": "气象预警通知", "subject": "【慧眼护农】{area_name}气象预警",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的用户</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;">您绑定的产区「{area_name}」收到气象预警，请及时关注。</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>预警标题:</b> {alert_title}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>预警等级:</b> {alert_level}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>预警详情:</b> {alert_text}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>生效时间:</b> {start_time} 至 {end_time}</p>',
        "cta_text": "前往查看", "cta_link": "/farmer/production-area",
        "auto_enable": False,
    },
    # 气象预警告警（管理员版，通知管理员）
    "weather_alert_admin": {
        "name": "气象预警告警", "subject": "【慧眼护农】产区气象预警告警",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的管理员</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;">产区「{area_name}」收到气象预警，请及时关注。</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>预警标题:</b> {alert_title}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>预警等级:</b> {alert_level}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>预警详情:</b> {alert_text}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>生效时间:</b> {start_time} 至 {end_time}</p>',
        "cta_text": "前往查看", "cta_link": "/admin/weather",
        "auto_enable": False,
    },
}


async def _seed_business_email_templates(db):
    """
    为业务通知动作预创建默认邮件模板并关联启用。
    幂等规则同 _seed_verify_code_email_templates。
    """
    from core.db.notice import NoticeAction, EmailTemplate

    for action_key, tpl_info in _BUSINESS_EMAIL_TEMPLATES.items():
        existing_tpl = (await db.execute(
            select(EmailTemplate).where(EmailTemplate.action_key == action_key)
        )).scalar_one_or_none()

        if not existing_tpl:
            # 新模板使用 body+cta 生成完整 HTML；已有模板直接使用 content
            if "body" in tpl_info:
                content = _build_email_content(
                    tpl_info["body"], tpl_info["cta_text"], tpl_info["cta_link"],
                )
            else:
                content = tpl_info["content"]
            tpl = EmailTemplate(
                name=tpl_info["name"],
                subject=tpl_info["subject"],
                content=content,
                action_key=action_key,
            )
            db.add(tpl)
            await db.flush()
            tpl_id = tpl.id
            logger.info("[通知种子] 创建业务通知邮件模板: %s (id=%d)", action_key, tpl_id)
        else:
            tpl_id = existing_tpl.id

        # 关联到对应动作（仅当动作尚未手动配置模板时）
        action = (await db.execute(
            select(NoticeAction).where(NoticeAction.action_key == action_key)
        )).scalar_one_or_none()
        if action and action.email_template_id == 0:
            # weather 类被动通知默认不自动启用邮件，由发送设置页显式开启
            auto_enable = tpl_info.get("auto_enable", True)
            action.email_template_id = tpl_id
            if auto_enable:
                action.email_enabled = True
                logger.info(
                    "[通知种子] 动作 %s 已启用 Email 并关联业务模板 id=%d",
                    action_key, tpl_id,
                )
            else:
                logger.info(
                    "[通知种子] 动作 %s 仅关联邮件模板 id=%d（不自动启用，由发送设置页显式开启）",
                    action_key, tpl_id,
                )


# 任务执行失败告警邮件模板配置
# 每个任务失败时发送邮件通知管理员，正文包含任务名称、错误信息、失败时间
_TASK_ALERT_EMAIL_TEMPLATES = {
    "clean_repeat_cache": {
        "name": "清理防重复缓存失败", "subject": "【慧眼护农】任务执行失败告警",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的管理员</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;">任务「清理防重复缓存」执行失败，请及时处理。</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>任务标识:</b> {task_name}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>错误信息:</b> {error_msg}</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;"><b>失败时间:</b> {fail_time}</p>',
        "cta_text": "前往处理", "cta_link": "/admin/task_monitor",
    },
    "clean_old_logs": {
        "name": "清理过期日志失败", "subject": "【慧眼护农】任务执行失败告警",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的管理员</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;">任务「清理过期系统日志」执行失败，请及时处理。</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>任务标识:</b> {task_name}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>错误信息:</b> {error_msg}</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;"><b>失败时间:</b> {fail_time}</p>',
        "cta_text": "前往处理", "cta_link": "/admin/task_monitor",
    },
    "clean_task_logs": {
        "name": "清理任务日志失败", "subject": "【慧眼护农】任务执行失败告警",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的管理员</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;">任务「清理过期任务日志」执行失败，请及时处理。</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>任务标识:</b> {task_name}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>错误信息:</b> {error_msg}</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;"><b>失败时间:</b> {fail_time}</p>',
        "cta_text": "前往处理", "cta_link": "/admin/task_monitor",
    },
    "weather_pull": {
        "name": "天气拉取失败", "subject": "【慧眼护农】任务执行失败告警",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的管理员</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;">任务「天气全量拉取」执行失败，请及时处理。</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>任务标识:</b> {task_name}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>错误信息:</b> {error_msg}</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;"><b>失败时间:</b> {fail_time}</p>',
        "cta_text": "前往处理", "cta_link": "/admin/task_monitor",
    },
    "weather_daily_finalize": {
        "name": "天气日终定格失败", "subject": "【慧眼护农】任务执行失败告警",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的管理员</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;">任务「天气日终定格」执行失败，请及时处理。</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>任务标识:</b> {task_name}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>错误信息:</b> {error_msg}</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;"><b>失败时间:</b> {fail_time}</p>',
        "cta_text": "前往处理", "cta_link": "/admin/task_monitor",
    },
    "weather_clean": {
        "name": "天气数据清理失败", "subject": "【慧眼护农】任务执行失败告警",
        "body": '<p class="body-text" style="margin:0 0 15px 0;font-size:16px;font-weight:bold;line-height:1.5;">尊敬的管理员</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;">任务「天气历史数据清理」执行失败，请及时处理。</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>任务标识:</b> {task_name}</p><p class="body-text" style="margin:0 0 15px 0;font-size:16px;line-height:1.6;color:#555555;"><b>错误信息:</b> {error_msg}</p><p class="body-text" style="margin:0 0 25px 0;font-size:16px;line-height:1.6;color:#555555;"><b>失败时间:</b> {fail_time}</p>',
        "cta_text": "前往处理", "cta_link": "/admin/task_monitor",
    },
}


async def _seed_task_alert_email_templates(db):
    """
    为任务执行失败告警预创建邮件模板。
    这些模板不关联 NoticeAction，由 alert_service 按 action_key=task_name 加载。
    幂等规则：模板已存在则跳过创建。
    """
    from core.db.notice import EmailTemplate

    for action_key, tpl_info in _TASK_ALERT_EMAIL_TEMPLATES.items():
        existing_tpl = (await db.execute(
            select(EmailTemplate).where(EmailTemplate.action_key == action_key)
        )).scalar_one_or_none()

        if not existing_tpl:
            content = _build_email_content(
                tpl_info["body"], tpl_info["cta_text"], tpl_info["cta_link"],
            )
            tpl = EmailTemplate(
                name=tpl_info["name"],
                subject=tpl_info["subject"],
                content=content,
                action_key=action_key,
            )
            db.add(tpl)
            await db.flush()
            logger.info("[通知种子] 创建任务告警邮件模板: %s (id=%d)", action_key, tpl.id)
