# -*- coding: utf-8 -*-
"""AgentScope 聊天图片内部引用与临时模型输入转换。"""
from __future__ import annotations

import asyncio
import base64
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from agentscope.message import Base64Source, DataBlock, HintBlock, Msg

from core.config import BASE_DIR
from services.image_upload import get_image_upload_config


MAX_IMAGES_PER_TURN = 4
_INTERNAL_SCHEME = "huiyan-upload"
_LOCAL_DIRECTORY = "common"
_UPLOAD_ROOT = (Path(BASE_DIR) / "upload" / _LOCAL_DIRECTORY).resolve()
_FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.(?:jpg|jpeg|png|gif|webp)$", re.IGNORECASE)
_MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ImageInputError(ValueError):
    """图片引用不可信、不可读取或与模型能力不匹配。"""


def build_model_image_url(public_url: str) -> str:
    """把上传结果转换为模型可持久化引用，公网 HTTPS 地址保持不变。"""
    raw = str(public_url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme == "https" and parsed.hostname not in _LOOPBACK_HOSTS:
        return raw
    filename = Path(parsed.path).name
    _validate_filename(filename)
    return f"{_INTERNAL_SCHEME}://{_LOCAL_DIRECTORY}/{filename}"


def _validate_filename(filename: str) -> None:
    if not _FILENAME_RE.fullmatch(filename or ""):
        raise ImageInputError("图片内部引用的文件名无效")


def _local_filename(raw_url: str) -> str | None:
    """识别内部引用及旧版 loopback URL；公网 HTTPS 返回 ``None``。"""
    parsed = urlparse(raw_url)
    if parsed.scheme == "https" and parsed.hostname not in _LOOPBACK_HOSTS:
        if not parsed.hostname or parsed.username or parsed.password:
            raise ImageInputError("公网图片地址无效或包含 URL 凭据")
        return None
    if parsed.query or parsed.fragment:
        raise ImageInputError("本地图片引用不能包含查询参数或片段")
    if parsed.scheme == _INTERNAL_SCHEME:
        if parsed.netloc != _LOCAL_DIRECTORY or parsed.path.count("/") != 1:
            raise ImageInputError("图片内部引用超出允许目录")
        filename = parsed.path.removeprefix("/")
        _validate_filename(filename)
        return filename
    is_relative_upload = not parsed.scheme and not parsed.netloc
    is_loopback_upload = parsed.scheme in {"http", "https"} and parsed.hostname in _LOOPBACK_HOSTS
    if is_relative_upload or is_loopback_upload:
        parts = tuple(part for part in parsed.path.split("/") if part)
        if len(parts) != 3 or tuple(part.lower() for part in parts[:2]) != ("upload", _LOCAL_DIRECTORY):
            raise ImageInputError("本地图片引用超出 upload/common 目录")
        filename = parts[-1]
        _validate_filename(filename)
        return filename
    raise ImageInputError("图片只允许使用内部上传引用或公网 HTTPS 地址")


def _detect_media_type(content: bytes) -> str | None:
    """以文件签名确认实际图片类型，避免只信扩展名或请求 MIME。"""
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _read_local_image(filename: str, declared_media_type: str) -> Base64Source:
    """按当前上传策略读取受控文件，并生成只用于本次调用的 Base64Source。"""
    _validate_filename(filename)
    path = (_UPLOAD_ROOT / filename).resolve()
    if path.parent != _UPLOAD_ROOT:
        raise ImageInputError("图片路径超出允许目录")
    config = await get_image_upload_config()
    extension = path.suffix.lower()
    if extension not in config["extensions"]:
        raise ImageInputError("图片扩展名不符合当前上传策略")
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise ImageInputError("图片文件不存在或已被清理") from exc
    if size < 1:
        raise ImageInputError("图片文件为空")
    if size > config["max_size"]:
        raise ImageInputError("图片大小超过当前上传策略")
    content = await asyncio.to_thread(path.read_bytes)
    expected_media_type = _MIME_BY_EXTENSION.get(extension)
    detected_media_type = _detect_media_type(content)
    if declared_media_type != expected_media_type or detected_media_type != expected_media_type:
        raise ImageInputError("图片 MIME、扩展名或文件内容不匹配")
    return Base64Source(
        data=base64.b64encode(content).decode("ascii"),
        media_type=expected_media_type,
    )


def _image_blocks(messages: Msg | list[Msg] | None):
    rows = [messages] if isinstance(messages, Msg) else list(messages or [])
    for message in rows:
        if not isinstance(message, Msg):
            continue
        for block in message.content:
            if isinstance(block, DataBlock) and block.source.media_type.startswith("image/"):
                yield block
            elif isinstance(block, HintBlock) and isinstance(block.hint, list):
                for child in block.hint:
                    if isinstance(child, DataBlock) and child.source.media_type.startswith("image/"):
                        yield child


async def validate_incoming_images(messages: Msg | list[Msg] | None, support_vision: bool) -> None:
    """在消息进入 Agent 上下文前校验数量、模型能力和客户端图片来源。"""
    blocks = list(_image_blocks(messages))
    if len(blocks) > MAX_IMAGES_PER_TURN:
        raise ImageInputError(f"每次最多发送 {MAX_IMAGES_PER_TURN} 张图片")
    if blocks and not support_vision:
        raise ImageInputError("当前模型不支持图片输入")
    for block in blocks:
        if isinstance(block.source, Base64Source):
            raise ImageInputError("客户端不能直接提交 Base64 图片")
        filename = _local_filename(str(block.source.url))
        if filename is not None:
            await _read_local_image(filename, block.source.media_type)


async def prepare_model_messages(messages: list[Msg]) -> list[Msg]:
    """深拷贝模型上下文，并仅在副本中把本地图片转换为 Base64。"""
    copied = deepcopy(messages)
    for block in _image_blocks(copied):
        if isinstance(block.source, Base64Source):
            continue
        filename = _local_filename(str(block.source.url))
        if filename is not None:
            block.source = await _read_local_image(filename, block.source.media_type)
    return copied


__all__ = [
    "MAX_IMAGES_PER_TURN",
    "ImageInputError",
    "build_model_image_url",
    "prepare_model_messages",
    "validate_incoming_images",
]
