# -*- coding: utf-8 -*-
"""公共 Skill 归档与 AgentScope 元数据适配。"""
from __future__ import annotations

import asyncio
import io
import json
import re
import tarfile
from pathlib import Path
from typing import AsyncIterator
from agentscope.app.storage import SkillRecord
from fastapi import UploadFile
from pydantic import BaseModel, Field

from core.config import BASE_DIR


SHARED_SKILL_OWNER = "admin:shared"
SHARED_SKILL_DIR = Path(BASE_DIR) / "runtime" / "agentscope-shared-skills"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_FILE_COUNT = 100
CHUNK_SIZE = 64 * 1024


class SkillUploadEntry(BaseModel):
    """浏览器上传清单中的单个文件。"""

    path: str
    size: int = Field(ge=0)


class SkillUploadManifest(BaseModel):
    """公共 Skill 文件夹上传清单。"""

    entries: list[SkillUploadEntry]


def parse_manifest(value: str) -> SkillUploadManifest:
    """解析并校验 JSON 清单。"""
    try:
        return SkillUploadManifest.model_validate_json(value)
    except ValueError as exc:
        raise ValueError("Skill 上传清单格式无效") from exc


def validate_manifest(manifest: SkillUploadManifest, file_count: int) -> str:
    """执行 Skill 文件夹的路径、数量和大小限制，返回根目录名。"""
    if not manifest.entries or len(manifest.entries) != file_count:
        raise ValueError("Skill 上传清单与文件数量不一致")
    if len(manifest.entries) > MAX_FILE_COUNT:
        raise ValueError("Skill 文件数量超过限制")
    roots: set[str] = set()
    total = 0
    for entry in manifest.entries:
        if entry.size > MAX_FILE_BYTES:
            raise ValueError(f"文件 {entry.path!r} 超过单文件大小限制")
        normalized = entry.path.replace("\\", "/")
        parts = normalized.split("/")
        if normalized.startswith("/") or len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"Skill 上传路径不安全：{entry.path!r}")
        roots.add(parts[0])
        total += entry.size
    if len(roots) != 1:
        raise ValueError("一个 Skill 必须只包含一个根目录")
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Skill 文件总大小超过限制")
    root = roots.pop()
    if not any(entry.path.replace("\\", "/") == f"{root}/SKILL.md" for entry in manifest.entries):
        raise ValueError("Skill 根目录必须包含 SKILL.md")
    return root


def archive_path(skill_id: str) -> Path:
    """返回不受用户输入影响的归档路径。"""
    return SHARED_SKILL_DIR / f"{skill_id}.tar"


def _metadata(markdown: str, root: str) -> tuple[str, str]:
    heading = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    display_name = heading.group(1).strip() if heading else root
    description = ""
    for line in markdown.splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            description = value
            break
    return display_name[:128], description[:1000]


async def write_archive(manifest: SkillUploadManifest, files: list[UploadFile], skill_id: str) -> tuple[str, str, Path]:
    """将上传文件流式写入归档，并返回 Skill 名称、Markdown 和路径。"""
    root = validate_manifest(manifest, len(files))
    SHARED_SKILL_DIR.mkdir(parents=True, exist_ok=True)
    target = archive_path(skill_id)
    markdown = bytearray()
    try:
        with tarfile.open(target, mode="w") as archive:
            for entry, upload in zip(manifest.entries, files):
                normalized = entry.path.replace("\\", "/")
                info = tarfile.TarInfo(normalized)
                info.size = entry.size
                info.mtime = 0
                content = bytearray()
                remaining = entry.size
                while remaining:
                    chunk = await upload.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ValueError(f"文件 {entry.path!r} 实际大小与清单不一致")
                    content.extend(chunk)
                    remaining -= len(chunk)
                if await upload.read(1):
                    raise ValueError(f"文件 {entry.path!r} 实际大小与清单不一致")
                archive.addfile(info, io.BytesIO(content))
                if normalized == f"{root}/SKILL.md":
                    markdown.extend(content)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return root, markdown.decode("utf-8", errors="replace"), target


async def archive_stream(path: Path) -> AsyncIterator[bytes]:
    """以异步生成器读取 Skill 归档。"""
    file = await asyncio.to_thread(path.open, "rb")
    try:
        while True:
            chunk = await asyncio.to_thread(file.read, CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        await asyncio.to_thread(file.close)


def record_payload(record: SkillRecord) -> dict:
    """生成公共 Skill 列表响应，不暴露服务器路径和 Markdown 全文。"""
    return {
        "id": record.id,
        "name": record.name,
        "display_name": record.display_name or record.name,
        "description": record.description,
        "tags": list(record.tags),
        "enabled": bool(record.enabled),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def build_record(skill_id: str, name: str, markdown: str) -> SkillRecord:
    display_name, description = _metadata(markdown, name)
    return SkillRecord(
        id=skill_id,
        user_id=SHARED_SKILL_OWNER,
        name=name,
        display_name=display_name,
        description=description,
        markdown=markdown,
        enabled=True,
    )


def encode_manifest(entries: list[dict]) -> str:
    """供测试和非浏览器调用构造上传清单。"""
    return json.dumps({"entries": entries}, ensure_ascii=False)
