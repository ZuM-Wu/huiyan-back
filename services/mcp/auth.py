"""
MCP 个人 API Key 鉴权

Bearer Token 校验流程:
1. 仅对无效 Key 使用有容量上限的进程内负缓存（60 秒，避免重复刷库）
2. sha256(token) 查 hy_api_key（status=1 启用中）
3. 按 user_type 校验所属 Admin/Farmer 账户 status=1
4. 管理员经 RBAC 取权限 code 列表作为 scopes（沿用其 2 小时缓存，超管 id=1 标记 is_super）
5. 回写 last_used_time

有效 Key 不缓存，因此账户禁用、Key 吊销和 RBAC 变更在下一次鉴权立即生效。
"""
import hashlib
import logging
import time
from collections import OrderedDict
from core.time_utils import china_now
from typing import Optional

from sqlalchemy import select, update
from fastmcp.server.auth import AccessToken, TokenVerifier

from core.db.base import async_session_factory
from core.db.api_key import ApiKeyModel

logger = logging.getLogger(__name__)

# 超级管理员固定 ID（与 core/auth/rbac.py 的放行规则一致）
SUPER_ADMIN_ID = 1
# 无效 Key 负缓存参数；有效 Key 不缓存，确保账号/RBAC 变更立即生效。
NEGATIVE_CACHE_TTL = 60
NEGATIVE_CACHE_MAX_SIZE = 1024

# key_hash -> 过期时间戳；仅缓存无效 Key，OrderedDict 用于容量淘汰。
_key_cache: OrderedDict[str, float] = OrderedDict()


def hash_key(plain: str) -> str:
    """计算 Key 明文的 sha256 十六进制哈希（数据库仅存哈希）"""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def invalidate_key_cache(key_hash: Optional[str] = None):
    """
    失效 Key 负缓存（吊销接口直调，单进程内即时生效）

    :param key_hash: 指定则只失效该条；为 None 则清空全部缓存
    """
    if key_hash is None:
        _key_cache.clear()
    else:
        _key_cache.pop(key_hash, None)


def _cache_get(key_hash: str) -> Optional[float]:
    """读取无效 Key 负缓存，过期则删除并返回 None。"""
    entry = _key_cache.get(key_hash)
    if entry is None:
        return None
    if time.time() > entry:
        _key_cache.pop(key_hash, None)
        return None
    _key_cache.move_to_end(key_hash)
    return entry


def _cache_put_negative(key_hash: str) -> None:
    """写入无效 Key 负缓存并淘汰最旧记录。"""
    _key_cache[key_hash] = time.time() + NEGATIVE_CACHE_TTL
    _key_cache.move_to_end(key_hash)
    while len(_key_cache) > NEGATIVE_CACHE_MAX_SIZE:
        _key_cache.popitem(last=False)


async def _check_account_active(db, user_type: str, user_id: int) -> bool:
    """校验 Key 所属账户当前是否启用（禁用账户的 Key 立即失效）"""
    if user_type == "admin":
        from core.db.admin import Admin
        row = (await db.execute(
            select(Admin.status).where(Admin.id == user_id)
        )).scalar_one_or_none()
    elif user_type == "farmer":
        from core.db.farmer import Farmer
        row = (await db.execute(
            select(Farmer.status).where(Farmer.id == user_id)
        )).scalar_one_or_none()
    else:
        return False
    return row == 1


class ApiKeyVerifier(TokenVerifier):
    """
    个人 API Key 校验器（FastMCP auth 组件）

    返回的 AccessToken:
    - client_id: "admin:3" / "farmer:7" 形式的身份标识
    - scopes:    管理员为 RBAC 权限 code 列表；农户为空列表（按 audience 过滤）
    - claims:    {"user_type", "user_id", "is_super"}，供权限中间件与工具 handler 使用
    """

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        key_hash = hash_key(token)

        # 1. 仅命中无效 Key 负缓存；有效 Key 每次重新查库。
        if _cache_get(key_hash) is not None:
            return None

        access = await self._verify_from_db(token, key_hash)
        if access is None:
            _cache_put_negative(key_hash)
        return access

    async def _verify_from_db(self, token: str, key_hash: str) -> Optional[AccessToken]:
        """查库校验 Key 与账户状态，构造 AccessToken"""
        try:
            async with async_session_factory() as db:
                key_row = (await db.execute(
                    select(ApiKeyModel).where(
                        ApiKeyModel.key_hash == key_hash,
                        ApiKeyModel.status == 1,
                    )
                )).scalar_one_or_none()
                if key_row is None:
                    return None

                # 所属账户被禁用则 Key 同步失效
                if not await _check_account_active(db, key_row.user_type, key_row.user_id):
                    return None

                # 管理员：取 RBAC 权限 code 列表作为 scopes（沿用 2 小时缓存）
                scopes: list[str] = []
                is_super = False
                if key_row.user_type == "admin":
                    from core.auth.rbac import get_admin_permissions
                    is_super = key_row.user_id == SUPER_ADMIN_ID
                    scopes = await get_admin_permissions(
                        key_row.user_id, db, use_cache=False
                    )

                # 记录本次鉴权使用时间；有效 Key 不缓存，因此每次都会更新。
                await db.execute(
                    update(ApiKeyModel)
                    .where(ApiKeyModel.id == key_row.id)
                    .values(last_used_time=china_now())
                )
                await db.commit()

                return AccessToken(
                    token=token,
                    client_id=f"{key_row.user_type}:{key_row.user_id}",
                    scopes=scopes,
                    claims={
                        "user_type": key_row.user_type,
                        "user_id": key_row.user_id,
                        "is_super": is_super,
                    },
                )
        except Exception:
            logger.exception("[MCP] API Key 校验异常")
            return None


async def verify_api_key_access(token: str) -> Optional[AccessToken]:
    """按系统 MCP 网络端点的同一规则校验个人 API Key。

    该函数是供系统内受信任调用方使用的公共鉴权门面。它复用
    :class:`ApiKeyVerifier`，因此密钥吊销、账户状态和管理员 RBAC 权限均在
    每次调用时重新校验，避免插件复制一套容易漂移的鉴权逻辑。
    """
    if not token:
        return None
    return await ApiKeyVerifier().verify_token(token)
