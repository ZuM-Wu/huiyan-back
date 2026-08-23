# -*- coding: utf-8 -*-
"""统一上传策略读取、校验与首次保存迁移服务。"""
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.configuration import ConfigurationModel

CONFIG_KEY = "upload_policies"
SCHEMA_VERSION = 1
MAX_SIZE_MB = 2048
MAX_EXTENSIONS = 50
IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "gif", "webp")
_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,16}$")
_save_lock = asyncio.Lock()
logger = logging.getLogger(__name__)

CORE_POLICY_DEFINITIONS = [
    {
        "id": "core.image", "owner": "core", "owner_title": "系统",
        "label": "通用图片", "description": "站点图片、头像和聊天图片",
        "default_max_size_mb": 2,
        "default_extensions": list(IMAGE_EXTENSIONS),
        "extensions_editable": True,
        "allowed_extensions": list(IMAGE_EXTENSIONS),
        "legacy_keys": {
            "max_size_mb": "upload_image_max_size",
            "extensions": "upload_image_extensions",
        },
    },
    {
        "id": "core.file", "owner": "core", "owner_title": "系统",
        "label": "通用文件", "description": "后台通用文档与压缩包上传",
        "default_max_size_mb": 10,
        "default_extensions": [
            "pdf", "doc", "docx", "xls", "xlsx",
            "csv", "txt", "json", "zip", "rar",
        ],
        "extensions_editable": True,
        "legacy_keys": {
            "max_size_mb": "upload_file_max_size",
            "extensions": "upload_file_extensions",
        },
    },
]


