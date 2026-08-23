"""系统配置服务层

封装对 hy_configuration 表的查询与操作，供 api 层调用。
所有函数返回纯 dict/list，不返回 ORM 实例。

设计要点:
- KV 键值对存储，set_config 支持 UPSERT 语义（存在则更新，不存在则创建）
- list_configs 支持按关键词搜索 key 和 description 字段
- list_site_config 批量读取公开站点品牌配置（白名单字段）
- update_site_config 批量更新官网配置（nav/footer/banner/intro）
  将 JSON 值序列化为紧凑字符串后写入 hy_configuration
- get_maintenance_status 读取维护模式开关（site_maintenance 配置项）
"""
import json
import logging
from typing import Optional

from sqlalchemy import select, func, or_

from core.db.base import async_session_factory
from core.db.configuration import ConfigurationModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 公开站点品牌字段白名单（仅用于登录页/前台展示，不含任何敏感配置）
# ---------------------------------------------------------------------------
_PUBLIC_SITE_KEYS = [
    "site_name", "site_subtitle", "site_logo", "site_favicon",
    "login_bg", "site_logo_url", "site_logo_target",
    "copyright", "record_number",
    # 农户注册配置（供登录页判断是否显示注册入口及必填字段）
    "allow_farmer_register", "farmer_register_phone_required",
    "farmer_register_email_required",
    # 农户协议链接（供登录/注册页展示）
    "farmer_service_agreement_url", "farmer_privacy_policy_url",
    # 验证码登录 / 注册方式控制（供登录页动态控制 UI 展示）
    "farmer_allow_phone_register", "farmer_phone_register_verify",
    "farmer_phone_password_login", "farmer_phone_sms_login",
    "farmer_allow_email_register", "farmer_email_register_verify",
    "farmer_email_password_login", "farmer_email_code_login",
    "farmer_show_register_switch",
    "farmer_default_login_method", "farmer_default_password_type",
]

# ---------------------------------------------------------------------------
# 官网配置字段定义（key -> {default, desc, kind}）
# kind: list=JSON数组, dict=JSON对象, text=纯文本
# ---------------------------------------------------------------------------
_SITE_FIELDS = {
    "nav":    {"default": "[]", "desc": "官网导航菜单（JSON 数组）",   "kind": "list"},
    "footer": {"default": "[]", "desc": "官网页脚分组（JSON 数组）",   "kind": "list"},
    "banner": {"default": "{}", "desc": "官网 Banner（JSON 对象）",    "kind": "dict"},
    "intro":  {"default": "",   "desc": "官网功能简介文案（纯文本）",   "kind": "text"},
}


def _row_to_dict(row: ConfigurationModel) -> dict:
    """将配置 ORM 行转换为输出字典"""
    return {
        "id": row.id,
        "key": row.key,
        "value": row.value,
        "description": row.description,
        "group": row.group_name or "basic",
    }


def _normalize_site_value(value, kind: str) -> str:
    """规范化站点配置值并序列化为字符串

    Args:
        value: 输入值（list/dict/str/None）
        kind:  值类型（list/dict/text）

    Returns:
        序列化后的字符串

    Raises:
        ValueError: 值类型不匹配或 JSON 解析失败
    """
    if kind == "text":
        return "" if value is None else str(value)

    # list / dict：序列化为紧凑 JSON 字符串
    if value in (None, "", [], {}):
        return "[]" if kind == "list" else "{}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str):
        # 字符串也尝试解析以校验 JSON 合法性
        parsed = json.loads(value)
        if kind == "list" and not isinstance(parsed, list):
            raise ValueError("期望 JSON 数组")
        if kind == "dict" and not isinstance(parsed, dict):
            raise ValueError("期望 JSON 对象")
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

    raise ValueError(f"不支持的值类型: {type(value).__name__}")


