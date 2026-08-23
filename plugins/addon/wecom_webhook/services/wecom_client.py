"""企业微信群机器人卡片发送客户端。"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)
VALID_MSGTYPES = {"template_card"}


def build_template_card_body(card_data: Dict[str, Any]) -> Dict[str, Any]:
    """构造企业微信模板卡片请求体。"""
    return {"msgtype": "template_card", "template_card": card_data}


def build_notice_card(
    title: str,
    site_name: str,
    fields: Optional[List[Dict[str, str]]] = None,
    card_url: str = "",
    source_desc: str = "慧眼护农系统通知",
) -> Dict[str, Any]:
    """按 ZJMF 风格构造文本通知卡片。"""
    card: Dict[str, Any] = {
        "card_type": "text_notice",
        "source": {"desc": source_desc[:20], "desc_color": 0},
        "main_title": {"title": title[:20], "desc": (site_name or "慧眼护农")[:30]},
        "sub_title_text": title[:30],
        "horizontal_content_list": list(fields or [])[:6],
    }
    if card_url:
        card["jump_list"] = [{"type": 1, "url": card_url, "title": "查看通知日志"}]
        card["card_action"] = {"type": 1, "url": card_url}
    return build_template_card_body(card)["template_card"]


async def send_webhook(
    webhook_url: str, body: Dict[str, Any], timeout: float = 10,
) -> Dict[str, Any]:
    """发送模板卡片并转换企业微信响应为统一结果。"""
    if not webhook_url:
        return {"status": "error", "msg": "Webhook 地址未配置"}
    parsed = urlparse(webhook_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return {"status": "error", "msg": "Webhook 地址必须是 HTTPS URL"}
    if body.get("msgtype") not in VALID_MSGTYPES:
        return {"status": "error", "msg": "仅支持模板卡片消息"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(webhook_url, json=body)
            response.raise_for_status()
        result = response.json()
    except httpx.HTTPError as exc:
        logger.error("[wecom_webhook] HTTP 请求失败: %s", exc)
        return {"status": "error", "msg": f"网络请求失败: {exc}"}
    except ValueError as exc:
        logger.error("[wecom_webhook] 响应解析失败: %s", exc)
        return {"status": "error", "msg": f"响应解析失败: {exc}"}

    if not isinstance(result, dict):
        return {"status": "error", "msg": "企业微信响应格式错误"}
    if result.get("errcode") == 0:
        return {"status": "success", "msg": "发送成功", "msg_id": str(result.get("msgid", ""))}
    return {"status": "error", "msg": f"企业微信返回错误: [{result.get('errcode')}] {result.get('errmsg', '')}"}
