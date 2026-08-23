"""Web 资源 URL 门面，底层复用 platform.resource。"""

import re

from core.platform.resource import asset_registry, resource_registry


_SAFE_OWNER = re.compile(r"^[A-Za-z0-9._-]+$")


def resolve(surface: str, asset_key: str, owner: str = "") -> dict | None:
    return asset_registry.resolve(surface, asset_key, owner=owner)


def register_manifest(manifest: dict, owner: str) -> list[dict]:
    return asset_registry.register_manifest(manifest, owner)


def invalidate(owner: str) -> int:
    return resource_registry.invalidate_owner(owner)


def build_url(surface: str, asset_key: str, owner: str = "") -> str:
    """生成带资源版本的本地 URL，不返回磁盘路径。"""
    if not surface or not asset_key:
        raise ValueError("资源端面和 key 不能为空")
    normalized_key = asset_key.replace("\\", "/").lstrip("/")
    if ".." in normalized_key.split("/"):
        raise ValueError("资源 key 无效")
    if owner and not _SAFE_OWNER.fullmatch(owner):
        raise ValueError("资源 owner 无效")
    record = resolve(surface, normalized_key, owner=owner)
    if not record:
        raise FileNotFoundError(f"资源不存在: {surface}/{normalized_key}")
    return f"/assets/{surface}/{owner or 'platform'}/{normalized_key}?rv={record['resource_version']}"
