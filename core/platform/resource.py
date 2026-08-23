"""主题、插件静态资源的 owner 隔离登记表。"""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ResourceRecord:
    key: str
    owner: str
    surface: str
    version: str
    resource_type: str
    path: str
    registered_at: str


class ResourceRegistry:
    """进程内资源索引；启动时由主题/插件扫描重建。"""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ResourceRecord] = {}
        self._versions: dict[tuple[str, str], int] = {}

    def register_manifest(self, manifest: dict, owner: str) -> list[dict]:
        """登记 manifest 中的本地资源，重复 key 或跨 owner 覆盖会失败。"""
        if not owner:
            raise ValueError("资源 owner 不能为空")
        surface = str(manifest.get("surface") or "")
        resources = manifest.get("static_assets") or manifest.get("resources") or []
        if isinstance(resources, dict):
            resources = [resources]
        registered = []
        for item in resources:
            if not isinstance(item, (str, dict)):
                raise ValueError(f"资源声明必须是字符串或对象: {item!r}")
            if isinstance(item, str):
                item = {"key": item, "path": item, "type": "asset"}
            key = str(item.get("key") or item.get("path") or "").replace("\\", "/").strip()
            path = str(item.get("path") or "").replace("\\", "/").strip()
            if not key or not path or key.startswith("/") or ".." in key.split("/"):
                raise ValueError(f"资源声明无效: {key or path}")
            parsed = urlsplit(path)
            if parsed.scheme or parsed.netloc or path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
                raise ValueError(f"资源路径必须是 owner 内本地相对路径: {path}")
            record_key = (surface, key)
            current = self._records.get(record_key)
            if current and current.owner != owner:
                raise ValueError(f"资源 key 冲突: {surface}/{key}")
            record = ResourceRecord(
                key=key,
                owner=owner,
                surface=surface,
                version=str(manifest.get("version") or ""),
                resource_type=str(item.get("type") or "asset"),
                path=path,
                registered_at=datetime.now().isoformat(),
            )
            self._records[record_key] = record
            self._versions[record_key] = self._versions.get(record_key, 0) + 1
            registered.append(self._to_dict(record))
        self._mark_health()
        return registered

    def resolve(self, surface: str, key: str, owner: str = "") -> dict | None:
        """解析稳定资源 key，不返回可写绝对路径。"""
        record = self._records.get((surface, key))
        if not record or (owner and record.owner != owner):
            return None
        return {
            **self._to_dict(record),
            "resource_version": self._versions.get((surface, key), 0),
        }

    def invalidate_owner(self, owner: str) -> int:
        """清理 owner 资源并递增其资源版本。"""
        keys = [key for key, record in self._records.items() if record.owner == owner]
        for key in keys:
            self._records.pop(key, None)
            self._versions[key] = self._versions.get(key, 0) + 1
        self._mark_health()
        return len(keys)

    def invalidate_surface(self, surface: str, owner_prefix: str = "") -> int:
        """按端面和可选 owner 前缀失效资源，避免清理其他插件。"""
        owners = {
            record.owner for (record_surface, _), record in self._records.items()
            if record_surface == surface and (not owner_prefix or record.owner.startswith(owner_prefix))
        }
        count = sum(self.invalidate_owner(owner) for owner in owners)
        self._mark_health()
        return count

    def snapshot(self, owner: str = "") -> list[dict]:
        records = [item for item in self._records.values() if not owner or item.owner == owner]
        return [self._to_dict(item) for item in records]

    def health_snapshot(self) -> dict:
        """返回资源索引摘要，不暴露底层路径或可写对象。"""
        return {"status": "ready", "records": len(self._records), "versions": len(self._versions)}

    def _mark_health(self) -> None:
        from core.platform.health import platform_health

        platform_health.mark_resource_registry(self.health_snapshot())

    @staticmethod
    def _to_dict(record: ResourceRecord) -> dict:
        return {
            "key": record.key,
            "owner": record.owner,
            "surface": record.surface,
            "version": record.version,
            "type": record.resource_type,
            "path": record.path,
            "registered_at": record.registered_at,
        }


resource_registry = ResourceRegistry()
# web.assets 使用相同的稳定索引，避免再创建一套前端资源注册表。
asset_registry = resource_registry
