# -*- coding: utf-8 -*-
"""
通知编排服务（核心系统）

封装"一对多/单接收者降级"群发场景，由核心钩子监听者（core/notice_hooks.py）
与气象预警入库逻辑（core/weather_service._store_alerts）调用。

职责:
- 查动作配置（NoticeAction）判断短信/邮件启用状态
- 查农户/管理员取手机/邮箱
- 按渠道优先级发送：有手机且动作启用短信→短信；有邮箱且动作启用邮件→邮件；
  无可用渠道→降级站内信（保证至少一条消息送达）
- 单接收者异常隔离，失败记 warning 不阻断主流程

依赖说明：
- 通知发送/站内信：notice_sender.send + inbox_trigger.create（公共接入点 1.10/1.11）
- 接收者查询：直接 SELECT core.db.notice/farmer/admin/production_area 获取手机/邮箱/绑定关系，
  属于核心编排层职责，非外部插件可用的公开门面
- 禁止直连发送插件或直接 INSERT 业务表
"""
import logging
from typing import Dict, Any

from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.notice import NoticeAction
from core.db.farmer import Farmer
from core.db.admin import Admin
from core.db.production_area import AreaFarmer
from core.notice_sender import notice_sender
from core.inbox_trigger import inbox_trigger

logger = logging.getLogger(__name__)


