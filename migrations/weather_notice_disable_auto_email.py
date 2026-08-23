# -*- coding: utf-8 -*-
"""
迁移脚本 - 关闭天气通知动作被种子越权自动开启的邮件开关

背景：
    core/seed_notice.py 的 _seed_business_email_templates 历史上把
    weather_alert / weather_alert_admin 与业务通知混在一起，无条件
    email_enabled=True，导致管理员未在发送设置页显式开启却持续收到
    天气预警邮件。种子已修正为仅关联模板不自动启用（auto_enable=False），
    本迁移负责清理存量库中已被污染的开关，保留 email_template_id 模板
    关联，便于管理员后续在发送设置页显式开启。

    通过 migrations/registry.py 注册表统一调度，迁移只执行一次；
    管理员之后手动开启 weather 邮件不会被本迁移回关（run_pending 对已
    标记 applied 的迁移名直接 continue）。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def probe(db) -> bool:
    """没有 weather 动作处于 email_enabled=1 即视为已满足（含动作不存在的极端情况）"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM hy_notice_action "
        "WHERE action_key IN ('weather_alert','weather_alert_admin') "
        "AND email_enabled = 1"
    ))
    return result.scalar() == 0


async def apply(db):
    """关闭天气动作被种子自动开启的邮件开关，保留 email_template_id 模板关联"""
    result = await db.execute(text(
        "UPDATE hy_notice_action SET email_enabled = 0 "
        "WHERE action_key IN ('weather_alert','weather_alert_admin') "
        "AND email_enabled = 1"
    ))
    logger.info(
        "[迁移] 已关闭天气动作自动开启的邮件开关 %d 条（保留模板关联）",
        result.rowcount or 0,
    )
