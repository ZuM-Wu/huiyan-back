# -*- coding: utf-8 -*-
"""
Hello World 插件数据模型

演示插件私有业务表的定义规范：
- 表名前缀 hy_plugin_{plugin}_{entity}
- 继承 core.db.base.Base
- 每个字段带中文 comment

注意：插件业务表由 plugin.install() 通过 migrations/install.sql 建表，
此 ORM 模型仅用于业务代码中的查询与序列化，不参与系统启动时的 create_all。
"""
from core.time_utils import china_now
from sqlalchemy import Column, DateTime, Integer, String, Text

from core.db.base import Base


class HelloMessage(Base):
    """示例留言表 — 演示插件私有业务实体"""

    __tablename__ = "hy_plugin_hello_world_message"
    __table_args__ = {"comment": "Hello World 插件留言表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="留言ID")
    title = Column(String(128), nullable=False, comment="留言标题")
    content = Column(Text, nullable=False, comment="留言内容")
    author = Column(String(64), default="", comment="留言人")
    status = Column(Integer, default=1, comment="状态：0=隐藏, 1=显示")
    create_time = Column(DateTime, default=china_now, comment="创建时间")
