"""
Widget 挂件引擎
慧眼护农 3.4.12 仪表盘挂件子系统

负责挂件的注册、排序、启停以及数据库持久化（每个管理员的显示配置）
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List

from sqlalchemy import select
from core.db.base import async_session_factory

logger = logging.getLogger(__name__)


def _string_list(value) -> list[str]:
    """仅保留非空字符串，避免异常 JSON 污染挂件标识。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _string_set(value) -> set[str]:
    """兼容列表、集合和字典键，统一过滤挂件标识。"""
    if isinstance(value, dict):
        value = value.keys()
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _parse_widget_state(raw) -> dict | None:
    """解析旧数组或新字典配置，不在此处按运行时注册表剪裁。"""
    try:
        stored = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(stored, list):
        widgets = _string_list(stored)
        return {
            "widgets": widgets,
            "hidden": set(),
            "known": set(widgets),
            "legacy": True,
        }
    if isinstance(stored, dict):
        widgets = _string_list(stored.get("widgets", []))
        return {
            "widgets": widgets,
            "hidden": _string_set(stored.get("hidden", [])),
            "known": _string_set(stored.get("known", [])) | set(widgets),
            "legacy": False,
        }
    return None


def _merge_widget_order(old_widgets: list[str], requested: list[str], preserved: set[str]) -> list[str]:
    """将用户排序写入可用挂件槽位，保留暂不可用挂件的原位置。"""
    requested = [name for name in requested if name not in preserved]
    requested_iter = iter(requested)
    result: list[str] = []
    for name in old_widgets:
        if name in preserved:
            result.append(name)
            continue
        next_name = next(requested_iter, None)
        if next_name:
            result.append(next_name)
    result.extend(name for name in requested_iter if name)
    return list(dict.fromkeys(result))


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

    async def _load_widget_state(self, admin_id: int) -> dict | None:
        """读取管理员挂件配置，返回未按当前注册表剪裁的原始状态。"""
        from core.db.widget import AdminWidget

        async with async_session_factory() as db:
            result = await db.execute(
                select(AdminWidget.widgets).where(AdminWidget.admin_id == admin_id)
            )
            row = result.scalar_one_or_none()
        return _parse_widget_state(row) if row else None

    async def get_widget_config(self, admin_id: int) -> List[str]:
        """
        从数据库读取该管理员的挂件显示列表（有序）

        首次访问（无记录）时返回全部已注册挂件按权重排序的默认列表。
        插件停用只影响本次返回值，不改写其他挂件的持久化布局。
        """
        admin_widgets = self._admin_widgets()
        available = set(admin_widgets) - self._disabled
        state = await self._load_widget_state(admin_id)
        if state is None:
            return [
                w.name for w in sorted(admin_widgets.values(), key=lambda w: w.weight)
                if w.name in available
            ]

        hidden = state["hidden"]
        known = state["known"]
        persisted_widgets = [name for name in state["widgets"] if name not in hidden]
        widget_list = [name for name in persisted_widgets if name in available]
        new_widgets = [
            w.name for w in sorted(admin_widgets.values(), key=lambda w: w.weight)
            if w.name in available and w.name not in known and w.name not in hidden
        ]
        if state["legacy"] or new_widgets:
            persisted_widgets.extend(new_widgets)
            widget_list.extend(new_widgets)
            known.update(new_widgets)
            known.update(admin_widgets)
            await self.save_widget_config(
                admin_id, persisted_widgets, hidden=hidden, known_widgets=known,
            )
        return widget_list

    async def save_widget_config(self, admin_id: int, widget_list: List[str], hidden=None, known_widgets=None):
        """
        持久化管理员的挂件显示列表（含排序）

        参数:
            admin_id:    管理员ID
            widget_list: 挂件标识的有序列表，如 ["admin_count","farmer_count"]
        """
        from core.db.widget import AdminWidget
        from sqlalchemy import text as sa_text

        state = await self._load_widget_state(admin_id)
        old_widgets = list(state["widgets"]) if state else []
        old_known = set(state["known"]) if state else set()
        old_hidden = set(state["hidden"]) if state else set()
        hidden = old_hidden if hidden is None else _string_set(hidden)
        declared = _string_set(known_widgets if known_widgets is not None else ())
        # known 只增不减，避免插件停用期间的短时缺失导致其他挂件被永久遗忘。
        registered = set(self._admin_widgets())
        known = set(old_widgets) | old_known | declared | registered
        allowed = known | registered
        requested = [
            name for name in _string_list(widget_list)
            if name in allowed and name not in hidden
        ]
        old_visible = [name for name in old_widgets if name not in hidden]
        preserved = set(old_visible) - set(requested)
        ordered = _merge_widget_order(old_visible, requested, preserved)
        widgets_json = json.dumps({
            "widgets": ordered,
            "hidden": sorted(hidden),
            "known": sorted(known),
        }, ensure_ascii=False)

        async with async_session_factory() as db:
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

        logger.info(f"[WidgetEngine] 管理员 {admin_id} 挂件配置已保存: {ordered}")

    async def toggle_widget(self, admin_id: int, widget_name: str, enabled: bool) -> List[str]:
        """
        切换单个挂件的显示/隐藏状态

        参数:
            admin_id:    管理员ID
            widget_name: 挂件标识
            enabled:     True=显示, False=隐藏

        返回: 更新后的挂件显示列表
        """
        available_widgets = self._admin_widgets()
        if widget_name not in available_widgets or widget_name in self._disabled:
            return await self.get_widget_config(admin_id)
        state = await self._load_widget_state(admin_id)
        hidden = set(state["hidden"]) if state else set()
        widget_list = (
            [name for name in state["widgets"] if name not in hidden]
            if state else [
                w.name for w in sorted(available_widgets.values(), key=lambda w: w.weight)
                if w.name not in self._disabled
            ]
        )
        if enabled:
            if widget_name not in widget_list:
                widget_list.append(widget_name)
            hidden.discard(widget_name)
        elif widget_name in widget_list:
            widget_list.remove(widget_name)
            hidden.add(widget_name)

        await self.save_widget_config(admin_id, widget_list, hidden=hidden)
        available = set(available_widgets) - self._disabled
        return [name for name in widget_list if name in available and name not in hidden]

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