async def list_configs(page: int = 1, limit: int = 20, keywords: str = "") -> dict:
    """获取配置分页列表

    Args:
        page:     页码（从 1 开始）
        limit:    每页条数
        keywords: 搜索关键词（匹配 key 或 description 字段）

    Returns:
        {"total": int, "page": int, "limit": int, "list": [...]}
    """
    async with async_session_factory() as db:
        query = select(ConfigurationModel)
        # 关键词搜索（同时匹配 key 和 description）
        if keywords:
            query = query.where(
                or_(
                    ConfigurationModel.key.contains(keywords),
                    ConfigurationModel.description.contains(keywords),
                )
            )

        # 统计总数
        total = (
            await db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar() or 0

        # 分页查询（按 ID 正序）
        rows = (
            await db.execute(
                query.order_by(ConfigurationModel.id)
                .offset((page - 1) * limit)
                .limit(limit)
            )
        ).scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": [_row_to_dict(c) for c in rows],
    }


async def get_config(key: str) -> Optional[str]:
    """获取单个配置值

    Args:
        key: 配置键

    Returns:
        配置值字符串，不存在则返回 None
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(ConfigurationModel.value).where(ConfigurationModel.key == key)
        )
    return result.scalar_one_or_none()


async def set_config(key: str, value: str) -> bool:
    """设置配置值（UPSERT 语义：存在则更新，不存在则创建）

    Args:
        key:   配置键
        value: 配置值

    Returns:
        True=设置成功
    """
    async with async_session_factory() as db:
        existing = (
            await db.execute(
                select(ConfigurationModel).where(ConfigurationModel.key == key)
            )
        ).scalar_one_or_none()
        if existing:
            existing.value = str(value)
        else:
            db.add(ConfigurationModel(key=key, value=str(value), description=""))
        from core.events import event_bus
        await event_bus.publish_durable(
            "config.changed", {"key": key, "value": str(value)}, db,
        )
        await db.commit()
    return True


async def delete_config(key: str) -> bool:
    """删除配置项

    Args:
        key: 配置键

    Returns:
        True=删除成功, False=配置不存在
    """
    async with async_session_factory() as db:
        existing = (
            await db.execute(
                select(ConfigurationModel).where(ConfigurationModel.key == key)
            )
        ).scalar_one_or_none()
        if not existing:
            return False
        await db.delete(existing)
        await db.commit()
    return True


async def list_site_config() -> dict:
    """获取公开站点品牌配置（多 key 批量读取）

    从白名单字段中批量读取配置项，仅暴露安全的展示字段，
    供登录页/前台使用。

    Returns:
        {"total": int, "list": [{"key": str, "value": str}, ...]}
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(ConfigurationModel).where(
                ConfigurationModel.key.in_(_PUBLIC_SITE_KEYS)
            )
        )
        configs = result.scalars().all()

    return {
        "total": len(configs),
        "list": [{"key": c.key, "value": c.value} for c in configs],
    }


async def update_site_config(data: dict) -> bool:
    """批量更新站点配置（nav/footer/banner/intro）

    将传入的 JSON 值序列化为紧凑字符串后写入 hy_configuration，
    键名自动加 "site_" 前缀（如 nav → site_nav）。
    支持 UPSERT 语义：键不存在时自动创建。

    Args:
        data: {"nav": [...], "footer": [...], "banner": {...}, "intro": "..."}

    Returns:
        True=更新成功
    """
    async with async_session_factory() as db:
        for field_key, meta in _SITE_FIELDS.items():
            if field_key not in data:
                continue
            # 规范化值并序列化
            value = _normalize_site_value(data[field_key], meta["kind"])
            config_key = f"site_{field_key}"

            existing = (
                await db.execute(
                    select(ConfigurationModel).where(
                        ConfigurationModel.key == config_key
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.value = value
            else:
                db.add(ConfigurationModel(
                    key=config_key, value=value, description=meta["desc"]
                ))
        await db.commit()
    return True


async def get_maintenance_status() -> bool:
    """获取系统维护模式状态

    从 hy_configuration 表读取 site_maintenance 配置项，
    值为 "1" 时表示维护中。

    Returns:
        True=维护中, False=正常
    """
    value = await get_config("site_maintenance")
    return value == "1"
