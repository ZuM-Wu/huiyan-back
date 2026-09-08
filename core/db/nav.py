"""
页面注册表模型（Nav/Menu 分层架构 — 借鉴 ZJMF NavModel）

hy_nav 记录"系统有哪些可用页面"（页面注册层），
hy_menu 记录"侧边栏怎么排列"（布局层）。
两者分离后，导航管理可从 hy_nav 中选择页面编排到 hy_menu。
"""
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from core.time_utils import china_now
from core.db.base import Base


class Nav(Base):
    """页面注册表"""
    __tablename__ = "hy_nav"
    __table_args__ = {"comment": "页面注册表（系统预设页 + 插件声明页）"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="页面ID")
    key: Mapped[str] = mapped_column(String(64), nullable=False, comment="页面唯一标识（如 dashboard, plugin_hello_world）")
    title: Mapped[str] = mapped_column(String(128), nullable=False, comment="页面显示名称")
    path: Mapped[str] = mapped_column(String(256), default="", comment="前端路由路径")
    icon: Mapped[str] = mapped_column(String(64), default="", comment="图标名称")
    nav_type: Mapped[str] = mapped_column(String(32), default="admin", comment="导航类型：admin=后台，frontend=前台")
    source: Mapped[str] = mapped_column(String(32), default="system", comment="来源：system=系统预设，plugin=插件声明")
    plugin: Mapped[str] = mapped_column(String(64), default="", comment="所属插件标识（source=plugin 时必填）")
    group_name: Mapped[str] = mapped_column(String(64), default="", comment="分组名称（供导航管理下拉按分组展示）")
    page_type: Mapped[str] = mapped_column(String(32), default="system", comment="页面类型：system=系统页，custom=自定义路由")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
