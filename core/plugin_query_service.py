# -*- coding: utf-8 -*-
"""插件查询服务

封装对 hy_plugin 表的查询与状态更新操作，供 api 层和插件层调用。
所有函数返回纯 dict/list，不返回 ORM 实例。

使用方式:
    from core.plugin_query_service import list_plugins, get_plugin_by_name
"""
import logging
from typing import Any, Optional, cast

from sqlalchemy import select, func, update
from sqlalchemy.engine import CursorResult

from core.db.base import async_session_factory
from core.db.plugin import PluginModel

logger = logging.getLogger(__name__)

# 插件类型中文映射（与 api/admin/plugin.py 保持一致）
MODULE_LABELS = {
    "addon": "插件", "server": "模块", "gateway": "支付",
    "sms": "短信", "mail": "邮件", "captcha": "验证码",
    "certification": "实名", "oauth": "第三方登录",
    "oss": "存储", "widget": "挂件", "weather": "天气",
}


def _plugin_to_dict(p: PluginModel) -> dict:
    """将 PluginModel ORM 实例转为纯字典"""
    return {
        "id": p.id,
        "name": p.name,
        "title": p.title,
        "version": p.version,
        "author": p.author or "",
        "module": p.module,
        "module_label": MODULE_LABELS.get(p.module, p.module),
        "status": p.status,
        "config": p.config or "",
        "install_time": str(p.install_time) if p.install_time else None,
        "update_time": str(p.update_time) if p.update_time else None,
    }


async def list_plugins(
    page: int = 1,
    limit: int = 20,
    keywords: str = "",
) -> dict:
    """插件分页列表

    参数:
        page: 页码（从 1 开始）
        limit: 每页条数
        keywords: 关键词（按 name/title 模糊搜索）
    返回:
        {"total": int, "page": int, "limit": int, "list": [dict, ...]}
    """
    async with async_session_factory() as db:
        query = select(PluginModel)
        count_query = select(func.count(PluginModel.id))

        if keywords:
            kw = f"%{keywords}%"
            query = query.where(
                PluginModel.name.like(kw) | PluginModel.title.like(kw)
            )
            count_query = count_query.where(
                PluginModel.name.like(kw) | PluginModel.title.like(kw)
            )

        total = (await db.execute(count_query)).scalar() or 0

        query = (
            query.order_by(PluginModel.install_time.asc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        plugins = (await db.execute(query)).scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": [_plugin_to_dict(p) for p in plugins],
    }


async def get_plugin_by_name(name: str) -> Optional[dict]:
    """按插件唯一标识（name）查询插件

    参数:
        name: 插件唯一标识（snake_case）
    返回:
        插件信息字典，不存在则返回 None
    """
    async with async_session_factory() as db:
        plugin = (await db.execute(
            select(PluginModel).where(PluginModel.name == name)
        )).scalar_one_or_none()
        if not plugin:
            return None
        return _plugin_to_dict(plugin)


async def get_plugin_by_module(module: str) -> Optional[dict]:
    """按模块类型查询第一个匹配的插件

    参数:
        module: 插件类型标识（addon/gateway/sms/mail/...）
    返回:
        插件信息字典，不存在则返回 None
    """
    async with async_session_factory() as db:
        plugin = (await db.execute(
            select(PluginModel)
            .where(PluginModel.module == module)
            .order_by(PluginModel.id)
            .limit(1)
        )).scalar_one_or_none()
        if not plugin:
            return None
        return _plugin_to_dict(plugin)


async def list_all_plugins() -> list:
    """全量插件列表（无分页），按安装时间升序排列

    返回:
        [dict, ...]
    """
    async with async_session_factory() as db:
        plugins = (await db.execute(
            select(PluginModel).order_by(PluginModel.install_time.asc())
        )).scalars().all()
    return [_plugin_to_dict(p) for p in plugins]


async def update_plugin_status(name: str, enabled: bool) -> bool:
    """更新插件启用状态

    参数:
        name: 插件唯一标识
        enabled: True=启用(status=1), False=禁用(status=2)
    返回:
        更新成功返回 True，插件不存在返回 False
    """
    new_status = 1 if enabled else 2
    async with async_session_factory() as db:
        result = cast(CursorResult[Any], await db.execute(
            update(PluginModel)
            .where(PluginModel.name == name)
            .values(status=new_status)
        ))
        await db.commit()
        updated = result.rowcount or 0
    if updated:
        logger.info(
            f"[PluginQueryService] 插件状态已更新: {name} -> "
            f"{'启用' if enabled else '禁用'}"
        )
    return updated > 0


async def list_plugins_by_module(module: str, status: int = 1) -> list[dict]:
    """按模块名查询启用中的插件列表（供 admin_notifier 等插件配置页面）

    参数:
        module: 插件模块名（如 'mail'/'sms'/'oss'）
        status: 插件状态，默认 1=启用
    返回:
        [{name, title, module}]
    """
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(PluginModel.name, PluginModel.title, PluginModel.module).where(
                PluginModel.module == module,
                PluginModel.status == status,
            )
        )).all()
    return [
        {"name": r[0], "title": r[1], "module": r[2]}
        for r in rows
    ]


