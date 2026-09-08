"""
配置管理器
提供 KV 配置的读写接口，支持插件配置前缀查询
"""

import logging
from collections.abc import Iterable
from typing import Any, Optional

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.configuration import ConfigurationModel
from core.events import event_bus

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    配置管理器
    负责 KV 配置的读写
    """

    async def get(self, key: str, db: AsyncSession) -> Optional[str]:
        """
        读取单个配置值

        参数:
            key: 配置键
            db: 数据库会话
        返回: 配置值字符串，不存在则返回 None
        """
        result = await db.execute(
            select(ConfigurationModel.value).where(ConfigurationModel.key == key)
        )
        row = result.scalar_one_or_none()
        return row

    async def set(self, key: str, value: str, db: AsyncSession, description: str = "") -> None:
        """
        写入或更新配置值

        参数:
            key: 配置键
            value: 配置值
            db: 数据库会话
            description: 配置说明（可选）
        """
        result = await db.execute(
            select(ConfigurationModel).where(ConfigurationModel.key == key)
        )
        record = result.scalar_one_or_none()

        if record:
            record.value = value
            if description:
                record.description = description
        else:
            record = ConfigurationModel(
                key=key,
                value=value,
                description=description,
            )
            db.add(record)

        await event_bus.publish_durable(
            "config.changed", {"key": key, "value": value}, db,
        )
        await db.commit()
        # 配置值可能包含 API Key、密码等敏感信息，日志只记录键名。
        logger.debug("配置写入: %s", key)

    async def get_plugin_config(self, plugin_name: str, db: AsyncSession) -> dict:
        """
        获取插件的所有配置
        通过 key 前缀匹配读取（如 {plugin_name}.xxx）

        参数:
            plugin_name: 插件标识
            db: 数据库会话
        返回: 配置字典 {key: value}
        """
        prefix = f"{plugin_name}."
        result = await db.execute(
            select(ConfigurationModel).where(
                ConfigurationModel.key.like(f"{prefix}%")
            )
        )
        records = result.scalars().all()
        return {r.key[len(prefix):]: r.value for r in records}

    async def delete_plugin_config(
        self,
        plugin_name: str,
        db: AsyncSession,
        extra_prefixes: Iterable[str] = (),
    ) -> int:
        """
        删除插件配置及显式登记的兼容前缀。

        插件卸载由 PluginManager 统一提交事务，因此本方法只执行删除，
        不 commit。extra_prefixes 用于兼容历史上未使用插件名作为根前缀的配置，
        例如 push 插件的 ``push_center.*``。
        """
        conditions: list[Any] = [ConfigurationModel.key.like(f"{plugin_name}.%")]
        for prefix in extra_prefixes:
            if not prefix:
                continue
            if prefix.endswith("."):
                conditions.append(ConfigurationModel.key.like(f"{prefix}%"))
            else:
                conditions.append(ConfigurationModel.key == prefix)
        if not conditions:
            return 0
        result = await db.execute(delete(ConfigurationModel).where(or_(*conditions)))
        deleted = int(getattr(result, "rowcount", 0) or 0)
        logger.debug("插件配置清理: %s (%d 条)", plugin_name, deleted)
        return deleted
