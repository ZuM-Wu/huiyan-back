# -*- coding: utf-8 -*-
"""
迁移脚本 - 预置基础邮件模板种子数据
运行方式：在 app 目录下执行
    python migrations/email_template_seed.py

邮件模板不依赖平台审核，可以直接预置；模板不绑定插件接口。
幂等：以 name 判断是否已存在。
"""
import asyncio
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from core.db.base import engine

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# 基础邮件模板定义
EMAIL_TEMPLATES = [
    {
        "name": "验证码通知",
        "subject": "【慧眼护农】验证码通知",
        "content": """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 20px; color: #333;">
<div style="max-width: 600px; margin: 0 auto; border: 1px solid #e8e8e8; border-radius: 8px; padding: 32px;">
    <h2 style="color: #0052d9; margin-top: 0;">验证码通知</h2>
    <p>您好，您的验证码为：</p>
    <div style="font-size: 32px; font-weight: bold; color: #0052d9; padding: 16px 0; letter-spacing: 4px;">{code}</div>
    <p>有效期 <strong>{minutes}</strong> 分钟，请勿泄露给他人。</p>
    <hr style="border: none; border-top: 1px solid #e8e8e8; margin: 24px 0;">
    <p style="font-size: 12px; color: #999;">如果您未请求此验证码，请忽略本邮件。</p>
    <p style="font-size: 12px; color: #999;">— 慧眼护农团队</p>
</div>
</body>
</html>""",
        "action_key": "verify_code",
    },
    {
        "name": "系统推送通知",
        "subject": "【慧眼护农】系统通知",
        "content": """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 20px; color: #333;">
<div style="max-width: 600px; margin: 0 auto; border: 1px solid #e8e8e8; border-radius: 8px; padding: 32px;">
    <h2 style="color: #0052d9; margin-top: 0;">系统通知</h2>
    <div style="padding: 16px 0; line-height: 1.6;">{content}</div>
    <hr style="border: none; border-top: 1px solid #e8e8e8; margin: 24px 0;">
    <p style="font-size: 12px; color: #999;">— 慧眼护农团队</p>
</div>
</body>
</html>""",
        "action_key": "system_push",
    },
]


async def migrate():
    """预置基础邮件模板（幂等）"""
    async with engine.begin() as conn:
        # 确保 hy_email_template 表存在
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_email_template'"
        ))
        if result.scalar() == 0:
            logger.info("[邮件模板种子] hy_email_template 表不存在，请先运行 notice_module.py")
            return

        inserted = 0
        for tpl in EMAIL_TEMPLATES:
            # 幂等：以 name 判断
            check = await conn.execute(text(
                "SELECT id FROM hy_email_template WHERE name = :name"
            ), {"name": tpl["name"]})
            if check.scalar() is not None:
                logger.info("[跳过] 模板已存在: %s", tpl['name'])
                continue

            await conn.execute(text(
                "INSERT INTO hy_email_template (name, subject, content, action_key) "
                "VALUES (:name, :subject, :content, :action_key)"
            ), tpl)
            inserted += 1
            logger.info("[新增] %s", tpl['name'])

        logger.info("[邮件模板种子] 完成，新增 %d 条", inserted)


if __name__ == "__main__":
    asyncio.run(migrate())
