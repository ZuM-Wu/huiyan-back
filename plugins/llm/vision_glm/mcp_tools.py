"""智谱 GLM 视觉 MCP 工具实现。"""
import base64
import binascii
import logging
import re
from typing import Any, Dict, List

import httpx

from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from plugins.llm.vision_glm.model_catalog import (
    DEFAULT_VISION_MODEL,
    get_model_capabilities,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_IMAGE_MB = 8
MAX_TOTAL_IMAGE_MB = 16
# 保留旧常量名，避免插件内部与既有测试的导入契约失效。
MODEL_NAME = DEFAULT_VISION_MODEL
MAX_IMAGES = 4
_DATA_URL_RE = re.compile(r"^data:(image/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/=]+)$")


class VisionInputError(ValueError):
    """图片输入不符合视觉接口约束。"""


def _parse_timeout(value: Any) -> float:
    """解析请求超时，异常配置回落默认值并限制范围。"""
    try:
        return min(max(float(value), 5.0), 180.0)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def _parse_max_bytes(value: Any) -> int:
    """解析单张图片大小上限，单位为 MB。"""
    try:
        mb = min(max(float(value), 1.0), 16.0)
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_IMAGE_MB
    return int(mb * 1024 * 1024)


def validate_image_data(images: List[str], max_bytes: int) -> List[str]:
    """校验并规范化图片 Data URL，不返回解码后的原始图片内容。"""
    if not isinstance(images, list) or not images:
        raise VisionInputError("至少需要提供一张图片")
    if len(images) > MAX_IMAGES:
        raise VisionInputError(f"单次最多分析 {MAX_IMAGES} 张图片")

    normalized: List[str] = []
    total_bytes = 0
    for index, value in enumerate(images, start=1):
        if not isinstance(value, str) or not value:
            raise VisionInputError(f"第 {index} 张图片格式无效")
        match = _DATA_URL_RE.fullmatch(value)
        if not match:
            raise VisionInputError(
                f"第 {index} 张图片必须是 JPEG、PNG、GIF 或 WebP 的 Data URL"
            )
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise VisionInputError(f"第 {index} 张图片编码无效") from exc
        if not raw:
            raise VisionInputError(f"第 {index} 张图片为空")
        if len(raw) > max_bytes:
            raise VisionInputError(
                f"第 {index} 张图片超过大小限制（最大 {max_bytes // 1024 // 1024}MB）"
            )
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_IMAGE_MB * 1024 * 1024:
            raise VisionInputError(f"图片总大小超过限制（最大 {MAX_TOTAL_IMAGE_MB}MB）")
        normalized.append(value)
    return normalized


def build_payload(
    images: List[str], prompt: str = "", model_name: str = DEFAULT_VISION_MODEL,
) -> Dict[str, Any]:
    """构造智谱 OpenAI 兼容多模态请求体。"""
    content = [{"type": "image_url", "image_url": {"url": image}} for image in images]
    content.append({
        "type": "text",
        "text": prompt.strip() or "请识别图片内容，并结合农业场景给出客观、可核验的描述。",
    })
    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }
    if get_model_capabilities(model_name).get("support_reasoning"):
        payload["thinking"] = {"type": "disabled"}
    return payload


def _error_message(status_code: int) -> str:
    """将平台错误状态转换为用户可理解的中文错误，不透传平台原文。"""
    mapping = {
        401: "智谱视觉 API Key 无效或已过期",
        402: "智谱视觉服务额度不足",
        429: "智谱视觉请求过于频繁，请稍后重试",
    }
    return mapping.get(status_code, f"智谱视觉接口返回错误（HTTP {status_code}）")


async def vision_glm_analyze(images: List[str], prompt: str = "") -> str:
    """调用管理员指定的智谱视觉模型分析图片并返回文本结果。"""
    async with async_session_factory() as db:
        config_manager = ConfigManager()
        config = await config_manager.get_plugin_config("vision_glm", db)
        model_name = (
            await config_manager.get("ai.vision_model", db) or DEFAULT_VISION_MODEL
        )

    api_key = str(config.get("api_key") or "").strip()
    if not api_key:
        return "视觉分析暂不可用：尚未配置智谱 GLM API Key。"
    if not get_model_capabilities(model_name).get("support_vision"):
        return "视觉分析暂不可用：配置的识图模型不支持视觉理解。"

    try:
        images = validate_image_data(images, _parse_max_bytes(config.get("max_image_mb")))
    except VisionInputError as exc:
        return f"视觉分析输入无效：{exc}"

    base_url = str(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_parse_timeout(config.get("timeout"))) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=build_payload(images, prompt, model_name),
            )
    except httpx.TimeoutException:
        return "视觉分析请求超时，请稍后重试。"
    except httpx.HTTPError:
        logger.warning("[vision_glm] 请求智谱视觉接口失败")
        return "视觉分析网络请求失败，请检查服务端网络。"

    if response.status_code != 200:
        # 平台错误原文可能回显请求参数或鉴权信息，统一只返回本地映射文案。
        return _error_message(response.status_code)

    try:
        body = response.json()
        content = body["choices"][0]["message"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return "视觉分析接口返回格式异常，请稍后重试。"
    return str(content).strip() or "视觉模型未返回有效分析结果。"
