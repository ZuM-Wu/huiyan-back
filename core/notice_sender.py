# -*- coding: utf-8 -*-
"""
通知发送器（核心系统）
统一通知发送入口：查询动作配置 -> 选择模板 -> 变量替换 ->
写入日志 -> 联动站内信 -> 异步投递插件发送任务
"""
import logging
from core.time_utils import china_now
from typing import Any, Dict, Optional

from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.notice import NoticeAction, NoticeLog, SmsTemplate, EmailTemplate
from core.inbox_trigger import inbox_trigger
from services.task.queue_worker import submit_task
from core.events import event_bus, pipeline_engine

logger = logging.getLogger(__name__)

def replace_variables(content: str, variables: Dict[str, Any]) -> str:
    """
    模板变量替换
    统一支持两种语法：短信 @var(key)、邮件 {key}
    """
    result = content or ""
    for key, value in (variables or {}).items():
        text_value = str(value) if value is not None else ""
        result = result.replace(f"@var({key})", text_value)
        result = result.replace(f"{{{key}}}", text_value)
    return result


class NoticeSender:
    """通知发送器"""

    async def send(self, action_key: str, recipient: str, channel: str,
                   variables: Optional[Dict[str, Any]] = None,
                   recipient_id: int = 0) -> Dict[str, Any]:
        """
        统一发送入口

        Args:
            action_key: 动作标识（如 user_registered）
            recipient: 接收者手机号/邮箱
            channel: 渠道 sms/email
            variables: 模板变量
            recipient_id: 接收者 ID（farmer_id/admin_id），用于站内信联动

        Returns:
            {"task_id": 异步任务标识, "log_id": 日志 ID}

        Raises:
            ValueError: 动作不存在/未启用/模板未配置等业务校验失败
        """
        variables = variables or {}
        channel = (channel or "").lower()
        if channel not in ("sms", "email"):
            raise ValueError(f"不支持的渠道：{channel}")

        async with async_session_factory() as db:
            # 1. 查询动作配置
            result = await db.execute(
                select(NoticeAction).where(NoticeAction.action_key == action_key)
            )
            action = result.scalar_one_or_none()
            if not action:
                raise ValueError(f"动作不存在：{action_key}")

            # 提前取出会话关闭后仍需使用的字段，避免 ORM 实例过期
            trigger_inbox = bool(action.trigger_inbox)
            action_name = action.action_name

            # 2. 选择模板并组装发送载荷
            if channel == "sms":
                payload, content, subject = await self._build_sms_payload(
                    db, action, recipient, variables
                )
            else:
                payload, content, subject = await self._build_email_payload(
                    db, action, recipient, variables
                )

            # 3. 发送过滤管道在写日志和入队前执行，异常或拒绝均 fail-closed。
            decision = await pipeline_engine.run("notice.filter_send", {
                "action_key": action_key, "recipient": recipient,
                "channel": channel, "content": content,
                "variables": variables, "payload": payload,
            })
            if not decision.allowed:
                raise ValueError(f"通知已被过滤: {decision.reason or '未说明原因'}")
            filtered = decision.context
            recipient = str(filtered.get("recipient", recipient))
            content = str(filtered.get("content", content))
            variables = dict(filtered.get("variables", variables))
            payload = dict(filtered.get("payload", payload))
            payload.update({
                "recipient": recipient, "content": content, "variables": variables,
            })

            # 4. 写入日志（初始 pending 状态，由 worker 回写结果）
            # 验证码入库前掩码（前 2 位 + ****）：防日志查阅者拿验证码冒用；
            # 发送流程用的 payload/content 仍为原值，不受影响
            masked_variables = dict(variables)
            masked_content = content
            code_val = str(variables.get("code") or "")
            if code_val:
                masked_code = code_val[:2] + "****"
                masked_variables["code"] = masked_code
                masked_content = content.replace(code_val, masked_code)
            log = NoticeLog(
                action_key=action_key,
                recipient=recipient,
                channel=channel,
                template_id=payload["local_template_id"],
                content=(masked_content[:100] + "...") if len(masked_content) > 100 else masked_content,
                status=0,
                recipient_id=recipient_id or 0,
                extra={
                    "pending": True,
                    "original_content": masked_content,
                    "variables": masked_variables,
                    "subject": subject,
                },
                create_time=china_now(),
            )
            db.add(log)
            await db.commit()
            await db.refresh(log)
            log_id = log.id

        # 5. 只读发送事件不具备修改或拦截能力。
        await event_bus.publish_transient("notice.sending", {
            "action_key": action_key, "recipient": recipient,
            "channel": channel, "content": content, "variables": variables,
        })

        # 6. 持久化投递发送任务，进程重启后仍可由队列重试
        task_id = await self._enqueue_notice(log_id, payload, f"通知发送: {action_name}")

        # 7. 联动站内信
        if trigger_inbox and recipient_id:
            await inbox_trigger.create(
                receiver_id=recipient_id,
                title=f"【{action_name}】通知",
                content=content,
                extra={"log_id": log_id, "action_key": action_key},
            )

        logger.info(
            f"[通知发送] 已投递: action={action_key}, channel={channel}, "
            f"recipient={recipient}, log_id={log_id}"
        )
        return {"task_id": task_id, "log_id": log_id}

    async def send_content(
        self,
        *,
        recipient: str,
        channel: str,
        content: str,
        subject: str = "",
        interface: str = "",
        sms_type: int = 0,
        template_id: str = "",
        local_template_id: int = 0,
        variables: Optional[Dict[str, Any]] = None,
        action_key: str = "system_notice",
        recipient_id: int = 0,
        description: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """投递已生成内容的短信或邮件，供业务插件复用统一通知链路。"""
        channel = (channel or "").lower()
        if channel not in ("sms", "email"):
            raise ValueError(f"不支持的渠道：{channel}")

        payload = {
            "channel": channel, "interface": interface, "sms_type": sms_type,
            "recipient": recipient, "content": content, "subject": subject,
            "template_id": template_id, "local_template_id": local_template_id,
            "variables": variables or {}, "attachments": [],
        }
        decision = await pipeline_engine.run("notice.filter_send", {
            "action_key": action_key, "recipient": recipient,
            "channel": channel, "content": content,
            "variables": variables or {}, "payload": payload,
        })
        if not decision.allowed:
            raise ValueError(f"通知已被过滤: {decision.reason or '未说明原因'}")
        filtered = decision.context
        recipient = str(filtered.get("recipient", recipient))
        content = str(filtered.get("content", content))
        variables = dict(filtered.get("variables", variables or {}))
        payload = dict(filtered.get("payload", payload))
        payload.update({"recipient": recipient, "content": content, "variables": variables})
        log_extra = {"pending": True, "original_content": content, "subject": subject}
        log_extra.update(extra or {})
        async with async_session_factory() as db:
            log = NoticeLog(
                action_key=action_key, recipient=recipient, channel=channel,
                template_id=local_template_id,
                content=(content[:100] + "...") if len(content) > 100 else content,
                status=0, recipient_id=recipient_id or 0, extra=log_extra,
                create_time=china_now(),
            )
            db.add(log)
            await db.commit()
            await db.refresh(log)

        await event_bus.publish_transient("notice.sending", {
            "action_key": action_key, "recipient": recipient,
            "channel": channel, "content": content, "variables": variables,
        })

        task_id = await self._enqueue_notice(
            log.id, payload, description or f"内容通知: {action_key}"
        )
        return {"task_id": task_id, "log_id": log.id}

    @staticmethod
    async def _enqueue_notice(log_id: int, payload: Dict[str, Any], description: str) -> str:
        """提交通知任务；入队失败时将通知日志标记为失败，避免永久 pending。"""
        try:
            task_id = await submit_task(
                "notice", {"log_id": log_id, "payload": payload}, description=description
            )
        except Exception as e:
            async with async_session_factory() as db:
                result = await db.execute(select(NoticeLog).where(NoticeLog.id == log_id))
                log = result.scalar_one_or_none()
                if log:
                    log.status = 0
                    log.error_msg = f"任务投递失败：{e}"
                    log.extra = {**(log.extra or {}), "pending": False}
                    log.send_time = china_now()
                    await db.commit()
            logger.error("[通知发送] 任务投递失败: log_id=%s, error=%s", log_id, e)
            raise RuntimeError(f"通知任务投递失败：{e}") from e
        return f"notice-{task_id}"

    @staticmethod
    async def _build_sms_payload(db, action: NoticeAction, recipient: str,
                                 variables: Dict[str, Any]):
        """组装短信发送载荷（优先国内配置，未启用时回落国际配置）"""
        if action.sms_enabled:
            interface, template_id = action.sms_interface, action.sms_template_id
        elif action.sms_global_enabled:
            interface, template_id = action.sms_global_interface, action.sms_global_template_id
        else:
            raise ValueError("该动作未启用短信")

        # 当 interface 为空时，自动查找第一个已启用的 SMS 插件作为回落
        if not interface:
            from core.db.plugin import PluginModel
            fallback = (await db.execute(
                select(PluginModel).where(
                    PluginModel.module == "sms",
                    PluginModel.status == 1,
                )
            )).scalars().first()
            if fallback:
                interface = fallback.name
                logger.info("[通知发送] 动作 %s 未配置接口，自动回落到: %s",
                            action.action_key, interface)
            else:
                raise ValueError("未配置 SMS 接口且无已启用的短信插件")

        result = await db.execute(select(SmsTemplate).where(SmsTemplate.id == template_id))
        template = result.scalar_one_or_none()
        if not template:
            raise ValueError("短信模板未配置")

        # 变量替换后拼接签名
        content = replace_variables(template.content, variables)
        if template.signature and not content.startswith(template.signature):
            content = f"{template.signature}{content}"

        payload = {
            "channel": "sms",
            "interface": interface,
            "sms_type": template.type or 0,
            "recipient": recipient,
            "content": content,
            "template_id": template.template_id or "",
            "local_template_id": template.id,
            "variables": variables,
        }
        return payload, content, ""

    @staticmethod
    async def _build_email_payload(db, action: NoticeAction, recipient: str,
                                   variables: Dict[str, Any]):
        """
        组装邮件发送载荷
        邮件模板不绑定插件：interface 仅作为动作级发送接口偏好，
        为空时由 Worker 自动路由到第一个已启用的邮件插件
        """
        if not action.email_enabled:
            raise ValueError("该动作未启用邮件")

        result = await db.execute(
            select(EmailTemplate).where(EmailTemplate.id == action.email_template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            raise ValueError("邮件模板未配置")

        content = replace_variables(template.content, variables)
        subject = replace_variables(template.subject, variables)

        payload = {
            "channel": "email",
            "interface": action.email_interface or "",
            "recipient": recipient,
            "content": content,
            "subject": subject,
            "attachments": [],
            "local_template_id": template.id,
            "variables": variables,
        }
        return payload, content, subject


# 全局单例
notice_sender = NoticeSender()
