"""插件更新包的校验、规范化暂存与完整性检查。"""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from core.config import BASE_DIR
from core.plugin_manager import PluginManager


STAGING_DIR = Path(BASE_DIR) / "runtime" / "plugin-update-staging"


def _safe_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    parsed = urlsplit(normalized)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f"插件包路径必须是本地相对路径: {name}")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"插件包路径越界: {name}")
    return path.parts


def _zip_manifest_root(archive: zipfile.ZipFile) -> tuple[str, dict]:
    files = [item for item in archive.infolist() if not item.is_dir()]
    manifest_items = []
    for item in files:
        parts = _safe_parts(item.filename)
        if stat.S_ISLNK(item.external_attr >> 16):
            raise ValueError(f"插件 ZIP 不允许符号链接: {item.filename}")
        if parts[-1] == "plugin.json":
            manifest_items.append((item, parts))
    if len(manifest_items) != 1:
        raise ValueError("插件 ZIP 必须且只能包含一个 plugin.json")
    manifest_item, manifest_parts = manifest_items[0]
    if len(manifest_parts) > 2:
        raise ValueError("plugin.json 只能位于 ZIP 根目录或唯一的单层包装目录")
    prefix = "" if len(manifest_parts) == 1 else f"{manifest_parts[0]}/"
    for item in files:
        normalized = "/".join(_safe_parts(item.filename))
        if prefix and not normalized.startswith(prefix):
            raise ValueError("插件 ZIP 的文件必须位于同一个单层包装目录")
    try:
        data = json.loads(archive.read(manifest_item).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("插件 manifest 不是有效的 UTF-8 JSON") from exc
    return prefix, data


def _directory_manifest(path: Path) -> dict:
    manifest_path = path / "plugin.json"
    if not manifest_path.is_file():
        raise ValueError("插件包目录缺少 plugin.json")
    manifests = [item for item in path.rglob("plugin.json") if item.is_file()]
    if len(manifests) != 1 or manifests[0] != manifest_path:
        raise ValueError("插件包目录必须且只能包含一个根级 plugin.json")
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"插件包目录不允许符号链接: {item}")
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("插件 manifest 不是有效的 UTF-8 JSON") from exc


def _validate_manifest(plugin_id: str, data: object, manager: PluginManager) -> dict:
    if not isinstance(data, dict):
        raise ValueError("插件 manifest 顶层必须是对象")
    if str(data.get("name") or "") != plugin_id:
        raise ValueError("插件包 name 与目标插件不一致")
    owner_id = str(data.get("owner_id") or data.get("owner") or plugin_id)
    if owner_id != plugin_id:
        raise ValueError("插件包 owner_id 与目标插件不一致")
    version = str(data.get("version") or "")
    if not version:
        raise ValueError("插件包缺少 version")
    manager.compare_versions(version, version)
    for field in ("dependencies", "conflicts", "permissions", "capabilities", "pages", "resources", "tasks", "events"):
        if field in data and not isinstance(data[field], list):
            raise ValueError(f"插件 manifest 字段必须是数组: {field}")
    for field in ("dependencies", "conflicts"):
        for item in data.get(field, []):
            item_name = item if isinstance(item, str) else item.get("name", "") if isinstance(item, dict) else ""
            if not item_name or item_name == plugin_id:
                raise ValueError(f"插件 manifest {field} 声明无效: {item!r}")
    for item in data.get("resources", []):
        value = item if isinstance(item, str) else item.get("path", "") if isinstance(item, dict) else ""
        if not value or len(_safe_parts(str(value))) < 1:
            raise ValueError(f"插件资源必须是包内本地路径: {value}")
    compatible = data.get("compatible_app_versions", [])
    if not isinstance(compatible, list) or any(not isinstance(item, str) or not item.strip() for item in compatible):
        raise ValueError("插件 manifest compatible_app_versions 必须是非空字符串数组")
    if not isinstance(data.get("health", {}), dict):
        raise ValueError("插件 manifest health 必须是对象")
    return data


