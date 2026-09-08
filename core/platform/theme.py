"""三端主题公共门面。"""

import json
import zipfile
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from core.config_service import set_config
from core.platform.audit import audit_log
from core.platform.event import publish_lifecycle_event
from core.platform.health import platform_health
from core.platform.lock import platform_lock
from core.platform.resource import resource_registry
from core.theme_manager import THEME_MODULES, theme_manager


class ThemePlatform:
    """对现有 ThemeManager 增加快照、资源版本和审计语义。"""

    def __init__(self) -> None:
        self._snapshots: dict[str, dict] = {}
        self._previous: dict[str, dict] = {}
        self._installed: dict[tuple[str, str], dict] = {}
        self._resource_versions: dict[str, int] = {surface: 0 for surface in THEME_MODULES}

    def discover(self, surface: str) -> list[dict]:
        self._require_surface(surface)
        return theme_manager.list_themes(surface)

    def get_active_snapshot(self, surface: str) -> dict:
        self._require_surface(surface)
        theme = theme_manager.get_active(surface)
        snapshot = self._snapshots.get(surface)
        if not snapshot or snapshot.get("theme_id") != theme:
            snapshot = self._build_snapshot(surface, theme)
            self._snapshots[surface] = snapshot
        return dict(snapshot)

    async def install(self, surface: str, package_ref: str = "", request=None) -> dict:
        """登记并校验本地主题包；安装阶段不改变当前活动主题。"""
        if not package_ref:
            raise ValueError("主题安装必须提供本机已暂存目录或 ZIP 包")
        validation = self.validate_theme_package(surface, package_ref)
        theme_id = validation.get("theme_id", "")
        operation_id = f"theme-{uuid4().hex}"
        record = {
            **validation,
            "status": "installed",
            "operation_id": operation_id,
        }
        self._installed[(surface, theme_id)] = dict(record)
        await audit_log(
            f"登记{surface}主题: {theme_id}", "theme_install",
            owner=f"theme:{surface}:{theme_id}", version=validation.get("version", ""),
            operation_id=operation_id, phase="install", request=request,
        )
        return dict(record)

    async def activate(self, surface: str, theme_id: str, request=None) -> dict:
        self._require_surface(surface)
        if not theme_manager.theme_exists(surface, theme_id):
            raise ValueError(f"主题 '{theme_id}' 不存在或无效")
        self._validate_installed_theme(surface, theme_id)
        owner = f"theme:{surface}"
        if not await platform_lock.acquire(owner, owner):
            raise RuntimeError("主题端面正在更新，请稍后重试")
        operation_id = f"theme-{uuid4().hex}"
        previous = self.get_active_snapshot(surface)
        previous_resource_version = self._resource_versions.get(surface, 0)
        try:
            resolved = theme_manager.set_active(surface, theme_id)
            await set_config(f"{surface}_theme", resolved)
            self._previous[surface] = previous
            self._resource_versions[surface] = self._resource_versions.get(surface, 0) + 1
            snapshot = self._build_snapshot(surface, resolved, operation_id)
            self._snapshots[surface] = snapshot
            manifest = theme_manager._read_manifest(surface, resolved) or {}
            resource_registry.invalidate_surface(surface, owner_prefix=f"theme:{surface}:")
            resource_registry.register_manifest(
                {**manifest, "surface": surface}, f"theme:{surface}:{resolved}"
            )
            platform_health.mark(f"theme:{surface}", "active", theme_id=resolved)
            await audit_log(
                f"切换{surface}主题: {resolved}", "theme_activate",
                owner=owner, version=snapshot["version"], current_version=previous.get("version", ""), operation_id=operation_id,
                phase="activate", request=request,
            )
            await publish_lifecycle_event("theme.activated", {
                "surface": surface, "theme_id": resolved,
                "version": snapshot["version"], "operation_id": operation_id,
            }, correlation_id=operation_id)
            return snapshot
        except Exception as exc:
            platform_health.mark(f"theme:{surface}", "failed", str(exc))
            try:
                theme_manager.set_active(surface, previous["theme_id"])
                await set_config(f"{surface}_theme", previous["theme_id"])
                self._snapshots[surface] = previous
                self._resource_versions[surface] = previous_resource_version
                previous_manifest = theme_manager._read_manifest(surface, previous["theme_id"]) or {}
                resource_registry.invalidate_surface(surface, owner_prefix=f"theme:{surface}:")
                resource_registry.register_manifest(
                    {**previous_manifest, "surface": surface},
                    f"theme:{surface}:{previous['theme_id']}",
                )
            except Exception as rollback_exc:
                platform_health.mark(f"theme:{surface}", "degraded", str(rollback_exc))
            await audit_log(
                f"主题激活失败: {surface}/{theme_id}", "theme_rollback",
                owner=owner, version=previous.get("version", ""),
                current_version=previous.get("version", ""), operation_id=operation_id, phase="activate",
                result="failed", error_reason=str(exc), request=request,
            )
            try:
                await publish_lifecycle_event("theme.rollback", {
                    "surface": surface, "theme_id": previous.get("theme_id", ""),
                    "version": previous.get("version", ""), "operation_id": operation_id,
                }, correlation_id=operation_id)
            except Exception:
                pass
            raise
        finally:
            platform_lock.release(owner, owner)

    async def rollback(self, surface: str, request=None) -> dict:
        self._require_surface(surface)
        previous = self._previous.get(surface)
        if not previous:
            raise ValueError("没有可回退的主题快照")
        snapshot = await self.activate(surface, previous["theme_id"], request=request)
        operation_id = snapshot.get("operation_id", "")
        owner = f"theme:{surface}"
        await audit_log(
            f"主题回退: {surface}/{snapshot['theme_id']}", "theme_rollback",
            owner=owner, version=snapshot.get("version", ""),
            operation_id=operation_id, phase="rollback", request=request,
        )
        await publish_lifecycle_event("theme.rollback", {
            "surface": surface, "theme_id": snapshot["theme_id"],
            "version": snapshot.get("version", ""),
            "operation_id": operation_id,
        }, correlation_id=operation_id)
        return snapshot

    def resolve_template(self, surface: str, template_key: str) -> str:
        self._require_surface(surface)
        normalized_key = (template_key or "").replace("\\", "/")
        if not normalized_key or normalized_key.startswith("/") or ".." in normalized_key.split("/"):
            raise ValueError("主题模板 key 无效")
        snapshot = self.get_active_snapshot(surface)
        manifest = theme_manager._read_manifest(surface, snapshot["theme_id"]) or {}
        declared = manifest.get("template_overrides") or []
        if declared and normalized_key not in [str(item).replace("\\", "/") for item in declared]:
            raise ValueError(f"主题模板未声明: {normalized_key}")
        active_path = theme_manager.theme_dir(surface, snapshot["theme_id"]) / normalized_key
        fallback_theme = theme_manager.default_theme(surface)
        fallback_path = theme_manager.theme_dir(surface, fallback_theme) / normalized_key
        if not active_path.is_file() and not fallback_path.is_file():
            raise FileNotFoundError(f"主题模板不存在: {surface}/{normalized_key}")
        return normalized_key

    def build_asset_url(self, surface: str, asset_key: str) -> str:
        snapshot = self.get_active_snapshot(surface)
        asset_key = (asset_key or "").replace("\\", "/").lstrip("/")
        if not asset_key or ".." in asset_key.split("/"):
            raise ValueError("主题资源 key 无效")
        asset_path = theme_manager.theme_dir(surface, snapshot["theme_id"]) / "assets" / asset_key
        fallback_theme = theme_manager.default_theme(surface)
        fallback_path = theme_manager.theme_dir(surface, fallback_theme) / "assets" / asset_key
        selected_theme = snapshot["theme_id"]
        if not asset_path.is_file() and fallback_path.is_file():
            selected_theme = fallback_theme
        if not asset_path.is_file() and not fallback_path.is_file():
            raise FileNotFoundError(f"主题资源不存在: {surface}/{asset_key}")
        manifest = theme_manager._read_manifest(surface, selected_theme) or {}
        declared = manifest.get("static_assets") or []
        if declared:
            declared_keys = {
                (item if isinstance(item, str) else item.get("key") or item.get("path", "")).replace("\\", "/").lstrip("/")
                for item in declared if isinstance(item, (str, dict))
            }
            declared_paths = {
                (item if isinstance(item, str) else item.get("path", "")).replace("\\", "/").lstrip("/")
                for item in declared if isinstance(item, (str, dict))
            }
            if asset_key not in declared_keys and f"assets/{asset_key}" not in declared_paths:
                raise ValueError(f"主题资源未声明: {asset_key}")
        return f"/theme/{surface}/{selected_theme}/{asset_key}?rv={snapshot['resource_version']}"

    def validate_theme_package(self, surface: str, package_ref: str) -> dict:
        self._require_surface(surface)
        if not package_ref:
            return {"surface": surface, "valid": True, "source": "installed"}
        path = Path(package_ref).resolve()
        if path.is_dir():
            manifest_path = path / "theme.json"
            if not manifest_path.is_file():
                raise ValueError("主题目录缺少 theme.json")
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"主题 manifest 无法读取: {exc}") from exc
            self._validate_package_files(data, path)
        elif path.is_file() and path.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                    self._validate_zip_names(names)
                    manifest_name = next((name for name in names if name.endswith("theme.json")), "")
                    if not manifest_name:
                        raise ValueError("主题包缺少 theme.json")
                    try:
                        data = json.loads(archive.read(manifest_name).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError(f"主题 manifest 无法读取: {exc}") from exc
                    self._validate_package_files(data, names, manifest_name)
            except zipfile.BadZipFile as exc:
                raise ValueError("主题 ZIP 文件无效") from exc
        else:
            raise ValueError("主题包必须是目录或 ZIP 文件")
        self._validate_manifest(data, surface)
        declared = data.get("surface") or data.get("module")
        if declared and declared != surface:
            raise ValueError("主题包端面与目标端面不一致")
        if not (data.get("theme_id") or data.get("key")):
            raise ValueError("主题 manifest 缺少 theme_id/key")
        return {"surface": surface, "theme_id": data.get("theme_id") or data.get("key"), "version": data.get("version", ""), "valid": True}

    @staticmethod
    def _validate_manifest(data: dict, surface: str) -> None:  # noqa: C901
        """校验主题 Standard manifest 的稳定字段和本地资源边界。"""
        if not isinstance(data, dict):
            raise ValueError("主题 manifest 顶层必须是对象")
        theme_id = str(data.get("theme_id") or data.get("key") or "")
        declared_surface = str(data.get("surface") or data.get("module") or "")
        if not theme_id or not declared_surface or declared_surface != surface:
            raise ValueError("主题 manifest 缺少 theme_id 或 surface 不匹配")
        if not str(data.get("version") or ""):
            raise ValueError("主题 manifest 缺少 version")
        for field in ("dependencies", "conflicts", "template_overrides", "static_assets"):
            value = data.get(field, [])
            if not isinstance(value, list):
                raise ValueError(f"主题 manifest 字段必须是数组: {field}")
        for field in ("dependencies", "conflicts"):
            if any(not isinstance(item, (str, dict)) for item in data.get(field, [])):
                raise ValueError(f"主题 manifest 字段声明无效: {field}")
        for item in data.get("template_overrides", []):
            if not isinstance(item, str) or not item or item.startswith(("/", "\\")) or ".." in item.replace("\\", "/").split("/"):
                raise ValueError(f"主题模板声明无效: {item}")
        for item in data.get("static_assets", []):
            if not isinstance(item, (str, dict)):
                raise ValueError(f"主题资源声明无效: {item!r}")
            path = item if isinstance(item, str) else item.get("path", "")
            parsed = urlsplit(str(path))
            if not path or parsed.scheme or parsed.netloc or str(path).startswith(("/", "\\")) or ".." in str(path).replace("\\", "/").split("/"):
                raise ValueError(f"主题资源必须是包内本地路径: {path}")
        component_tokens = data.get("component_tokens", {})
        if not isinstance(component_tokens, dict):
            raise ValueError("主题 manifest 字段必须是对象: component_tokens")
        for token in component_tokens:
            if not isinstance(token, str) or not token.startswith("--td-"):
                raise ValueError(f"主题令牌必须使用受控 --td-* 名称: {token}")

    def _build_snapshot(self, surface: str, theme_id: str, operation_id: str = "") -> dict:
        manifest = theme_manager._read_manifest(surface, theme_id) or {}
        return {
            "surface": surface,
            "theme_id": theme_id,
            "version": str(manifest.get("version") or ""),
            "resource_version": self._resource_versions.get(surface, 0),
            "fallback_theme": manifest.get("fallback_theme") or theme_manager.default_theme(surface),
            "operation_id": operation_id,
        }

    def _validate_installed_theme(self, surface: str, theme_id: str) -> None:
        """激活前重新校验已登记主题的 manifest 和包内资源。"""
        manifest = theme_manager._read_manifest(surface, theme_id)
        if manifest is None:
            raise ValueError(f"主题 '{theme_id}' manifest 无效")
        self._validate_manifest(manifest, surface)
        fallback_theme = str(manifest.get("fallback_theme") or theme_manager.default_theme(surface))
        if not theme_manager.theme_exists(surface, fallback_theme):
            raise ValueError(f"主题回退主题不存在: {surface}/{fallback_theme}")
        root = theme_manager.theme_dir(surface, theme_id)
        for item in manifest.get("static_assets", []):
            path = item if isinstance(item, str) else item.get("path", "")
            candidate = (root / str(path).replace("\\", "/")).resolve()
            if root not in candidate.parents or not candidate.is_file():
                raise ValueError(f"主题资源未找到: {path}")
        for item in manifest.get("template_overrides", []):
            candidate = (root / str(item).replace("\\", "/")).resolve()
            if root not in candidate.parents or not candidate.is_file():
                raise ValueError(f"主题模板未找到: {item}")

    @staticmethod
    def _validate_package_files(data: dict, source, manifest_name: str = "theme.json") -> None:
        """校验主题包声明的模板和资源确实位于包内。"""
        if not isinstance(data, dict):
            raise ValueError("主题 manifest 顶层必须是对象")
        is_directory = isinstance(source, Path)
        if is_directory:
            root = source.resolve()
            if not (root / "base.html").is_file():
                raise ValueError("主题包缺少 base.html")

            def exists(relative: str) -> bool:
                candidate = (root / relative.replace("\\", "/")).resolve()
                return root in candidate.parents and candidate.is_file()
        else:
            names = {str(item).replace("\\", "/").lstrip("/") for item in source}
            prefix = manifest_name.replace("\\", "/").rsplit("/", 1)[0]
            prefix = f"{prefix}/" if prefix else ""
            if f"{prefix}base.html" not in names:
                raise ValueError("主题包缺少 base.html")

            def exists(relative: str) -> bool:
                normalized = relative.replace("\\", "/").lstrip("/")
                return f"{prefix}{normalized}" in names

        for item in data.get("template_overrides", []):
            if not exists(str(item)):
                raise ValueError(f"主题模板未找到: {item}")
        for item in data.get("static_assets", []):
            path = item if isinstance(item, str) else item.get("path", "")
            if not exists(str(path)):
                raise ValueError(f"主题资源未找到: {path}")

    @staticmethod
    def _validate_zip_names(names: list[str]) -> None:
        for name in names:
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError(f"主题包路径越界: {name}")

    @staticmethod
    def _require_surface(surface: str) -> None:
        if surface not in THEME_MODULES:
            raise ValueError(f"主题端面仅支持: {', '.join(THEME_MODULES)}")


theme_platform = ThemePlatform()
