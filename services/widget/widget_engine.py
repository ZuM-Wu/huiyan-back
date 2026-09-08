"""
Widget 挂件引擎
慧眼护农 3.4.0 仪表盘挂件子系统

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
        widget_type: str  挂件渲染类型（stat_card / list / chart）
    """

    title: str = "未命名挂件"
    columns: int = 2
    weight: int = 100
    name: str = ""
    widget_type: str = "stat_card"

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

    def register(self, widget: BaseWidget):
        """注册挂件"""
        name = widget.name or widget.__class__.__name__
        widget.name = name
        self._widgets[name] = widget
        logger.debug(f"[WidgetEngine] 已注册挂件: {name}")

    def disable(self, name: str):
        """禁用挂件（内存级，用于全局禁用）"""
        self._disabled.add(name)

    def enable(self, name: str):
        """启用挂件（内存级，用于全局启用）"""
        self._disabled.discard(name)

    async def get_widget_list(self) -> List[dict]:
        """获取所有已注册挂件的元信息（按权重排序）"""
        widgets = sorted(self._widgets.values(), key=lambda w: w.weight)
        result = []
        for w in widgets:
            status = "disabled" if w.name in self._disabled else "enabled"
            result.append({
                "name": w.name,
                "title": w.title,
                "columns": w.columns,
                "weight": w.weight,
                "widget_type": w.widget_type,
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

        async with async_session_factory() as db:
            result = await db.execute(
                select(AdminWidget.widgets).where(AdminWidget.admin_id == admin_id)
            )
            row = result.scalar_one_or_none()

        if row:
            try:
                widget_list = json.loads(row) if isinstance(row, str) else row
                # 过滤掉已不存在的挂件（卸载后清理）
                return [w for w in widget_list if w in self._widgets]
            except (json.JSONDecodeError, TypeError):
                pass

        # 默认：全部已注册挂件按权重排序
        return [
            w.name for w in sorted(self._widgets.values(), key=lambda w: w.weight)
            if w.name not in self._disabled
        ]

    async def save_widget_config(self, admin_id: int, widget_list: List[str]):
        """
        持久化管理员的挂件显示列表（含排序）

        参数:
            admin_id:    管理员ID
            widget_list: 挂件标识的有序列表，如 ["admin_count","farmer_count"]
        """
        from core.db.widget import AdminWidget
        from sqlalchemy import text as sa_text

        widgets_json = json.dumps(widget_list, ensure_ascii=False)

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

        if enabled:
            if widget_name not in widget_list:
                # 新增的挂件追加到末尾
                widget_list.append(widget_name)
        elif widget_name in widget_list:
            widget_list.remove(widget_name)

        await self.save_widget_config(admin_id, widget_list)
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


# 全局单例
widget_engine = WidgetEngine()
