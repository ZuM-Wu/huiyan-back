# -*- coding: utf-8 -*-
"""慧眼平台的 AgentScope Team 管理接口。

AgentScope 2.0.7 将团队管理主要暴露为 Agent 工具，没有单独的 REST
router。本模块补齐后台工作台需要的资源 CRUD，底层仍使用 AgentScope
Storage，团队成员和会话级关联不复制到旧 AI 表。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from agentscope.app.deps import get_current_user_id, get_storage
from agentscope.app.storage import StorageBase, TeamData, TeamMember, TeamRecord


class TeamCreateRequest(BaseModel):
    """创建团队请求。"""

    name: str = Field(min_length=1, max_length=255, description="团队名称")
    description: str = Field(default="", max_length=2000, description="团队说明")
    session_id: str = Field(min_length=1, max_length=255, description="领导会话标识")
    members: list[TeamMember] = Field(default_factory=list, description="团队成员")


class TeamUpdateRequest(BaseModel):
    """更新团队请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    members: list[TeamMember] | None = None


class TeamListResponse(BaseModel):
    """团队列表响应。"""

    teams: list[TeamRecord]
    total: int


team_router = APIRouter(prefix="/team", tags=["team"])


async def _require_leader_session(
    storage: StorageBase,
    user_id: str,
    session_id: str,
) -> None:
    """校验团队领导会话确实属于当前用户。"""
    session = await storage.get_session(user_id, "", session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="领导会话不存在或不属于当前用户",
        )
    if session.team_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该会话已经属于一个团队，请先解散原团队",
        )


async def _validate_members(
    storage: StorageBase,
    user_id: str,
    leader_session_id: str,
    members: list[TeamMember],
    team_id: str | None = None,
) -> None:
    """校验成员归属和会话占用状态，防止跨用户或跨团队挂载。"""
    seen: set[tuple[str, str]] = set()
    for member in members:
        if member.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="团队成员必须属于当前用户",
            )
        if member.session_id == leader_session_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="领导会话不能重复作为团队成员",
            )
        key = (member.agent_id, member.session_id)
        if key in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="团队成员不能重复",
            )
        seen.add(key)
        session = await storage.get_session(
            user_id,
            member.agent_id,
            member.session_id,
        )
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"成员会话不存在：{member.session_id}",
            )
        if session.team_id is not None and session.team_id != team_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"成员会话已属于其他团队：{member.session_id}",
            )


@team_router.get("/", response_model=TeamListResponse)
async def list_teams(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> TeamListResponse:
    """列出当前用户拥有的全部团队。"""
    teams = await storage.list_teams(user_id)
    return TeamListResponse(teams=teams, total=len(teams))


@team_router.post("/", response_model=TeamRecord, status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamCreateRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> TeamRecord:
    """创建团队并把领导会话关联到团队。"""
    await _require_leader_session(storage, user_id, body.session_id)
    await _validate_members(storage, user_id, body.session_id, body.members)
    record = TeamRecord(
        user_id=user_id,
        session_id=body.session_id,
        data=TeamData(
            name=body.name,
            description=body.description,
            members=body.members,
            member_ids=[member.agent_id for member in body.members],
        ),
    )
    await storage.upsert_team(user_id, record)
    await storage.set_session_team_id(user_id, body.session_id, record.id)
    for member in body.members:
        await storage.set_session_team_id(user_id, member.session_id, record.id)
    return record


@team_router.patch("/{team_id}", response_model=TeamRecord)
async def update_team(
    team_id: str,
    body: TeamUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> TeamRecord:
    """更新团队名称、说明或成员列表。"""
    existing = await storage.get_team(user_id, team_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="团队不存在")
    updates = body.model_dump(exclude_none=True)
    members = body.members
    data_updates = updates
    if members is not None:
        await _validate_members(
            storage,
            user_id,
            existing.session_id,
            members,
            team_id=team_id,
        )
        old_members = {
            (member.agent_id, member.session_id): member
            for member in existing.data.members
        }
        new_members = {
            (member.agent_id, member.session_id): member
            for member in members
        }
        for key in old_members.keys() - new_members.keys():
            await storage.set_session_team_id(user_id, key[1], None)
        for key in new_members.keys() - old_members.keys():
            await storage.set_session_team_id(user_id, key[1], team_id)
        # model_copy 不会重新校验嵌套字段，保留已通过 Pydantic 校验的
        # TeamMember 实例，避免响应记录中的 members 退化为字典。
        data_updates["members"] = members
        data_updates["member_ids"] = [member.agent_id for member in members]
    updated = existing.model_copy(
        update={
            "data": existing.data.model_copy(update=data_updates),
        },
    )
    await storage.upsert_team(user_id, updated)
    return updated


@team_router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """删除团队并执行 AgentScope 的成员级联清理。"""
    if not await storage.delete_team(user_id, team_id):
        raise HTTPException(status_code=404, detail="团队不存在")


__all__ = ["team_router"]
