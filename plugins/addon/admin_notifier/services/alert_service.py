# -*- coding: utf-8 -*-
"""
邮件通知管理员插件 — 告警服务

核心职责（插件层面，仅做邮件通知）:
1. 任务失败时查询告警配置
2. 首次出现的任务自动创建默认配置（notify_enabled=0）
3. 如果 notify_enabled=1：解析接收管理员、加载邮件插件、发送通知

日志记录、系统内告警、手动重试、标记处理等核心功能
已移至 core/task/task_monitor.py，不在本插件职责范围内。
"""
import logging
from core.time_utils import china_now
from typing import List

from sqlalchemy import select

from core.db.base import async_session_factory

from plugins.addon.admin_notifier.models import TaskAlertConfig

logger = logging.getLogger(__name__)


class AlertService:
    """邮件通知服务单例"""

    async def handle_failed_task(self, task_name: str, task_desc: str,
                                  error_msg: str, task_type: str = "system"):
        """
        任务失败时的处理入口（仅邮件通知）

        1. 查询或自动创建告警配置
        2. 如果 notify_enabled=1，发送邮件通知
        """
        async with async_session_factory() as db:
            config = await self._get_or_create_config(
                db, task_name, task_desc, task_type
            )
            await db.commit()

            notify_enabled = config.notify_enabled
            notify_interface = config.notify_interface
            admin_ids = config.admin_ids

        if not notify_enabled:
            return

        try:
            await self._send_notify(
                task_name, task_desc, error_msg,
                notify_interface, admin_ids,
            )
        except Exception as e:
            logger.error(f"[admin_notifier] 通知发送失败: {e}")

    async def _get_or_create_config(self, db, task_name: str,
                                    task_desc: str,
                                    task_type: str) -> TaskAlertConfig:
        """查询或自动创建告警配置（首次出现的任务自动建配置）"""
        result = await db.execute(
            select(TaskAlertConfig).where(TaskAlertConfig.task_name == task_name)
        )
        config = result.scalar_one_or_none()
        if config:
            return config

        config = TaskAlertConfig(
            task_name=task_name,
            task_title=task_desc or task_name,
            task_type=task_type,
            notify_enabled=0,
            notify_channel="email",
            notify_interface="",
            admin_ids="",
        )
        db.add(config)
        await db.flush()
        logger.info(f"[admin_notifier] 自动创建告警配置: {task_name}")
        return config

    async def _send_notify(self, task_name: str, task_desc: str,
                           error_msg: str,
                           interface: str, admin_ids: str):
        """发送邮件通知给管理员"""
        if not admin_ids:
            logger.warning(f"[admin_notifier] 任务 {task_name} 未配置接收管理员，跳过通知")
            return

        admins = await self.get_notify_admins(admin_ids)
        if not admins:
            logger.warning(f"[admin_notifier] 任务 {task_name} 的管理员ID无有效邮箱")
            return

        from core.notice_sender import notice_sender

        # 尝试从数据库加载邮件模板，找不到则回退到内联构造
        now_str = china_now().strftime("%Y-%m-%d %H:%M:%S")
        subject, content = await self._load_email_template(
            task_name, task_desc, error_msg, now_str,
        )

        # 逐个持久化投递邮件，最终第三方发送结果由通知日志异步回写
        queued_count = 0
        for admin in admins:
            try:
                await notice_sender.send_content(
                    recipient=admin["email"], channel="email", content=content,
                    subject=subject, interface=interface, action_key="task_alert",
                    recipient_id=admin["id"], description=f"任务失败告警: {task_desc}",
                    extra={"task_name": task_name, "task_type": "task_alert"},
                )
                queued_count += 1
            except Exception as e:
                logger.error(f"[admin_notifier] 投递给 {admin.get('email')} 异常: {e}")

        from core.log.active_log import active_log
        await active_log(
            f"任务失败通知已投递: {task_desc} → {queued_count}/{len(admins)} 人",
            log_type="task_alert",
        )

    async def _load_email_template(self, task_name: str, task_desc: str,
                                    error_msg: str, now_str: str):
        """从数据库加载邮件模板，找不到则回退到内联构造"""
        from core.notice_template_service import get_email_template_by_action
        tpl = await get_email_template_by_action(task_name)

        if tpl:
            subject = tpl["subject"]
            content = tpl["content"]
            # 替换模板变量
            content = content.replace("{task_name}", task_name)
            content = content.replace("{task_desc}", task_desc)
            content = content.replace("{error_msg}", error_msg)
            content = content.replace("{fail_time}", now_str)
            content = content.replace("{send_time}", now_str)
            return subject, content

        # 回退到内联构造（容错）
        subject = f"[慧眼护农] 任务执行失败告警: {task_desc}"
        content = (
            f"<h3>任务执行失败告警</h3>"
            f"<p><b>任务名称:</b> {task_desc}</p>"
            f"<p><b>任务标识:</b> {task_name}</p>"
            f"<p><b>失败时间:</b> {now_str}</p>"
            f"<p><b>错误信息:</b> {error_msg}</p>"
            f"<p>请及时登录管理后台查看详情并处理。</p>"
        )
        return subject, content

    async def get_notify_admins(self, admin_ids: str) -> List[dict]:
        """解析管理员ID列表并查询邮箱（委托 admin_service 公开门面）"""
        if not admin_ids:
            return []
        ids = []
        for s in admin_ids.split(","):
            s = s.strip()
            if s.isdigit():
                ids.append(int(s))
        if not ids:
            return []

        from core.admin_service import get_admin_contacts
        return await get_admin_contacts(ids)


# 全局单例
alert_service = AlertService()
