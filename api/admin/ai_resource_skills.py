# -*- coding: utf-8 -*-
"""管理员公共 Skill 库接口。"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok
from services.agentscope.runtime import agentscope_runtime
from services.agentscope.shared_skills import (
    SHARED_SKILL_OWNER,
    archive_path,
    archive_stream,
    build_record,
    parse_manifest,
    record_payload,
    validate_manifest,
    write_archive,
)


router = APIRouter(tags=["AgentScope公共Skill"])
_PERM = Depends(require_permission("ai:setting"))


class SkillInstallInput(BaseModel):
    """Skill 安装目标，仅在安装动作中选择 Agent 与 Session。"""

    agent_id: str = Field(min_length=1, max_length=255)
    session_id: str = Field(min_length=1, max_length=255)


async def _admin_user(request: Request, _: None = Depends(check_admin)) -> str:
    return f"admin:{request.state.user_id}"


@router.get("/skills", dependencies=[_PERM])
async def list_shared_skills(_: str = Depends(_admin_user)):
    records = await agentscope_runtime.storage.list_skills(SHARED_SKILL_OWNER)
    return ok({"list": [record_payload(record) for record in records]})


@router.post("/skills/upload", dependencies=[_PERM])
async def upload_shared_skill(
    manifest: str = Form(...),
    files: list[UploadFile] = File(...),
    _: str = Depends(_admin_user),
):
    try:
        parsed = parse_manifest(manifest)
        root = validate_manifest(parsed, len(files))
        if await agentscope_runtime.storage.get_skill_by_name(SHARED_SKILL_OWNER, root):
            raise HTTPException(status_code=409, detail="公共 Skill 名称已存在")
        skill_id = uuid4().hex
        name, markdown, _archive = await write_archive(parsed, files, skill_id)
        record = build_record(skill_id, name, markdown)
        await agentscope_runtime.storage.upsert_skill(SHARED_SKILL_OWNER, record)
    except HTTPException:
        raise
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ok(record_payload(record), msg="公共 Skill 已上传")


@router.delete("/skills/{skill_id}", dependencies=[_PERM])
async def delete_shared_skill(skill_id: str, _: str = Depends(_admin_user)):
    record = await agentscope_runtime.storage.get_skill(SHARED_SKILL_OWNER, skill_id)
    if record is None:
        raise HTTPException(status_code=404, detail="公共 Skill 不存在")
    deleted = await agentscope_runtime.storage.delete_skill(SHARED_SKILL_OWNER, skill_id)
    if deleted:
        archive_path(skill_id).unlink(missing_ok=True)
    return ok(msg="公共 Skill 已删除")


@router.post("/skills/{skill_id}/install", dependencies=[_PERM])
async def install_shared_skill(
    skill_id: str,
    data: SkillInstallInput,
    user_id: str = Depends(_admin_user),
):
    record = await agentscope_runtime.storage.get_skill(SHARED_SKILL_OWNER, skill_id)
    if record is None:
        raise HTTPException(status_code=404, detail="公共 Skill 不存在")
    session = await agentscope_runtime.storage.get_session(user_id, data.agent_id, data.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="目标 Agent/Session 不存在")
    path = archive_path(skill_id)
    if not path.is_file():
        raise HTTPException(status_code=409, detail="公共 Skill 归档不存在，请重新上传")
    workspace = await agentscope_runtime.workspace_manager.get_workspace(
        user_id,
        data.agent_id,
        data.session_id,
        session.config.workspace_id,
    )
    try:
        await workspace.add_skill_archive(
            archive_stream(path),
            "tar",
            record.name,
            agent_id=data.agent_id,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Skill 安装失败：{exc}") from exc
    return ok({"skill_id": skill_id, "agent_id": data.agent_id, "session_id": data.session_id}, msg="Skill 已安装到会话")
