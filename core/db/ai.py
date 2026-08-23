"""
AI 对话模块数据模型
承载 AI 大模型对话的会话、消息、技能预设与外部 MCP 服务器数据

表清单:
- hy_ai_conversation:      对话会话（管理员/农户共用，user_type 区分身份体系）
- hy_ai_message:           对话消息（OpenAI 风格 role，换驱动零迁移）
- hy_ai_skill:             技能预设（系统提示词 + MCP 工具白名单，本地预设非 MCP 协议概念）
- hy_ai_mcp_server:        管理员外部 MCP 服务器（管理端添加的管理员专用外部工具源）
- hy_ai_farmer_mcp_server: 农户端外部 MCP 服务器（与管理员表物理隔离，独立 ID 自增）
- hy_farmer_mcp_credential: 农户个人外部 MCP 凭据（按农户与服务器隔离）

设计约定:
- tool_calls/token_usage/tools 等结构化字段以 JSON 字符串存 TEXT（读写由服务层序列化）
- usage 为 MySQL 保留字，列名使用 token_usage 规避
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class AiConversationModel(Base):
    """AI 对话会话表"""
    __tablename__ = "hy_ai_conversation"
    __table_args__ = (
        # 用户维度联合索引（会话列表查询热路径）
        Index("idx_ai_conv_user", "user_type", "user_id"),
        {"comment": "AI对话会话表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="用户体系: admin=管理员, farmer=农户")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="所属用户ID（hy_admin.id 或 hy_farmer.id）")
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="会话标题（取首条用户消息前缀）")
    skill_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="关联技能预设ID（0=未使用技能）")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="状态: 1=正常, 2=已删除")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="最后活跃时间")


class AiMessageModel(Base):
    """AI 对话消息表"""
    __tablename__ = "hy_ai_message"
    __table_args__ = (
        # 会话维度索引（历史消息分页查询热路径）
        Index("idx_ai_msg_conv", "conversation_id"),
        {"comment": "AI对话消息表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="所属会话ID（hy_ai_conversation.id）")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="角色: system/user/assistant/tool（OpenAI 风格）")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="消息正文内容")
    attachments: Mapped[str | None] = mapped_column(Text, nullable=True, comment="消息图片附件 JSON 数组（仅存永久URL与元数据）")
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True, comment="思维链内容（推理模型 assistant 消息专用）")
    tool_calls: Mapped[str | None] = mapped_column(Text, nullable=True, comment="工具调用请求 JSON（assistant 请求工具时）")
    tool_call_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="对应工具调用ID（tool 结果消息专用）")
    token_usage: Mapped[str | None] = mapped_column(Text, nullable=True, comment="token 用量统计 JSON（assistant 消息落库）")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")


class AiSkillModel(Base):
    """AI 技能预设表"""
    __tablename__ = "hy_ai_skill"
    __table_args__ = (
        {"comment": "AI技能预设表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="技能名称")
    description: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="技能描述")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, comment="系统提示词")
    tools: Mapped[str | None] = mapped_column(Text, nullable=True, comment="MCP工具白名单 JSON 数组（空=不限制，引用工具注册名）")
    audience: Mapped[str] = mapped_column(String(16), nullable=False, default="admin", comment="可见范围: admin=后台, farmer=农户端, both=双端")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="状态: 1=启用, 2=停用")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class AiMcpServerModel(Base):
    """管理员外部 MCP 服务器表"""
    __tablename__ = "hy_ai_mcp_server"
    __table_args__ = (
        {"comment": "管理员外部MCP服务器表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="服务器名称")
    url: Mapped[str] = mapped_column(String(256), nullable=False, comment="Streamable HTTP 接入地址")
    api_key: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="Bearer 鉴权密钥（可为空）")
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True, comment="完整 JSON 配置（Claude Desktop 风格，粘贴导入）")
    tools_cache: Mapped[str | None] = mapped_column(Text, nullable=True, comment="工具列表缓存 JSON（工具发现后缓存）")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="状态: 1=启用, 2=停用")
    last_test_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近测试时间")
    last_test_status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="最近测试状态: 0=未测试, 1=成功, 2=失败")
    last_error: Mapped[str] = mapped_column(String(512), nullable=False, default="", comment="最近测试错误信息")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class AiFarmerMcpServerModel(Base):
    """农户端外部 MCP 服务器表（与管理员表物理隔离，独立 ID 自增）"""
    __tablename__ = "hy_ai_farmer_mcp_server"
    __table_args__ = (
        {"comment": "农户端外部MCP服务器表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="服务器名称")
    url: Mapped[str] = mapped_column(String(256), nullable=False, comment="Streamable HTTP 接入地址")
    api_key: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="Bearer 鉴权密钥（可为空）")
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True, comment="完整 JSON 配置（Claude Desktop 风格，粘贴导入）")
    tools_cache: Mapped[str | None] = mapped_column(Text, nullable=True, comment="工具列表缓存 JSON（工具发现后缓存）")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="状态: 1=启用, 2=停用")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class FarmerMcpCredentialModel(Base):
    """农户个人外部 MCP 凭据表（按农户与服务器隔离）"""
    __tablename__ = "hy_farmer_mcp_credential"
    __table_args__ = (
        Index("uk_farmer_mcp_credential", "farmer_id", "server_id", unique=True),
        Index("idx_farmer_mcp_credential_farmer", "farmer_id"),
        {"comment": "农户外部MCP个人凭据表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    farmer_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="农户ID（hy_farmer.id）")
    server_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="公共MCP服务器ID（hy_ai_farmer_mcp_server.id）")
    api_key: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="农户个人 Bearer 鉴权密钥（明文存储）")
    tools_cache: Mapped[str | None] = mapped_column(Text, nullable=True, comment="按个人凭据缓存的工具列表 JSON")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="状态: 1=启用, 2=停用")
    last_test_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近测试时间")
    last_test_status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="最近测试状态: 0=未测试, 1=成功, 2=失败")
    last_error: Mapped[str] = mapped_column(String(512), nullable=False, default="", comment="最近测试错误信息")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
