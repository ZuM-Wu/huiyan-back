"""
MCP 核心工具共享助手

供 tools_core / tools_area / tools_weather / tools_admin 及各操作工具模块复用：
- _current_claims()       读取当前调用者身份 claims
- _farmer_area_bound()    校验农户是否绑定指定产区
- ensure_farmer_bound()   farmer 身份强制产区绑定校验（admin 放行）
- _clamp_limit()          列表条数钳位（限量返回约定，各列表工具共用）
- admin_identity()        管理员归因身份反查（操作类工具审计字段用）
- validate_payload()      Pydantic Schema 校验（写操作与后台同等校验入口）

独立成模块的原因: tools_core.py 聚合各模块的工具声明列表，
若助手仍留在 tools_core 会形成循环 import。
"""
from pydantic import ValidationError
from sqlalchemy import select
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token

from core.db.base import async_session_factory
from core.db.admin import Admin
from core.db.production_area import AreaFarmer

# 单次返回条数上限（限量返回约定，farmer_list / task_pending 等列表工具共用）
LIST_MAX_LIMIT = 100


def _clamp_limit(limit: int) -> int:
    """将 limit 钳位到 [1, 100] 区间"""
    return max(1, min(int(limit), LIST_MAX_LIMIT))


def _current_claims() -> dict:
    """读取当前请求的身份 claims（鉴权层保证存在，缺失视为异常）"""
    token = get_access_token()
    if token is None or not token.claims:
        raise ToolError("未获取到调用者身份")
    return token.claims


async def _farmer_area_bound(db, farmer_id: int, area_id: int) -> bool:
    """校验农户是否绑定了指定产区（绑定唯一来源: hy_area_farmer）"""
    row = (await db.execute(
        select(AreaFarmer.id).where(
            AreaFarmer.farmer_id == farmer_id,
            AreaFarmer.area_id == area_id,
        ).limit(1)
    )).scalar_one_or_none()
    return row is not None


async def ensure_farmer_bound(claims: dict, area_id: int):
    """farmer 身份强制校验产区绑定，未绑定抛 ToolError；admin 直接放行

    :param claims: 调用者身份 claims（含 user_type / user_id）
    :param area_id: 目标产区ID
    """
    if claims.get("user_type") != "farmer":
        return
    async with async_session_factory() as db:
        if not await _farmer_area_bound(db, claims["user_id"], area_id):
            raise ToolError("您未绑定该产区，无权查询其数据")


async def admin_identity(claims: dict) -> tuple[int, str]:
    """按 claims 反查管理员归因身份（重试/标记等操作工具写处理人字段用）

    MCP 密钥 claims 仅含 user_type/user_id/is_super（见 auth.py），无用户名，
    故按 user_id 查 Admin 表补齐用户名；查不到回退 "MCP密钥" 占位，
    保证审计字段始终可读。

    :param claims: 调用者身份 claims（含 user_id）
    :return: (admin_id, admin_name) 二元组
    """
    admin_id = int(claims.get("user_id") or 0)
    async with async_session_factory() as db:
        username = (await db.execute(
            select(Admin.username).where(Admin.id == admin_id)
        )).scalar_one_or_none()
    return admin_id, username or "MCP密钥"


def validate_payload(schema_cls, payload: dict):
    """用 Pydantic Schema 校验工具参数（写操作与后台接口同等校验的唯一入口）

    :param schema_cls: schemas 包下的请求体模型类（如 AreaCreate）
    :param payload: 工具入参组装的字典
    :return: 校验通过的模型实例
    :raises ToolError: 校验失败时抛中文错误（MCP 客户端可见）
    """
    try:
        return schema_cls(**payload)
    except ValidationError as e:
        first = e.errors()[0]
        field = ".".join(str(loc) for loc in first.get("loc", ())) or "参数"
        raise ToolError(f"参数校验失败: {field} {first.get('msg', '不合法')}") from e
