# -*- coding: utf-8 -*-
"""快捷检测图片的安全下载、路径校验和解码工具。"""

import asyncio
import ipaddress
import socket
from io import BytesIO
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from core.config import BASE_DIR

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000


class ImagePreparationError(ValueError):
    """图片地址、下载内容或解码结果不符合检测要求。"""


async def assert_public_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImagePreparationError("图片地址仅支持HTTP(S)")
    if parsed.username or parsed.password:
        raise ImagePreparationError("图片地址不能携带访问凭据")
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise ImagePreparationError("图片服务器域名无法解析") from exc
    for address in {item[4][0] for item in addresses}:
        if not ipaddress.ip_address(address).is_global:
            raise ImagePreparationError("图片地址不能指向内网或保留地址")


async def _download_http_image(url: str) -> bytes:
    timeout = httpx.Timeout(30.0, connect=10.0, read=30.0, write=10.0, pool=10.0)
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _redirect in range(4):
            await assert_public_http_url(current)
            async with client.stream("GET", current, headers={"Accept": "image/*"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ImagePreparationError("图片重定向地址无效")
                    current = urljoin(current, location)
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise ImagePreparationError("图片下载失败") from exc
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if not content_type.lower().startswith("image/"):
                    raise ImagePreparationError("图片响应类型无效")
                length = response.headers.get("content-length")
                if length and int(length) > MAX_IMAGE_BYTES:
                    raise ImagePreparationError("图片大小超过20MB")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_IMAGE_BYTES:
                        raise ImagePreparationError("图片大小超过20MB")
                return bytes(chunks)
    raise ImagePreparationError("图片重定向次数过多")


async def read_image_bytes(image_url: str) -> bytes:
    if image_url.startswith("/upload/"):
        parsed_path = unquote(urlsplit(image_url).path).lstrip("/")
        upload_root = (BASE_DIR / "upload").resolve()
        path = (BASE_DIR / parsed_path).resolve()
        if not path.is_relative_to(upload_root) or not path.is_file():
            raise ImagePreparationError("站内图片不存在或路径无效")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ImagePreparationError("图片大小超过20MB")
        return await asyncio.to_thread(path.read_bytes)
    try:
        return await _download_http_image(image_url)
    except httpx.RequestError as exc:
        raise ImagePreparationError("图片下载超时或网络不可用") from exc


def decode_image(content: bytes):
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        image = Image.open(BytesIO(content))
        image = ImageOps.exif_transpose(image)
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ImagePreparationError("图片像素数量超过限制")
        image.load()
        return image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ImagePreparationError("图片文件无法解析") from exc
