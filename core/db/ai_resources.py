# -*- coding: utf-8 -*-
"""AgentScope 管理资源的项目扩展模型。

AgentScope 原生负责 Credential/Agent/Session 等资源；本模块只保存
管理员需要维护的 ModelCard 目录和连接池运行元数据，不保存任何 API Key。
"""
from datetime import datetime
from core.time_utils import china_now
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class AgentScopeModelCardModel(Base):
    """可编辑的模型能力目录。"""

    __tablename__ = "hy_agentscope_model_card"
    __table_args__ = (
        Index("uq_agentscope_model_connection_name", "connection_id", "model_name", unique=True),
        {"comment": "AgentScope 可编辑 ModelCard 目录"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="来源连接池 ID")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, comment="Credential Provider 类型")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型 ID")
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="模型展示名称")
    input_types: Mapped[str] = mapped_column(Text, nullable=False, default="[]", comment="输入模态 JSON 数组")
    output_types: Mapped[str] = mapped_column(Text, nullable=False, default="[]", comment="输出能力 JSON 数组")
    context_size: Mapped[int] = mapped_column(Integer, nullable=False, default=32768, comment="上下文长度")
    output_size: Mapped[int] = mapped_column(Integer, nullable=False, default=4096, comment="最大输出长度")
    support_tools: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否支持工具调用")
    support_reasoning: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否支持思考")
    support_vision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否支持视觉输入")
    parameter_schema: Mapped[str] = mapped_column(Text, nullable=False, default="{}", comment="模型参数 Schema JSON")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="状态：1启用，2停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class AgentScopeConnectionPoolModel(Base):
    """Credential 连接池元数据，不保存密钥。"""

    __tablename__ = "hy_agentscope_connection_pool"
    __table_args__ = (
        Index("uq_agentscope_connection_credential", "credential_id", unique=True),
        {"comment": "AgentScope Credential 模型连接池"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    preset_key: Mapped[str] = mapped_column(String(64), nullable=False, default="custom_openai", comment="连接预设键")
    protocol: Mapped[str] = mapped_column(String(64), nullable=False, default="openai_chat_completions", comment="连接协议类型")
    vendor_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="供应商显示名称")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, comment="Credential Provider 类型")
    credential_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="AgentScope Credential ID")
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="连接显示名称")
    default_model: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="默认模型 ID")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="状态：1启用，2停用")
    is_default: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否默认连接：0否，1是")
    last_test_status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="测试状态：0未测，1成功，2失败")
    last_test_error: Mapped[str] = mapped_column(String(512), nullable=False, default="", comment="最近测试错误")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近测试时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class AgentScopeConnectionModelDiscoveryModel(Base):
    """连接最近一次成功发现的模型及其能力快照。"""

    __tablename__ = "hy_agentscope_connection_model_discovery"
    __table_args__ = (
        Index("uq_agentscope_connection_discovery_model", "connection_id", "model_name", unique=True),
        Index("ix_agentscope_connection_discovery_connection", "connection_id"),
        {"comment": "AgentScope 连接模型发现结果"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="来源连接池 ID")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="远端模型 ID")
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="模型展示名称")
    input_types: Mapped[str] = mapped_column(Text, nullable=False, default="[]", comment="输入模态 JSON 数组")
    output_types: Mapped[str] = mapped_column(Text, nullable=False, default="[]", comment="输出能力 JSON 数组")
    context_size: Mapped[int] = mapped_column(Integer, nullable=False, default=32768, comment="上下文长度")
    output_size: Mapped[int] = mapped_column(Integer, nullable=False, default=4096, comment="最大输出长度")
    support_tools: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否支持工具调用")
    support_reasoning: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否支持思考")
    support_vision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否支持视觉输入")
    parameter_schema: Mapped[str] = mapped_column(Text, nullable=False, default="{}", comment="模型参数 Schema JSON")
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="发现时间")
