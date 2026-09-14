"""
Widget 挂件引擎
慧眼护农 3.4.1 仪表盘挂件子系统

负责挂件的注册、排序、启停以及数据库持久化（每个管理员的显示配置）
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List

from sqlalchemy import select
from core.db.base import async_session_factory

logger = logging.getLogger(__name__)


class BaseWidget(ABC):
    """
    仪表盘挂件抽象基类

    属性:
        title:       str  挂件标题
        columns:     int  占列数 (1-4)
        weight:      int  排序权重（越大越靠后）
        name:        str  挂件唯一标识
        widget_type: str  挂件渲染类型（stat_card / list / chart / todo / hardware）
        owner:       str  挂件所属插件或 system
        audience:    str  展示端：admin / farmer / both
    """

    title: str = "未命名挂件"
    columns: int = 2
    weight: int = 100
    name: str = ""
    widget_type: str = "stat_card"
    owner: str = "system"
    audience: str = "admin"

    @abstractmethod
    async def get_data(self) -> dict:
        """获取挂件数据 — 子类必须实现"""
        ...

    async def render(self) -> dict:
        """渲染挂件输出 — 子类可选覆写"""
        data = await self.get_data()
        return {
            "name": self.name or self.__class__.__name__,
            "title": self.title,
            "columns": self.columns,
            "weight": self.weight,
            "widget_type": self.widget_type,
            "owner": self.owner,
            "audience": self.audience,
            "data": data,
        }


class WidgetEngine:
    """
    挂件引擎
    管理仪表盘挂件的注册、排序、启停、数据库持久化
    """

    def __init__(self):
        self._widgets: Dict[str, BaseWidget] = {}
        self._disabled: set = set()

    def register(self, widget: BaseWidget, owner: str | None = None):
        """注册挂件"""
        name = widget.name or widget.__class__.__name__
        widget.name = name
        if owner:
            widget.owner = owner
        if widget.audience not in {"admin", "farmer", "both"}:
            raise ValueError(f"挂件受众无效: {widget.audience}")
        current = self._widgets.get(name)
        if current is not None and current.owner != widget.owner:
            raise ValueError(f"挂件标识已被其他 owner 占用: {name}")
        self._widgets[name] = widget
        logger.debug(f"[WidgetEngine] 已注册挂件: {name}")

    def unregister_owner(self, owner: str) -> list[str]:
        """按插件 owner 注销挂件，返回被移除的挂件标识。"""
        removed = [name for name, widget in self._widgets.items() if widget.owner == owner]
        for name in removed:
            self._widgets.pop(name, None)
            self._disabled.discard(name)
        return removed

    def _admin_widgets(self) -> dict[str, BaseWidget]:
        """返回管理端可配置的挂件，避免 farmer-only 挂件进入管理员布局。"""
        return {
            name: widget for name, widget in self._widgets.items()
            if widget.audience in {"admin", "both"}
        }

    def disable(self, name: str):
        """禁用挂件（内存级，用于全局禁用）"""
        self._disabled.add(name)

    def enable(self, name: str):
        """启用挂件（内存级，用于全局启用）"""
        self._disabled.discard(name)

    async def get_widget_list(self) -> List[dict]:
        """获取所有已注册挂件的元信息（按权重排序）"""
        widgets = sorted(self._admin_widgets().values(), key=lambda w: w.weight)
        result = []
        for w in widgets:
            status = "disabled" if w.name in self._disabled else "enabled"
            result.append({
                "name": w.name,
                "title": w.title,
                "columns": w.columns,
                "weight": w.weight,
                "widget_type": w.widget_type,
                "owner": w.owner,
                "audience": w.audience,
                "status": status,
            })
        return result

    # ================================================================
    # 数据库持久化 — 管理员个人级配置
    # ================================================================

    async def get_widget_config(self, admin_id: int) -> List[str]:
        """
        从数据库读取该管理员的挂件显示列表（有序）

        首次访问（无记录）时返回全部已注册挂件按权重排序的默认列表
        """
        from core.db.widget import AdminWidget
        admin_widgets = self._admin_widgets()

        async with async_session_factory() as db:
            result = await db.execute(
                select(AdminWidget.widgets).where(AdminWidget.admin_id == admin_id)
            )
            row = result.scalar_one_or_none()

        if row:
            try:
                stored = json.loads(row) if isinstance(row, str) else row
                if isinstance(stored, list):
                    # 兼容旧版数组配置：存量管理员首次升级时补齐新挂件。
                    widget_list = [w for w in stored if w in admin_widgets]
                    known = set(widget_list)
                    widget_list.extend(
                        w.name for w in sorted(admin_widgets.values(), key=lambda w: w.weight)
                        if w.name not in known and w.name not in self._disabled
                    )
                    await self.save_widget_config(admin_id, widget_list, known_widgets=admin_widgets)
                    return widget_list
                if isinstance(stored, dict):
                    widget_list = stored.get("widgets", [])
                    hidden = set(stored.get("hidden", []))
                    known = set(stored.get("known", []))
                    widget_list = [w for w in widget_list if w in admin_widgets and w not in hidden]
                    new_widgets = [
                        w.name for w in sorted(admin_widgets.values(), key=lambda w: w.weight)
                        if w.name not in known and w.name not in hidden and w.name not in self._disabled
                    ]
                    if new_widgets:
                        widget_list.extend(new_widgets)
                        known.update(admin_widgets)
                        await self.save_widget_config(admin_id, widget_list, hidden=hidden, known_widgets=known)
                    return widget_list
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

        return [
            w.name for w in sorted(admin_widgets.values(), key=lambda w: w.weight)
            if w.name not in self._disabled
        ]

    async def save_widget_config(self, admin_id: int, widget_list: List[str], hidden=None, known_widgets=None):
        """
        持久化管理员的挂件显示列表（含排序）

        参数:
            admin_id:    管理员ID
            widget_list: 挂件标识的有序列表，如 ["admin_count","farmer_count"]
        """
        from core.db.widget import AdminWidget
        from sqlalchemy import text as sa_text

        async with async_session_factory() as db:
            # 读取旧状态，保存排序时必须保留管理员主动隐藏的挂件。
            existing = (await db.execute(
                select(AdminWidget.widgets).where(AdminWidget.admin_id == admin_id)
            )).scalar_one_or_none()
            old_hidden = set()
            if existing:
                try:
                    stored = json.loads(existing) if isinstance(existing, str) else existing
                    if isinstance(stored, dict):
                        old_hidden = set(stored.get("hidden", []))
                except (json.JSONDecodeError, TypeError):
                    pass
            hidden = old_hidden if hidden is None else set(hidden)
            declared = known_widgets if known_widgets is not None else self._admin_widgets()
            known = set(declared)
            known.intersection_update(self._admin_widgets())
            widget_list = [name for name in widget_list if name in self._admin_widgets()]
            widgets_json = json.dumps({
                "widgets": widget_list,
                "hidden": sorted(hidden),
                "known": sorted(known),
            }, ensure_ascii=False)

            # 幂等 upsert：有则更新，无则插入
            exist_result = await db.execute(
                select(AdminWidget.id).where(AdminWidget.admin_id == admin_id)
            )
            exist_id = exist_result.scalar_one_or_none()

            if exist_id:
                await db.execute(
                    sa_text("UPDATE hy_admin_widget SET widgets = :widgets WHERE admin_id = :aid"),
                    {"widgets": widgets_json, "aid": admin_id}
                )
            else:
                db.add(AdminWidget(admin_id=admin_id, widgets=widgets_json))

            await db.commit()

        logger.info(f"[WidgetEngine] 管理员 {admin_id} 挂件配置已保存: {widget_list}")

    async def toggle_widget(self, admin_id: int, widget_name: str, enabled: bool) -> List[str]:
        """
        切换单个挂件的显示/隐藏状态

        参数:
            admin_id:    管理员ID
            widget_name: 挂件标识
            enabled:     True=显示, False=隐藏

        返回: 更新后的挂件显示列表
        """
        widget_list = await self.get_widget_config(admin_id)
        if widget_name not in self._admin_widgets():
            return widget_list
        hidden = set()
        from core.db.widget import AdminWidget
        async with async_session_factory() as db:
            raw = (await db.execute(
                select(AdminWidget.widgets).where(AdminWidget.admin_id == admin_id)
            )).scalar_one_or_none()
        if raw:
            try:
                stored = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(stored, dict):
                    hidden = set(stored.get("hidden", []))
            except (json.JSONDecodeError, TypeError):
                pass
        if enabled:
            if widget_name not in widget_list:
                widget_list.append(widget_name)
            hidden.discard(widget_name)
        elif widget_name in widget_list:
            widget_list.remove(widget_name)
            hidden.add(widget_name)

        await self.save_widget_config(
            admin_id, widget_list, hidden=hidden,
            known_widgets=self._admin_widgets(),
        )
        return widget_list

    async def get_dashboard(self, admin_id: int) -> dict:
        """
        一次获取首页仪表盘所需的全部数据

        返回:
            {
                all_widgets:  [所有已注册挂件元信息],
                show_widgets: [当前管理员显示的挂件标识列表],
                widget_data:  [当前管理员显示的挂件渲染数据]
            }
        """
        all_widgets = await self.get_widget_list()
        show_widgets = await self.get_widget_config(admin_id)

        # 按管理员的排序获取挂件渲染数据
        widget_data = []
        for name in show_widgets:
            widget = self._widgets.get(name)
            if widget and name not in self._disabled:
                try:
                    widget_data.append(await widget.render())
                except Exception as e:
                    logger.error(f"[WidgetEngine] 挂件 '{name}' 渲染失败: {e}")

        return {
            "all_widgets": all_widgets,
            "show_widgets": show_widgets,
            "widget_data": widget_data,
        }

    async def get_farmer_dashboard(self) -> dict:
        """获取农户首页挂件，农户端不保存个人显隐配置。"""
        widgets = [
            widget for widget in sorted(self._widgets.values(), key=lambda item: item.weight)
            if widget.audience in {"farmer", "both"} and widget.name not in self._disabled
        ]
        widget_data = []
        for widget in widgets:
            try:
                widget_data.append(await widget.render())
            except Exception as exc:
                logger.error("[WidgetEngine] 农户挂件 '%s' 渲染失败: %s", widget.name, exc)
        return {
            "all_widgets": [
                {"name": item.name, "title": item.title, "columns": item.columns,
                 "weight": item.weight, "widget_type": item.widget_type,
                 "owner": item.owner, "audience": item.audience, "status": "enabled"}
                for item in widgets
            ],
            "show_widgets": [item.name for item in widgets],
            "widget_data": widget_data,
        }


# 全局单例
widget_engine = WidgetEngine()