class UploadPolicyError(ValueError):
    """上传策略业务错误，code 供 API 返回稳定错误标识。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_extensions(values: Any) -> list[str]:
    """把字符串或数组规范化为无点、小写、去重的扩展名数组。"""
    if isinstance(values, str):
        items = values.split(",")
    elif isinstance(values, list):
        items = values
    else:
        raise UploadPolicyError("invalid_upload_policy", "允许格式必须是数组")
    normalized: list[str] = []
    for raw in items:
        value = str(raw or "").strip().lower().lstrip(".")
        if not value or not _EXTENSION_RE.fullmatch(value):
            raise UploadPolicyError(
                "invalid_upload_policy", "扩展名只能包含 1-16 位小写字母或数字",
            )
        if value not in normalized:
            normalized.append(value)
    if not normalized or len(normalized) > MAX_EXTENSIONS:
        raise UploadPolicyError(
            "invalid_upload_policy", f"允许格式数量必须为 1-{MAX_EXTENSIONS} 项",
        )
    return normalized


def validate_policy_value(definition: dict, value: dict) -> dict:
    """按策略声明校验可保存值。"""
    try:
        max_size_mb = int(value.get("max_size_mb"))
    except (TypeError, ValueError) as exc:
        raise UploadPolicyError("invalid_upload_policy", "上传大小必须为整数") from exc
    if not 1 <= max_size_mb <= MAX_SIZE_MB:
        raise UploadPolicyError(
            "invalid_upload_policy", f"上传大小必须为 1-{MAX_SIZE_MB} MB",
        )

    extensions = normalize_extensions(value.get("extensions"))
    defaults = normalize_extensions(definition["default_extensions"])
    if not definition.get("extensions_editable", True) and extensions != defaults:
        raise UploadPolicyError("invalid_upload_policy", "该上传策略的文件格式不可修改")
    allowed = definition.get("allowed_extensions")
    if allowed and not set(extensions).issubset(set(normalize_extensions(allowed))):
        raise UploadPolicyError("invalid_upload_policy", "上传格式超出该策略允许范围")
    return {"max_size_mb": max_size_mb, "extensions": extensions}


def plugin_policy_definition(plugin: dict, item: dict) -> dict:
    """把插件局部声明转成带命名空间的统一声明。"""
    key = str(item.get("key") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
        raise UploadPolicyError("invalid_upload_policy", "插件上传策略 key 无效")
    legacy = item.get("legacy_keys") or {}
    definition = {
        **item,
        "id": f"{plugin['name']}.{key}",
        "owner": plugin["name"],
        "owner_title": plugin.get("title") or plugin["name"],
        "plugin_status": plugin.get("status"),
        "legacy_keys": {
            field: f"{plugin['name']}.{local_key}"
            for field, local_key in legacy.items() if local_key
        },
    }
    validate_policy_value(definition, {
        "max_size_mb": definition.get("default_max_size_mb"),
        "extensions": definition.get("default_extensions"),
    })
    return definition


async def collect_policy_definitions(plugin_manager) -> tuple[list[dict], list[str]]:
    """聚合核心与所有已安装插件的上传策略声明。"""
    from core.plugin_query_service import list_all_plugins

    definitions = [dict(item) for item in CORE_POLICY_DEFINITIONS]
    warnings: list[str] = []
    known_ids = {item["id"] for item in definitions}
    for plugin in await list_all_plugins():
        try:
            schema = plugin_manager.get_upload_policy_schema(plugin["name"])
            for raw in schema:
                definition = plugin_policy_definition(plugin, raw)
                if definition["id"] in known_ids:
                    raise UploadPolicyError(
                        "invalid_upload_policy", f"策略 ID 重复: {definition['id']}",
                    )
                known_ids.add(definition["id"])
                definitions.append(definition)
        except Exception as exc:
            message = f"插件 {plugin['name']} 上传策略声明无效: {exc}"
            logger.warning("[上传策略] %s", message)
            warnings.append(message)
    return definitions, warnings


async def _read_value(db, key: str) -> str | None:
    return (await db.execute(
        select(ConfigurationModel.value).where(ConfigurationModel.key == key)
    )).scalar_one_or_none()


def _parse_payload(raw: str | None) -> dict:
    if not raw:
        return {"schema_version": SCHEMA_VERSION, "policies": {}}
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "policies": {}}
    policies = payload.get("policies") if isinstance(payload, dict) else None
    return {
        "schema_version": SCHEMA_VERSION,
        "policies": policies if isinstance(policies, dict) else {},
    }


async def load_payload(db=None) -> tuple[dict, bool]:
    """读取统一策略原始配置；布尔值表示是否已完成首次保存。"""
    if db is not None:
        raw = await _read_value(db, CONFIG_KEY)
        return _parse_payload(raw), raw is not None
    async with async_session_factory() as session:
        raw = await _read_value(session, CONFIG_KEY)
        return _parse_payload(raw), raw is not None


async def _legacy_policy(db, definition: dict) -> dict:
    value = {
        "max_size_mb": definition["default_max_size_mb"],
        "extensions": definition["default_extensions"],
    }
    keys = definition.get("legacy_keys") or {}
    max_raw = await _read_value(db, keys["max_size_mb"]) if keys.get("max_size_mb") else None
    ext_raw = await _read_value(db, keys["extensions"]) if keys.get("extensions") else None
    if max_raw not in (None, ""):
        value["max_size_mb"] = max_raw
    if ext_raw not in (None, ""):
        value["extensions"] = ext_raw
    try:
        return validate_policy_value(definition, value)
    except UploadPolicyError:
        return validate_policy_value(definition, {
            "max_size_mb": definition["default_max_size_mb"],
            "extensions": definition["default_extensions"],
        })


async def get_effective_policy(definition: dict, db=None) -> dict:
    """按统一值、旧值、默认值优先级读取单项生效策略。"""
    if db is None:
        async with async_session_factory() as session:
            return await get_effective_policy(definition, session)
    payload, migrated = await load_payload(db)
    stored = payload["policies"].get(definition["id"])
    if isinstance(stored, dict):
        try:
            value = validate_policy_value(definition, stored)
            return {**value, "source": "unified", "migrated": migrated}
        except UploadPolicyError:
            pass
    value = await _legacy_policy(db, definition)
    return {**value, "source": "legacy", "migrated": migrated}


async def build_policy_items(definitions: list[dict], db=None) -> tuple[list[dict], bool]:
    """生成设置页需要的完整策略列表。"""
    if db is None:
        async with async_session_factory() as session:
            return await build_policy_items(definitions, session)
    _, migrated = await load_payload(db)
    items = []
    for definition in definitions:
        value = await get_effective_policy(definition, db)
        items.append({
            "id": definition["id"],
            "owner": definition["owner"],
            "owner_title": definition["owner_title"],
            "label": definition["label"],
            "description": definition.get("description", ""),
            "max_size_mb": value["max_size_mb"],
            "extensions": value["extensions"],
            "extensions_editable": bool(definition.get("extensions_editable", True)),
            "plugin_status": definition.get("plugin_status"),
            "source": value["source"],
        })
    return items, migrated


async def _upsert(db, key: str, value: str, description: str) -> None:
    row = (await db.execute(
        select(ConfigurationModel).where(ConfigurationModel.key == key)
    )).scalar_one_or_none()
    if row:
        row.value = value
        if description and not row.description:
            row.description = description
    else:
        db.add(ConfigurationModel(key=key, value=value, description=description))


async def save_policies(definitions: list[dict], values: list[dict]) -> None:
    """事务保存可见策略，并镜像其旧配置键。"""
    definition_map = {item["id"]: item for item in definitions}
    submitted: dict[str, dict] = {}
    for raw in values:
        policy_id = str(raw.get("id") or "")
        definition = definition_map.get(policy_id)
        if not definition:
            raise UploadPolicyError("policy_unavailable", f"上传策略不存在: {policy_id}")
        submitted[policy_id] = validate_policy_value(definition, raw)
    if set(submitted) != set(definition_map):
        raise UploadPolicyError("policy_unavailable", "请提交当前页面显示的全部上传策略")

    async with _save_lock:
        async with async_session_factory() as db:
            plugin_owners = sorted({
                item["owner"] for item in definitions if item["owner"] != "core"
            })
            if plugin_owners:
                from core.db.plugin import PluginModel

                rows = await db.execute(
                    select(PluginModel.name)
                    .where(PluginModel.name.in_(plugin_owners))
                    .with_for_update()
                )
                installed_owners = set(rows.scalars().all())
                unavailable = sorted(set(plugin_owners) - installed_owners)
                if unavailable:
                    raise UploadPolicyError(
                        "policy_unavailable",
                        f"插件上传策略已不可用: {', '.join(unavailable)}",
                    )
            raw = await _read_value(db, CONFIG_KEY)
            payload = _parse_payload(raw)
            payload["policies"].update(submitted)
            await _upsert(
                db, CONFIG_KEY,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "统一文件上传策略 JSON",
            )
            for policy_id, value in submitted.items():
                definition = definition_map[policy_id]
                legacy = definition.get("legacy_keys") or {}
                if legacy.get("max_size_mb"):
                    await _upsert(
                        db, legacy["max_size_mb"], str(value["max_size_mb"]),
                        f"{definition['label']}最大上传大小（MB）",
                    )
                if legacy.get("extensions"):
                    await _upsert(
                        db, legacy["extensions"], ",".join(value["extensions"]),
                        f"{definition['label']}允许格式",
                    )
            await db.commit()


def extension_from_filename(filename: str | None) -> str:
    """读取文件扩展名，返回无点小写值。"""
    return Path(filename or "").suffix.lower().lstrip(".")


def validate_filename(filename: str | None, policy: dict) -> str:
    """校验文件名和扩展名，返回带点扩展名。"""
    extension = extension_from_filename(filename)
    if not filename:
        raise UploadPolicyError("invalid_upload", "文件名不能为空")
    if extension not in policy["extensions"]:
        allowed = ", ".join(policy["extensions"])
        raise UploadPolicyError("invalid_upload", f"不支持的文件格式，允许: {allowed}")
    return f".{extension}"


async def stream_upload(
    file, destination: Path, max_size_mb: int, *, on_chunk=None,
) -> int:
    """分块写入上传文件，空文件、超限或异常时删除半成品。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = max_size_mb * 1024 * 1024
    size = 0
    try:
        with open(destination, "wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadPolicyError(
                        "invalid_upload", f"文件大小超过限制（最大 {max_size_mb}MB）",
                    )
                if on_chunk:
                    on_chunk(chunk)
                output.write(chunk)
        if size == 0:
            raise UploadPolicyError("invalid_upload", "文件内容不能为空")
        return size
    except Exception:
        destination.unlink(missing_ok=True)
        raise
