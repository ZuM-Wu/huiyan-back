# -*- coding: utf-8 -*-
"""
农业知识库插件数据模型

表定义规范：
- 表名前缀 hy_plugin_knowledge_{entity}
- 继承 core.db.base.Base
- 每个字段带中文 comment

注意：插件业务表由 plugin.install() 通过 migrations/install.sql 建表，
此 ORM 模型仅用于业务代码中的查询与序列化，不参与系统启动时的 create_all。
"""
from core.time_utils import china_now
from sqlalchemy import Column, DateTime, Integer, String, Text

from core.db.base import Base


class KnowledgeCategory(Base):
    """知识分类表 — 二级结构（parent_id=0 为大类，否则为子类）"""

    __tablename__ = "hy_plugin_knowledge_category"
    __table_args__ = {"comment": "农业知识库插件-分类表(二级)"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="分类ID")
    parent_id = Column(Integer, nullable=False, default=0, index=True, comment="父分类ID 0=大类")
    name = Column(String(50), nullable=False, comment="分类名称")
    sort_order = Column(Integer, nullable=False, default=0, comment="排序值 越小越靠前")
    status = Column(Integer, nullable=False, default=1, comment="状态 0停用 1启用")
    create_time = Column(DateTime, default=china_now, comment="创建时间")
    update_time = Column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class KnowledgeEntry(Base):
    """知识条目表 — 一条农业知识（含发生原因/解决方案/典型图片）"""

    __tablename__ = "hy_plugin_knowledge_entry"
    __table_args__ = {"comment": "农业知识库插件-知识条目表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="知识条目ID")
    title = Column(String(200), nullable=False, comment="知识标题 如 小麦白粉病")
    category_id = Column(Integer, nullable=False, default=0, index=True, comment="所属分类ID")
    crop = Column(String(200), nullable=False, default="", comment="适用作物 逗号分隔标签")
    summary = Column(String(500), nullable=False, default="", comment="摘要")
    cause = Column(Text, nullable=True, comment="发生原因")
    solution = Column(Text, nullable=True, comment="解决方案")
    images = Column(Text, nullable=True, comment="典型图片 JSON数组存/upload/...URL")
    view_count = Column(Integer, nullable=False, default=0, comment="浏览次数")
    sort_order = Column(Integer, nullable=False, default=0, comment="排序值 越小越靠前")
    admin_id = Column(Integer, nullable=False, default=0, comment="最后操作管理员ID")
    create_time = Column(DateTime, default=china_now, comment="创建时间")
    update_time = Column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class KnowledgeCorrection(Base):
    """勘误表 — 农户对某条知识提交的修正建议，管理员审核采纳/驳回"""

    __tablename__ = "hy_plugin_knowledge_correction"
    __table_args__ = {"comment": "农业知识库插件-勘误表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="勘误ID")
    knowledge_id = Column(Integer, nullable=False, default=0, index=True, comment="关联知识条目ID")
    farmer_id = Column(Integer, nullable=False, default=0, comment="提交农户ID")
    content = Column(String(1000), nullable=False, default="", comment="修正建议")
    status = Column(Integer, nullable=False, default=0, index=True, comment="状态 0待处理 1已采纳 2已驳回")
    admin_note = Column(String(500), nullable=False, default="", comment="管理员处理备注")
    admin_id = Column(Integer, nullable=False, default=0, comment="处理管理员ID")
    create_time = Column(DateTime, default=china_now, comment="提交时间")
    handle_time = Column(DateTime, nullable=True, comment="处理时间")
