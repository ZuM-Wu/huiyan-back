"""
菜单表模型
菜单数据持久化到 hy_menu 表，支持后台/前台双导航类型
"""
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from core.time_utils import china_now
from core.db.base import Base


class Menu(Base):
    """菜单表"""
    __tablename__ = "hy_menu"
    __table_args__ = {"comment": "菜单表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="菜单ID")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="菜单标识")
    title: Mapped[str] = mapped_column(String(128), nullable=False, comment="菜单标题")
    path: Mapped[str] = mapped_column(String(256), default="", comment="前端路由路径")
    icon: Mapped[str] = mapped_column(String(64), default="", comment="图标名称")
    parent_id: Mapped[int] = mapped_column(Integer, default=0, comment="父菜单ID, 0=顶级")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序")
    plugin: Mapped[str] = mapped_column(String(64), default="", comment="所属插件标识，空字符串=系统核心菜单")
    visible: Mapped[int] = mapped_column(Integer, default=1, comment="是否可见: 0=隐藏, 1=显示")
    nav_type: Mapped[str] = mapped_column(String(32), default="admin", comment="导航类型：admin=后台导航，frontend=前台导航")
    page_type: Mapped[str] = mapped_column(String(32), default="system", comment="页面类型：system/url/separator/list")
    target_type: Mapped[str] = mapped_column(String(16), default="", comment="外链打开方式：空=内部页面，_blank=新标签页，iframe=内嵌iframe")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
