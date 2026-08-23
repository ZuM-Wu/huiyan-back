"""API Key 管理服务层

封装对 hy_api_key 表的查询与操作，供 api 层调用。
所有函数返回纯 dict/list，不返回 ORM 实例。

设计要点:
- 鉴权热路径按 sha256 哈希（key_hash）查找，明文 key_plain 仅用于列表展示
- 创建时生成 "hy_" + secrets.token_urlsafe(32) 格式的密钥
- 吊销时设置 status=2 并失效鉴权缓存，单进程内即时生效
- permissions 参数当前模型未存储（密钥继承用户已有 RBAC/角色权限）
"""
import hashlib
import logging
import secrets
from typing import Optional

from sqlalchemy import select, func

from core.db.base import async_session_factory
from core.db.api_key import ApiKeyModel

logger = logging.getLogger(__name__)


def _hash_key(plain: str) -> str:
    """计算密钥明文的 sha256 十六进制哈希（数据库仅存哈希）"""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _row_to_dict(row: ApiKeyModel) -> dict:
    """将 ORM 行转换为输出字典（含明文 key 供可视化展示，不含哈希）"""
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "key": row.key_plain or "",
        "status": row.status,
        "last_used_time": str(row.last_used_time) if row.last_used_time else "",
        "create_time": str(row.create_time) if row.create_time else "",
    }


async def list_api_keys(
    owner_type: str, owner_id: int, page: int = 1, limit: int = 20
) -> dict:
    """获取指定用户的 API Key 列表（分页）

    Args:
        owner_type: 用户类型（admin=管理员, farmer=农户）
        owner_id:   用户 ID
        page:       页码（从 1 开始）
        limit:      每页条数

    Returns:
        {"total": int, "page": int, "limit": int, "list": [...]}
    """
    async with async_session_factory() as db:
        base_q = select(ApiKeyModel).where(
            ApiKeyModel.user_type == owner_type,
            ApiKeyModel.user_id == owner_id,
        )
        # 统计总数
        total = (
            await db.execute(select(func.count()).select_from(base_q.subquery()))
        ).scalar() or 0
        # 分页查询（按 ID 倒序）
        rows = (
            await db.execute(
                base_q.order_by(ApiKeyModel.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        ).scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": [_row_to_dict(r) for r in rows],
    }


async def create_api_key(
    owner_type: str, owner_id: int, name: str, permissions: str = ""
) -> dict:
    """创建 API Key（明文落库，返回 key 明文）

    密钥格式为 "hy_" + secrets.token_urlsafe(32)，同时存储 sha256 哈希和明文。
    permissions 参数当前模型未存储（密钥继承用户已有 RBAC/角色权限），
    保留此参数以兼容未来扩展。

    Args:
        owner_type:   用户类型（admin=管理员, farmer=农户）
        owner_id:     用户 ID
        name:         密钥备注名
        permissions:  权限描述（当前未持久化，预留扩展）

    Returns:
        {"id": int, "key": str, "prefix": str, "name": str}
    """
    plain = "hy_" + secrets.token_urlsafe(32)
    key_hash = _hash_key(plain)
    async with async_session_factory() as db:
        row = ApiKeyModel(
            user_type=owner_type,
            user_id=owner_id,
            name=name,
            key_hash=key_hash,
            key_plain=plain,
            prefix=plain[:8],
            status=1,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

    return {
        "id": row.id,
        "key": plain,
        "prefix": row.prefix,
        "name": row.name,
    }


async def revoke_api_key(key_id: int, owner_type: str, owner_id: int) -> bool:
    """吊销 API Key（status=2）并即时失效鉴权缓存

    仅允许密钥所属用户吊销本人的密钥（user_type + user_id 双重校验）。

    Args:
        key_id:      密钥 ID
        owner_type:  用户类型（admin=管理员, farmer=农户）
        owner_id:    用户 ID

    Returns:
        True=吊销成功（含已处于吊销状态）, False=密钥不存在或无权操作
    """
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(ApiKeyModel).where(
                    ApiKeyModel.id == key_id,
                    ApiKeyModel.user_type == owner_type,
                    ApiKeyModel.user_id == owner_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        # 已处于吊销状态，视为成功
        if row.status == 2:
            return True
        row.status = 2
        key_hash = row.key_hash
        await db.commit()

    # 失效鉴权缓存（单进程内即时生效）
    try:
        from services.mcp.auth import invalidate_key_cache

        invalidate_key_cache(key_hash)
    except Exception:
        logger.warning("失效 API Key 鉴权缓存失败", exc_info=True)
    return True


async def get_api_key_by_plain(plain_key: str) -> Optional[dict]:
    """按明文 Key 查询（鉴权用）

    对明文 key 取 sha256 哈希后查库，仅返回启用中（status=1）的密钥信息。

    Args:
        plain_key: 明文密钥

    Returns:
        密钥信息字典（含 user_type, user_id 等鉴权字段），不存在或已吊销则返回 None
    """
    key_hash = _hash_key(plain_key)
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(ApiKeyModel).where(
                    ApiKeyModel.key_hash == key_hash,
                    ApiKeyModel.status == 1,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None

    return {
        "id": row.id,
        "user_type": row.user_type,
        "user_id": row.user_id,
        "name": row.name,
        "prefix": row.prefix,
        "status": row.status,
        "last_used_time": str(row.last_used_time) if row.last_used_time else "",
        "create_time": str(row.create_time) if row.create_time else "",
    }