# ========== 通知接口管理扩展 ==========

async def list_plugins_by_names(names: list[str]) -> list[dict]:
    """按 name 列表批量查询插件

    参数:
        names: 插件唯一标识列表
    返回:
        [dict, ...]
    """
    if not names:
        return []
    async with async_session_factory() as db:
        result = await db.execute(
            select(PluginModel).where(PluginModel.name.in_(names))
        )
        return [_plugin_to_dict(p) for p in result.scalars().all()]


async def list_orphan_plugins(
    module: str | None, exclude_names: list[str]
) -> list[dict]:
    """查询已安装但不在 exclude_names 中的插件（孤儿插件）

    参数:
        module:        模块筛选，None 则筛选 sms+mail
        exclude_names: 需排除的插件 name 列表
    返回:
        [dict, ...]
    """
    async with async_session_factory() as db:
        if module:
            q = select(PluginModel).where(PluginModel.module == module)
        else:
            q = select(PluginModel).where(PluginModel.module.in_(("sms", "mail")))
        result = await db.execute(q)
        all_plugins = result.scalars().all()
        orphans = [p for p in all_plugins if p.name not in exclude_names]
        return [_plugin_to_dict(p) for p in orphans]


async def get_plugin_config_map(plugin_names: list[str]) -> dict:
    """批量获取多个插件的配置字典

    参数:
        plugin_names: 插件 name 列表
    返回:
        {plugin_name: {key: value, ...}, ...}
    """
    from core.config_manager import ConfigManager
    async with async_session_factory() as db:
        cm = ConfigManager()
        configs = {}
        for name in plugin_names:
            configs[name] = await cm.get_plugin_config(name, db)
        return configs


async def get_single_plugin_config(plugin_name: str) -> dict:
    """获取单个插件的配置字典

    参数:
        plugin_name: 插件 name
    返回:
        {key: value, ...}
    """
    from core.config_manager import ConfigManager
    async with async_session_factory() as db:
        cm = ConfigManager()
        return await cm.get_plugin_config(plugin_name, db)


async def save_plugin_config_and_maybe_enable(
    plugin_name: str, config: dict, pm, router_mgr
) -> None:
    """保存插件配置，若插件当前为禁用状态则自动启用

    参数:
        plugin_name: 插件 name
        config:      配置字典
        pm:          PluginManager 实例
        router_mgr:  RouterManager 实例
    """
    from core.config_manager import ConfigManager
    async with async_session_factory() as db:
        cm = ConfigManager()
        for key, value in config.items():
            full_key = f"{plugin_name}.{key}"
            await cm.set(full_key, str(value), db, description=f"通知插件 {plugin_name} 配置")
        record = (await db.execute(
            select(PluginModel).where(PluginModel.name == plugin_name)
        )).scalar_one_or_none()
        if record and record.status == 2:
            await pm.enable(plugin_name, db, router_manager=router_mgr)


async def toggle_plugin_with_pm(
    name: str, new_status: int, pm, router_mgr
) -> None:
    """通过 PluginManager 启用/禁用插件，或裸 UPDATE 设置 status=0

    参数:
        name:       插件 name
        new_status: 目标状态 (0=已安装未启用 1=已启用 2=已禁用)
        pm:         PluginManager 实例
        router_mgr: RouterManager 实例
    """
    async with async_session_factory() as db:
        if new_status == 1:
            await pm.enable(name, db, router_manager=router_mgr)
        elif new_status == 2:
            await pm.disable(name, db, router_manager=router_mgr)
        else:
            await db.execute(
                update(PluginModel).where(PluginModel.name == name).values(status=new_status)
            )
            await db.commit()
