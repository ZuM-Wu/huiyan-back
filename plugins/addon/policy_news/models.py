"""农业政策资讯插件数据模型。"""
from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from core.db.base import Base


class PolicyNewsItem(Base):
    """已抓取的政策条目。"""

    __tablename__ = "hy_plugin_policy_news_item"
    __table_args__ = (
        UniqueConstraint("source", "url", name="uq_policy_news_source_url"),
        Index("ix_policy_news_source", "source"),
        Index("ix_policy_news_url", "url"),
        Index("ix_policy_news_published_at", "published_at"),
        Index("ix_policy_news_last_seen_at", "last_seen_at"),
        {"comment": "农业政策资讯条目"},
    )

    id = Column(Integer, primary_key=True, comment="条目主键")
    source = Column(String(32), nullable=False, comment="政策来源")
    title = Column(String(512), nullable=False, comment="政策标题")
    url = Column(String(700), nullable=False, comment="政策详情地址")
    published_at = Column(DateTime, nullable=True, comment="发布日期")
    first_seen_at = Column(DateTime, nullable=False, comment="首次发现时间")
    last_seen_at = Column(DateTime, nullable=False, comment="最后抓取时间")
    summary = Column(Text, nullable=True, comment="政策摘要")


class PolicyNewsState(Base):
    """每个官方来源的最近抓取状态。"""

    __tablename__ = "hy_plugin_policy_news_state"
    __table_args__ = {"comment": "农业政策资讯来源抓取状态"}

    id = Column(Integer, primary_key=True, comment="状态主键")
    source = Column(String(32), nullable=False, unique=True, comment="政策来源")
    last_attempt_at = Column(DateTime, nullable=True, comment="最近尝试时间")
    last_success_at = Column(DateTime, nullable=True, comment="最近成功时间")
    last_error = Column(String(1024), nullable=False, default="", comment="最近错误信息")
    item_count = Column(Integer, nullable=False, default=0, comment="来源条目数量")
