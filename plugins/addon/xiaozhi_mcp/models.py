# -*- coding: utf-8 -*-
"""小智 AI MCP 插件业务模型。"""
from core.time_utils import china_now
from sqlalchemy import Column, DateTime, Integer, String, Text

from core.db.base import Base


class XiaozhiMcpServerModel(Base):
    """小智插件专属外部 MCP 服务器表。"""

    __tablename__ = "hy_plugin_xiaozhi_mcp_server"
    __table_args__ = ({"comment": "小智AI MCP插件外部服务器表"},)

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    name = Column(String(64), nullable=False, comment="服务器名称")
    url = Column(String(256), nullable=False, comment="Streamable HTTP接入地址")
    api_key = Column(String(256), nullable=False, default="", comment="Bearer鉴权密钥（可为空）")
    config_json = Column(Text, nullable=True, comment="完整JSON配置（Claude Desktop风格）")
    tools_cache = Column(Text, nullable=True, comment="工具列表缓存JSON")
    status = Column(Integer, nullable=False, default=1, comment="状态：1启用，2停用")
    last_test_time = Column(DateTime, nullable=True, comment="最近测试时间")
    last_test_status = Column(
        Integer,
        nullable=False,
        default=0,
        comment="最近测试状态：0未测试，1成功，2失败",
    )
    last_error = Column(String(512), nullable=False, default="", comment="最近测试错误信息")
    create_time = Column(DateTime, default=china_now, comment="创建时间")
    update_time = Column(
        DateTime,
        default=china_now,
        onupdate=china_now,
        comment="更新时间",
    )