def inspect_plugin_package(plugin_id: str, package_ref: str, manager: PluginManager | None = None) -> dict:
    """读取本地更新包元数据，同时校验目录结构和插件模块不变。"""
    manager = manager or PluginManager()
    current_rel = manager._find_plugin_path(plugin_id)
    current_path = manager.plugins_dir / current_rel
    if not current_path.is_dir() or "/" not in current_rel:
        raise ValueError(f"插件 '{plugin_id}' 尚未安装")
    supplied = Path(package_ref) if package_ref else current_path
    if supplied.is_symlink():
        raise ValueError("插件包不允许使用符号链接")
    source = supplied.resolve()
    if source.is_dir():
        data = _directory_manifest(source)
        package_kind, prefix = "directory", ""
    elif source.is_file() and source.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source) as archive:
                prefix, data = _zip_manifest_root(archive)
        except zipfile.BadZipFile as exc:
            raise ValueError("插件 ZIP 文件无效") from exc
        package_kind = "zip"
    else:
        raise ValueError("插件包必须是目录或 ZIP 文件")
    data = _validate_manifest(plugin_id, data, manager)
    module = current_rel.split("/", 1)[0]
    declared_module = str(data.get("module") or module)
    if declared_module != module:
        raise ValueError("插件更新不允许变更所属模块")
    return {
        "source": source,
        "kind": package_kind,
        "prefix": prefix,
        "manifest": data,
        "module": module,
        "version": str(data["version"]),
    }


def _write_normalized_archive(info: dict, target: Path) -> None:
    source = info["source"]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as output:
        if info["kind"] == "directory":
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    output.write(path, path.relative_to(source).as_posix())
            return
        with zipfile.ZipFile(source) as archive:
            prefix = info["prefix"]
            for item in archive.infolist():
                if item.is_dir():
                    continue
                normalized = "/".join(_safe_parts(item.filename))
                relative = normalized[len(prefix):] if prefix else normalized
                with archive.open(item, "r") as reader, output.open(relative, "w") as writer:
                    shutil.copyfileobj(reader, writer)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_plugin_package(plugin_id: str, operation_id: str, info: dict) -> tuple[str, str]:
    """把可变本地来源规范化为运行目录中的不可变 ZIP 快照。"""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    target = STAGING_DIR / f"{operation_id}.zip"
    temporary = target.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    try:
        _write_normalized_archive(info, temporary)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return str(target), file_sha256(target)


def verify_staged_package(plan: dict) -> dict:
    """校验计划暂存包未被替换，并返回 manifest。"""
    path = Path(str(plan.get("package_ref") or ""))
    if not path.is_file() or path.parent.resolve() != STAGING_DIR.resolve():
        raise ValueError("插件更新暂存包不存在或路径无效")
    expected = str(plan.get("package_digest") or "")
    if not expected or file_sha256(path) != expected:
        raise ValueError("插件更新暂存包摘要不一致")
    with zipfile.ZipFile(path) as archive:
        prefix, data = _zip_manifest_root(archive)
        if prefix:
            raise ValueError("规范化暂存包结构无效")
    manager = PluginManager()
    data = _validate_manifest(str(plan["plugin_id"]), data, manager)
    if str(data.get("version") or "") != str(plan.get("target_version") or ""):
        raise ValueError("暂存包版本与更新计划不一致")
    if str(data.get("module") or plan.get("package_module") or "") != str(plan.get("package_module") or ""):
        raise ValueError("暂存包模块与更新计划不一致")
    return data


def extract_staged_package(plan: dict, destination: Path) -> dict:
    """把已验证的规范化暂存包安全解压到空目录。"""
    manifest = verify_staged_package(plan)
    if destination.exists():
        raise ValueError("插件更新解包目录已存在")
    destination.mkdir(parents=True)
    path = Path(str(plan["package_ref"]))
    try:
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                if item.is_dir():
                    continue
                parts = _safe_parts(item.filename)
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError(f"插件 ZIP 不允许符号链接: {item.filename}")
                target = destination.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item, "r") as reader, target.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return manifest


def cleanup_staged_package(plan: dict) -> None:
    path = Path(str(plan.get("package_ref") or ""))
    if path.parent.resolve() == STAGING_DIR.resolve():
        path.unlink(missing_ok=True)
