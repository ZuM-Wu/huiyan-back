# -*- coding: utf-8 -*-
"""企业微信管理员通知服务。"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select

from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from services.task.queue_worker import submit_task

from plugins.addon.wecom_webhook.action_catalog import (
    ACTION_CATALOG,
    available_variables,
    build_card_fields,
    sanitize_variables,
)
from plugins.addon.wecom_webhook.models import WecomWebhookAction, WecomWebhookLog
from plugins.addon.wecom_webhook.schemas import SendMessageRequest
from plugins.addon.wecom_webhook.services import wecom_client

logger = logging.getLogger(__name__)
PLUGIN_NAME = "wecom_webhook"


class WecomNoticeService:
    """管理插件配置、管理员动作、持久化投递与发送日志。"""

    def __init__(self):
        self._config_manager = ConfigManager()

    async def get_config(self) -> Dict[str, Any]:
        """读取插件全局配置。"""
        keys = {"webhook_url": "", "enabled": "0", "timeout_seconds": "10", "retry_times": "3"}
        async with async_session_factory() as db:
            for key, default in tuple(keys.items()):
                value = await self._config_manager.get(f"{PLUGIN_NAME}.{key}", db)
                keys[key] = default if value in (None, "") else value
        return {
            "webhook_url": str(keys["webhook_url"]),
            "enabled": self._to_int(keys["enabled"], 0),
            "timeout_seconds": self._to_int(keys["timeout_seconds"], 10),
            "retry_times": self._to_int(keys["retry_times"], 3),
        }

    async def update_config(self, data: Dict[str, Any]) -> None:
        """更新插件全局配置。"""
        descriptions = {
            "webhook_url": "企业微信Webhook地址",
            "enabled": "是否全局启用",
            "timeout_seconds": "请求超时秒数",
            "retry_times": "任务失败重试次数",
        }
        async with async_session_factory() as db:
            for key, description in descriptions.items():
                await self._config_manager.set(
                    f"{PLUGIN_NAME}.{key}", str(data[key]), db, description=description,
                )
            await db.commit()

    async def list_actions(self) -> Dict[str, Any]:
        """仅读取插件自有管理员动作。"""
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(WecomWebhookAction).order_by(WecomWebhookAction.id)
            )).scalars().all()
        configs = {row.action_key: row for row in rows}
        items = []
        for action_key in ACTION_CATALOG:
            row = configs.get(action_key)
            if row is None:
                continue
            definition = ACTION_CATALOG[action_key]
            items.append({
                "action_key": row.action_key,
                "action_name": definition["name"],
                "action_type": definition["type"],
                "event_name": definition["event_name"],
                "event_title": definition["event_title"],
                "event_version": definition["event_version"],
                "export_fields": definition["export_fields"],
                "enabled": row.enabled or 0,
                "webhook_url": row.webhook_url or "",
                "uses_global_webhook": not bool(row.webhook_url),
                "available_variables": available_variables(action_key),
            })
        return {
            "list": items,
            "categories": sorted({item["action_type"] for item in items}),
            "total": len(items),
        }

    async def upsert_action(self, action_key: str, data: Dict[str, Any]) -> bool:
        """更新已登记的企业微信管理员动作。"""
        if action_key not in ACTION_CATALOG:
            return False
        async with async_session_factory() as db:
            row = (await db.execute(
                select(WecomWebhookAction).where(
                    WecomWebhookAction.action_key == action_key,
                )
            )).scalar_one_or_none()
            if row is None:
                return False
            row.enabled = int(data.get("enabled") or 0)
            row.webhook_url = str(data.get("webhook_url") or "")
            row.update_time = datetime.now()
            await db.commit()
        return True

    async def enqueue_action(
        self, action_key: str, variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """将单次管理员事件投递到企业微信持久化队列。"""
        if action_key not in ACTION_CATALOG:
            return {"status": "skipped", "msg": f"动作 {action_key} 未登记"}
        resolved = await self._resolve_action(action_key)
        if not resolved or not resolved["enabled"]:
            return {"status": "skipped", "msg": f"动作 {action_key} 未启用企业微信通知"}
        config = await self.get_config()
        if not config["enabled"]:
            return {"status": "skipped", "msg": "企业微信通知全局未启用"}
        if not resolved["webhook_url"] and not config["webhook_url"]:
            return {"status": "skipped", "msg": "Webhook 地址未配置"}
        task_id = await submit_task(
            "wecom_notice",
            {"action_key": action_key, "variables": sanitize_variables(action_key, variables)},
            description=f"企业微信管理员通知: {resolved['action_name']}",
        )
        return {"status": "queued", "msg": "已加入发送队列", "task_id": task_id}

    async def send_queued_notice(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """队列处理器入口，按当前动作数据生成模板卡片。"""
        action_key = str(task_data.get("action_key") or "")
        variables = task_data.get("variables")
        if not isinstance(variables, dict) or action_key not in ACTION_CATALOG:
            return {"status": "error", "msg": "管理员通知任务数据无效"}
        resolved = await self._resolve_action(action_key)
        config = await self.get_config()
        if not resolved or not resolved["enabled"] or not config["enabled"]:
            return {"status": "skipped", "msg": "动作或插件已禁用"}
        webhook_url = resolved["webhook_url"] or config["webhook_url"]
        if not webhook_url:
            return {"status": "error", "msg": "Webhook 地址未配置"}

        site_name, site_domain = await self._get_site_info()
        card_url = f"{site_domain.rstrip('/')}/admin/notice-log" if site_domain else ""
        card = wecom_client.build_notice_card(
            resolved["action_name"], site_name,
            build_card_fields(action_key, variables), card_url,
        )
        body = wecom_client.build_template_card_body(card)
        content = json.dumps(body["template_card"], ensure_ascii=False)
        result = await wecom_client.send_webhook(
            webhook_url, body, timeout=config["timeout_seconds"],
        )
        await self._write_log(
            action_key, "template_card", content,
            result.get("status") == "success",
            "" if result.get("status") == "success" else result.get("msg", ""),
            result.get("msg_id", ""),
        )
        return result

    async def send_message(
        self,
        request: SendMessageRequest,
        action_key: str = "",
        webhook_url: str = "",
    ) -> Dict[str, Any]:
        """手动或测试发送企业微信模板卡片。"""
        config = await self.get_config()
        target = webhook_url or config["webhook_url"]
        body = self._build_body(request)
        result = await wecom_client.send_webhook(target, body, timeout=config["timeout_seconds"])
        log_id = await self._write_log(
            action_key, "template_card", self._extract_content_preview(request),
            result.get("status") == "success",
            "" if result.get("status") == "success" else result.get("msg", ""),
            result.get("msg_id", ""),
        )
        return {**result, "log_id": log_id}

    async def _resolve_action(self, action_key: str) -> Optional[Dict[str, Any]]:
        """读取单个插件管理员动作，不访问核心通知表。"""
        async with async_session_factory() as db:
            row = (await db.execute(
                select(WecomWebhookAction).where(
                    WecomWebhookAction.action_key == action_key,
                )
            )).scalar_one_or_none()
        if row is None:
            return None
        return {
            "action_name": row.action_name or action_key,
            "enabled": row.enabled or 0,
            "webhook_url": row.webhook_url or "",
        }

    @staticmethod
    def _build_body(request: SendMessageRequest) -> Dict[str, Any]:
        """根据已验证的卡片模型构造企业微信请求体。"""
        card = request.template_card
        fields = [
            {"keyname": str(item.get("keyname") or "")[:5], "value": str(item.get("value") or "")[:30]}
            for item in card.horizontal_content
            if isinstance(item, dict) and str(item.get("keyname") or "").strip()
        ][:6]
        data = wecom_client.build_notice_card(
            card.main_title, card.main_desc, fields, card.card_url,
            card.source_desc or "慧眼护农系统通知",
        )
        data["card_type"] = card.card_type
        return wecom_client.build_template_card_body(data)

    @staticmethod
    def _extract_content_preview(request: SendMessageRequest) -> str:
        """提取卡片日志预览。"""
        return json.dumps(request.template_card.model_dump(), ensure_ascii=False)[:500]

    async def _get_site_info(self) -> tuple[str, str]:
        """读取卡片标题和跳转链接所需的网站配置。"""
        async with async_session_factory() as db:
            name = await self._config_manager.get("site_name", db)
            domain = await self._config_manager.get("site_domain", db)
        site_name = str(name or "慧眼护农").strip()
        site_domain = str(domain or "").strip()
        if not site_domain.startswith(("http://", "https://")):
            site_domain = ""
        return site_name, site_domain

    @staticmethod
    async def _write_log(
        action_key: str, msgtype: str, content: str, success: bool,
        error_msg: str, msg_id: str,
    ) -> int:
        """写入企业微信发送日志。"""
        try:
            async with async_session_factory() as db:
                log = WecomWebhookLog(
                    action_key=action_key, msgtype=msgtype,
                    content=(content or "")[:500], status=1 if success else 0,
                    error_msg=(error_msg or "")[:512], msg_id=msg_id or "",
                    create_time=datetime.now(),
                )
                db.add(log)
                await db.commit()
                await db.refresh(log)
                return log.id
        except Exception as exc:
            logger.error("[wecom_webhook] 日志写入失败: %s", exc)
            return 0

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


wecom_notice_service = WecomNoticeService()