class NoticeDispatcher:
    """通知编排服务（全局单例 notice_dispatcher）"""

    async def notify_single_farmer(
        self, action_key: str, farmer_id: int, variables: Dict[str, Any],
        inbox_title: str, inbox_content: str,
    ) -> Dict[str, Any]:
        """单农户通知（cert_approved/cert_rejected/batch_status_change/area_farmer_bindied 场景）

        查农户手机/邮箱与动作渠道配置，按优先级发送；
        无可用渠道时降级发站内信保证送达。
        """
        async with async_session_factory() as db:
            action = (await db.execute(
                select(NoticeAction).where(NoticeAction.action_key == action_key)
            )).scalar_one_or_none()
            if not action:
                logger.warning(f"[通知编排] 动作不存在: {action_key}，跳过")
                return {"sent": False, "reason": "action_not_found"}

            farmer = (await db.execute(
                select(Farmer).where(Farmer.id == farmer_id, Farmer.status == 1)
            )).scalar_one_or_none()
            if not farmer:
                logger.warning(f"[通知编排] 农户不存在或已禁用: {farmer_id}，跳过")
                return {"sent": False, "reason": "farmer_not_found"}

            # 提前取出会话关闭后仍需使用的字段，避免 ORM 实例过期
            sms_enabled = bool(action.sms_enabled)
            email_enabled = bool(action.email_enabled)
            phone = farmer.phone or ""
            email = farmer.email or ""
            nickname = farmer.nickname or farmer.username

        return await self._send_to_farmer(
            action_key=action_key, farmer_id=farmer_id, phone=phone, email=email,
            nickname=nickname, sms_enabled=sms_enabled, email_enabled=email_enabled,
            variables=variables, inbox_title=inbox_title, inbox_content=inbox_content,
        )

    async def broadcast_farmers_by_area(
        self, action_key: str, area_id: int, variables: Dict[str, Any],
        inbox_title: str, inbox_content: str,
    ) -> Dict[str, Any]:
        """按产区绑定农户群发（weather_alert/plot_created 场景）

        查产区绑定农户（AreaFarmer join Farmer, status=1），逐农户走单农户发送逻辑。
        """
        async with async_session_factory() as db:
            action = (await db.execute(
                select(NoticeAction).where(NoticeAction.action_key == action_key)
            )).scalar_one_or_none()
            if not action:
                logger.warning(f"[通知编排] 动作不存在: {action_key}，跳过群发")
                return {"total": 0, "sent_sms": 0, "sent_email": 0, "inbox_only": 0, "failed": 0}

            sms_enabled = bool(action.sms_enabled)
            email_enabled = bool(action.email_enabled)

            # 查产区绑定农户（只取启用状态农户，子查询关联表）
            rows = (await db.execute(
                select(Farmer.id, Farmer.phone, Farmer.email, Farmer.nickname, Farmer.username)
                .where(
                    Farmer.id.in_(
                        select(AreaFarmer.farmer_id).where(AreaFarmer.area_id == area_id)
                    ),
                    Farmer.status == 1,
                )
            )).all()

        stats = {"total": len(rows), "sent_sms": 0, "sent_email": 0, "inbox_only": 0, "failed": 0}
        for fid, phone, email, nickname, username in rows:
            result = await self._send_to_farmer(
                action_key=action_key, farmer_id=fid, phone=phone or "", email=email or "",
                nickname=nickname or username, sms_enabled=sms_enabled,
                email_enabled=email_enabled, variables=variables,
                inbox_title=inbox_title, inbox_content=inbox_content,
            )
            if result.get("sent_sms"):
                stats["sent_sms"] += 1
            elif result.get("sent_email"):
                stats["sent_email"] += 1
            elif result.get("inbox_only"):
                stats["inbox_only"] += 1
            else:
                stats["failed"] += 1
        logger.info(f"[通知编排] 产区 {area_id} 群发完成: {stats}")
        return stats

    async def broadcast_admins(
        self, action_key: str, variables: Dict[str, Any],
        inbox_title: str, inbox_content: str,
    ) -> Dict[str, Any]:
        """群发所有启用管理员（user_registered/weather_alert_admin 场景）

        查 Admin(status=1)，逐管理员发邮件 + 站内信（receiver_type=admin）。
        管理员通知走邮件为主渠道，无邮箱或邮件未启用时降级站内信。
        """
        async with async_session_factory() as db:
            action = (await db.execute(
                select(NoticeAction).where(NoticeAction.action_key == action_key)
            )).scalar_one_or_none()
            if not action:
                logger.warning(f"[通知编排] 动作不存在: {action_key}，跳过管理员群发")
                return {"total": 0, "sent_email": 0, "inbox_only": 0, "failed": 0}

            email_enabled = bool(action.email_enabled)

            admins = (await db.execute(
                select(Admin.id, Admin.email, Admin.nickname).where(Admin.status == 1)
            )).all()

        stats = {"total": len(admins), "sent_email": 0, "inbox_only": 0, "failed": 0}
        for aid, email, nickname in admins:
            try:
                sent = False
                # 管理员优先邮件（管理端通知主渠道）
                if email and email_enabled:
                    try:
                        await notice_sender.send(
                            action_key, email, "email", variables, aid)
                        sent = True
                        stats["sent_email"] += 1
                    except Exception as e:
                        logger.warning(f"[通知编排] 管理员 {aid} 邮件发送失败: {e}")
                # 无邮件或邮件未启用/失败 → 降级站内信保证送达
                if not sent:
                    await inbox_trigger.create(
                        aid, inbox_title, inbox_content, "admin",
                        extra={"action_key": action_key})
                    stats["inbox_only"] += 1
            except Exception as e:
                logger.warning(f"[通知编排] 管理员 {aid} 通知失败: {e}")
                stats["failed"] += 1
        logger.info(f"[通知编排] 管理员群发完成: {stats}")
        return stats

    async def broadcast_all_farmers(
        self, action_key: str, variables: Dict[str, Any],
        inbox_title: str, inbox_content: str,
    ) -> Dict[str, Any]:
        """群发所有启用农户（system_maintenance 场景）"""
        async with async_session_factory() as db:
            action = (await db.execute(
                select(NoticeAction).where(NoticeAction.action_key == action_key)
            )).scalar_one_or_none()
            if not action:
                logger.warning(f"[通知编排] 动作不存在: {action_key}，跳过全农户群发")
                return {"total": 0, "sent_sms": 0, "sent_email": 0, "inbox_only": 0, "failed": 0}

            sms_enabled = bool(action.sms_enabled)
            email_enabled = bool(action.email_enabled)

            farmers = (await db.execute(
                select(Farmer.id, Farmer.phone, Farmer.email, Farmer.nickname, Farmer.username)
                .where(Farmer.status == 1)
            )).all()

        stats = {"total": len(farmers), "sent_sms": 0, "sent_email": 0, "inbox_only": 0, "failed": 0}
        for fid, phone, email, nickname, username in farmers:
            result = await self._send_to_farmer(
                action_key=action_key, farmer_id=fid, phone=phone or "", email=email or "",
                nickname=nickname or username, sms_enabled=sms_enabled,
                email_enabled=email_enabled, variables=variables,
                inbox_title=inbox_title, inbox_content=inbox_content,
            )
            if result.get("sent_sms"):
                stats["sent_sms"] += 1
            elif result.get("sent_email"):
                stats["sent_email"] += 1
            elif result.get("inbox_only"):
                stats["inbox_only"] += 1
            else:
                stats["failed"] += 1
        logger.info(f"[通知编排] 全农户群发完成: {stats}")
        return stats

    async def _send_to_farmer(
        self, *, action_key: str, farmer_id: int, phone: str, email: str,
        nickname: str, sms_enabled: bool, email_enabled: bool,
        variables: Dict[str, Any], inbox_title: str, inbox_content: str,
    ) -> Dict[str, Any]:
        """单农户发送内核（渠道优先级：短信 > 邮件 > 站内信降级）

        notice_sender.send 内部已按 trigger_inbox 联动站内信，故发短信/邮件后
        不再调 inbox_trigger；仅当无可用渠道时单独发站内信保证送达。
        """
        try:
            # 渠道 1：短信（有手机且动作启用短信）
            if phone and sms_enabled:
                try:
                    await notice_sender.send(
                        action_key, phone, "sms", variables, farmer_id)
                    return {"sent_sms": True}
                except Exception as e:
                    logger.warning(f"[通知编排] 农户 {farmer_id} 短信发送失败，尝试邮件: {e}")
            # 渠道 2：邮件（有邮箱且动作启用邮件）
            if email and email_enabled:
                try:
                    await notice_sender.send(
                        action_key, email, "email", variables, farmer_id)
                    return {"sent_email": True}
                except Exception as e:
                    logger.warning(f"[通知编排] 农户 {farmer_id} 邮件发送失败，降级站内信: {e}")
            # 渠道 3：降级站内信（无可用渠道或发送失败，保证至少一条消息送达）
            await inbox_trigger.create(
                farmer_id, inbox_title, inbox_content, "farmer",
                extra={"action_key": action_key})
            return {"inbox_only": True}
        except Exception as e:
            logger.warning(f"[通知编排] 农户 {farmer_id} 通知彻底失败: {e}")
            return {"failed": True}


# 全局单例
notice_dispatcher = NoticeDispatcher()
