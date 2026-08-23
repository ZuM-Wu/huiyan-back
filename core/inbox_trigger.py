# -*- coding: utf-8 -*-
"""
站内信触发器
通知发送时联动生成站内信记录（写入 hy_inbox_message 表）
"""
import logging
from typing import Dict, Any, Optional

from core.db.base import async_session_factory
from core.db.notice import InboxMessage

logger = logging.getLogger(__name__)


class InboxTrigger:
    """通知联动站内信触发器"""

    @staticmethod
    async def create(receiver_id: int, title: str, content: str,
                     receiver_type: str = "farmer",
                     extra: Optional[Dict[str, Any]] = None,
                     sender_id: int = 0) -> int:
        """
        创建站内信消息

        Args:
            receiver_id: 接收者 ID（farmer_id/admin_id）
            title: 消息标题（超长自动截断至 256 字符）
            content: 消息内容（超长自动截断至 10000 字符）
            receiver_type: 接收者类型 farmer/admin
            extra: 扩展字段（如关联的通知日志 log_id）
            sender_id: 发件人 ID，系统消息为 0

        Returns:
            新建消息 ID，失败返回 0（联动失败不阻断主流程）
        """
        try:
            async with async_session_factory() as db:
                msg = InboxMessage(
                    sender_id=sender_id,
                    receiver_type=receiver_type,
                    receiver_id=receiver_id,
                    title=title[:256],
                    content=(content or "")[:10000],
                    extra=extra or {},
                    # 标题含"紧急"关键字时自动提升为重要优先级
                    priority=1 if "紧急" in title else 0,
                )
                db.add(msg)
                await db.commit()
                await db.refresh(msg)
                logger.info(f"[站内信] 已创建消息 id={msg.id}, receiver={receiver_type}:{receiver_id}")
                return msg.id
        except Exception as e:
            # 站内信联动失败仅记录日志，不影响短信/邮件主发送流程
            logger.error(f"[站内信] 创建消息失败: {e}")
            return 0


# 全局单例
inbox_trigger = InboxTrigger()
