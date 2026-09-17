"""七牛云 SDK 适配层。

七牛 SDK 为同步实现，所有网络调用均由异步门面转移到线程，避免阻塞
FastAPI 事件循环。该文件不保存任何运行时密钥。
"""

import asyncio
import mimetypes
import re
from typing import Any
from urllib.parse import quote

REGION_MAP = {
    "z0": "华东-浙江",
    "z1": "华北-河北",
    "z2": "华南-广东",
    "na0": "北美",
    "as0": "东南亚",
}
_REGIONS = frozenset(REGION_MAP)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_FOLDER_SCAN_LIMIT = 1000


def normalize_save_path(value: str | None) -> str:
    """规范化保存路径，并拒绝绝对路径、路径穿越和控制字符。"""
    raw = str(value or "").strip().replace("\\", "/")
    if _CONTROL_CHARS.search(raw) or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("保存路径包含不安全字符")
    raw = raw.lstrip("/")
    parts = [part for part in raw.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("保存路径不允许路径穿越")
    return "/".join(parts)


def normalize_object_key(value: str, save_path: str = "") -> str:
    """规范化对象键，确保对象始终位于插件保存路径下。"""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or _CONTROL_CHARS.search(raw):
        raise ValueError("对象键不合法")
    raw = raw.lstrip("/")
    if not raw:
        raise ValueError("对象键不合法")
    parts = [part for part in raw.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("对象键不允许路径穿越")
    normalized_prefix = normalize_save_path(save_path)
    key = "/".join(parts)
    if normalized_prefix and not (key == normalized_prefix or key.startswith(normalized_prefix + "/")):
        raise ValueError("对象键必须位于保存路径下")
    return key


def build_object_key(save_path: str | None, file_name: str) -> str:
    """按“保存路径 + 文件名”生成对象键。"""
    prefix = normalize_save_path(save_path)
    raw_name = str(file_name or "").strip()
    if "/" in raw_name or "\\" in raw_name:
        raise ValueError("文件名不允许包含路径")
    name = raw_name
    if not name:
        raise ValueError("文件名不能为空")
    key = normalize_object_key(f"{prefix}/{name}" if prefix else name, prefix)
    if len(key) > 512:
        raise ValueError("对象键长度不能超过 512")
    return key


def normalize_region(value: str | None) -> str:
    """校验七牛区域标识。"""
    region = str(value or "z0").strip().lower()
    if region not in _REGIONS:
        raise ValueError("不支持的七牛区域")
    return region


class QiniuService:
    """七牛 SDK 的小型异步门面。"""

    def __init__(self, config: dict[str, Any] | None = None):
        values = config or {}
        self.access_key = str(values.get("access_key") or "").strip()
        self.secret_key = str(values.get("secret_key") or "").strip()
        self.bucket = str(values.get("bucket") or "").strip()
        self.domain = str(values.get("domain") or "").strip().rstrip("/")
        if self.domain and not self.domain.startswith(("http://", "https://")):
            self.domain = "https://" + self.domain
        self.region = normalize_region(values.get("region"))
        self.private = str(values.get("private", "0")).lower() in {"1", "true", "yes", "on"}
        self.save_path = normalize_save_path(values.get("save_path"))

    def validate(self) -> None:
        """校验建立 SDK 客户端所需的配置。"""
        if not self.access_key or not self.secret_key:
            raise ValueError("请先配置七牛 AccessKey 和 SecretKey")
        if not self.bucket:
            raise ValueError("请先配置七牛存储空间")
        if not self.domain:
            raise ValueError("请先配置七牛加速域名")

    def _auth(self):
        from qiniu import Auth

        self.validate()
        return Auth(self.access_key, self.secret_key)

    def _bucket_manager(self):
        from qiniu import BucketManager, Zone

        return BucketManager(self._auth(), zone=Zone.from_region_id(self.region))

    @staticmethod
    def _check_response_info(response_info: Any) -> None:
        """检查七牛 SDK 响应对象，避免把 HTTP 200 误判成错误。"""
        if response_info is None:
            return
        ok = getattr(response_info, "ok", None)
        if callable(ok) and ok():
            return
        message = getattr(response_info, "error", None)
        if message:
            raise RuntimeError(str(message))
        status_code = getattr(response_info, "status_code", None)
        if status_code is not None:
            raise RuntimeError(f"七牛请求失败（HTTP {status_code}）")
        raise RuntimeError("七牛请求失败")

    def _test_sync(self) -> dict:
        list_prefix = f"{self.save_path}/" if self.save_path else None
        result, _eof, response_info = self._bucket_manager().list(
            self.bucket, prefix=list_prefix, limit=1, delimiter="/",
        )
        self._check_response_info(response_info)
        return {"items": (result or {}).get("items", [])}

    async def test_connection(self) -> dict:
        """调用七牛列举接口验证凭据、区域和存储空间。"""
        return await asyncio.to_thread(self._test_sync)

    def _list_sync(self, prefix: str, marker: str, limit: int, include_folders: bool = False) -> dict:
        list_prefix = f"{prefix.rstrip('/')}/" if prefix else None
        bucket_manager = self._bucket_manager()
        result, _eof, response_info = bucket_manager.list(
            self.bucket, prefix=list_prefix, marker=marker or None, limit=limit, delimiter="/",
        )
        self._check_response_info(response_info)
        result = result or {}
        common_prefixes = result.get("commonPrefixes") or result.get("common_prefixes") or []
        if include_folders and not marker:
            # 七牛的 delimiter 列举会把目录和文件一起分页。文件页前面的对象可能
            # 把目录推到后续页，因此首次打开目录时继续扫描后续页，只合并目录前缀，
            # 不改变当前文件页和分页游标，避免目录必须点到下一页才能出现。
            scan_marker = result.get("marker", "")
            while scan_marker:
                next_result, _next_eof, next_response_info = bucket_manager.list(
                    self.bucket,
                    prefix=list_prefix,
                    marker=scan_marker,
                    limit=_FOLDER_SCAN_LIMIT,
                    delimiter="/",
                )
                self._check_response_info(next_response_info)
                next_result = next_result or {}
                common_prefixes.extend(
                    next_result.get("commonPrefixes") or next_result.get("common_prefixes") or []
                )
                next_marker = next_result.get("marker", "")
                if next_marker == scan_marker:
                    raise RuntimeError("七牛目录分页游标未推进")
                scan_marker = next_marker
        return {
            "items": result.get("items") or [],
            "marker": result.get("marker", ""),
            "has_more": bool(result.get("marker")),
            # 七牛 SDK 使用 camelCase；保留 snake_case 回退便于 Mock 和未来 SDK 版本兼容。
            "common_prefixes": list(dict.fromkeys(common_prefixes)),
        }

    async def list_files(
        self, prefix: str = "", marker: str = "", limit: int = 100, include_folders: bool = False,
    ) -> dict:
        """分页列举对象。"""
        requested = normalize_save_path(prefix) if prefix else self.save_path
        if requested and self.save_path and not (
            requested == self.save_path or requested.startswith(self.save_path + "/")
        ):
            raise ValueError("前缀必须位于保存路径下")
        return await asyncio.to_thread(self._list_sync, requested, marker, limit, include_folders)

    def _upload_sync(self, file_path: str, key: str, mime_type: str) -> dict:
        from qiniu import put_file

        token = self._auth().upload_token(self.bucket, key=key, expires=3600)
        result, response_info = put_file(token, key, file_path, mime_type=mime_type)
        self._check_response_info(response_info)
        return result or {}

    async def upload_file(self, file_path: str, key: str, mime_type: str | None = None) -> dict:
        """上传本地文件到七牛。"""
        content_type = mime_type or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        return await asyncio.to_thread(self._upload_sync, file_path, key, content_type)

    def _delete_sync(self, key: str) -> None:
        _, response_info = self._bucket_manager().delete(self.bucket, key)
        self._check_response_info(response_info)

    async def delete_file(self, key: str) -> None:
        """删除七牛对象。"""
        await asyncio.to_thread(self._delete_sync, key)

    def _stat_sync(self, key: str) -> dict | None:
        result, response_info = self._bucket_manager().stat(self.bucket, key)
        status_code = getattr(response_info, "status_code", None)
        if status_code == 612:
            return None
        self._check_response_info(response_info)
        return result or {}

    async def stat_file(self, key: str) -> dict | None:
        """查询对象元数据；对象不存在返回 None。"""
        return await asyncio.to_thread(self._stat_sync, key)

    async def access_url(self, key: str, expires: int = 3600, action: str = "preview") -> str:
        """生成公开直链或私有临时签名地址，下载动作附加附件文件名。"""
        if not self.domain:
            raise ValueError("请先配置七牛加速域名")
        bounded = max(60, min(int(expires), 86400))
        url = f"{self.domain}/{key}"
        if action == "download":
            filename = quote(key.rsplit("/", 1)[-1] or "download", safe="")
            url += f"?attname={filename}"
        if self.private:
            return await asyncio.to_thread(
                lambda: self._auth().private_download_url(url, bounded)
            )
        return url

    def object_key(self, value: str) -> str:
        """将用户提供的键限制到配置的保存路径。"""
        return normalize_object_key(value, self.save_path)
