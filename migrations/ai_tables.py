# -*- coding: utf-8 -*-
"""
迁移脚本 - 创建 AI 对话模块 4 张数据表

背景：
    AI 大模型对话功能的数据表（会话/消息/技能预设/外部MCP服务器）。
    新库由 Base.metadata.create_all 直接建出（ORM 见 core/db/ai.py），
    存量库由本迁移补齐；通过 migrations/registry.py 注册表统一调度，
    探测函数发现表已存在时仅标记已应用（探测回填）。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 本迁移管辖的表名清单（探测以最后建的 hy_ai_mcp_server 为准）
_TABLES_DDL = [
    (
        "hy_ai_conversation",
        "CREATE TABLE IF NOT EXISTS hy_ai_conversation ("
        "  id INT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
        "  user_type VARCHAR(16) NOT NULL COMMENT '用户体系: admin=管理员, farmer=农户',"
        "  user_id INT NOT NULL COMMENT '所属用户ID（hy_admin.id 或 hy_farmer.id）',"
        "  title VARCHAR(128) NOT NULL DEFAULT '' COMMENT '会话标题（取首条用户消息前缀）',"
        "  skill_id INT NOT NULL DEFAULT 0 COMMENT '关联技能预设ID（0=未使用技能）',"
        "  status INT NOT NULL DEFAULT 1 COMMENT '状态: 1=正常, 2=已删除',"
        "  create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "  update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后活跃时间',"
        "  PRIMARY KEY (id),"
        "  KEY idx_ai_conv_user (user_type, user_id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI对话会话表'",
    ),
    (
        "hy_ai_message",
        "CREATE TABLE IF NOT EXISTS hy_ai_message ("
        "  id INT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
        "  conversation_id INT NOT NULL COMMENT '所属会话ID（hy_ai_conversation.id）',"
        "  role VARCHAR(16) NOT NULL COMMENT '角色: system/user/assistant/tool（OpenAI 风格）',"
        "  content TEXT NOT NULL COMMENT '消息正文内容',"
        "  attachments TEXT NULL COMMENT '消息图片附件 JSON 数组（仅存永久URL与元数据）',"
        "  reasoning TEXT NULL COMMENT '思维链内容（推理模型 assistant 消息专用）',"
        "  tool_calls TEXT NULL COMMENT '工具调用请求 JSON（assistant 请求工具时）',"
        "  tool_call_id VARCHAR(64) NOT NULL DEFAULT '' COMMENT '对应工具调用ID（tool 结果消息专用）',"
        "  token_usage TEXT NULL COMMENT 'token 用量统计 JSON（assistant 消息落库）',"
        "  create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "  PRIMARY KEY (id),"
        "  KEY idx_ai_msg_conv (conversation_id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI对话消息表'",
    ),
    (
        "hy_ai_skill",
        "CREATE TABLE IF NOT EXISTS hy_ai_skill ("
        "  id INT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
        "  name VARCHAR(64) NOT NULL COMMENT '技能名称',"
        "  description VARCHAR(256) NOT NULL DEFAULT '' COMMENT '技能描述',"
        "  system_prompt TEXT NOT NULL COMMENT '系统提示词',"
        "  tools TEXT NULL COMMENT 'MCP工具白名单 JSON 数组（空=不限制，引用工具注册名）',"
        "  audience VARCHAR(16) NOT NULL DEFAULT 'admin' COMMENT '可见范围: admin=后台, farmer=农户端, both=双端',"
        "  status INT NOT NULL DEFAULT 1 COMMENT '状态: 1=启用, 2=停用',"
        "  create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "  update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',"
        "  PRIMARY KEY (id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI技能预设表'",
    ),
    (
        "hy_ai_mcp_server",
        "CREATE TABLE IF NOT EXISTS hy_ai_mcp_server ("
        "  id INT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
        "  name VARCHAR(64) NOT NULL COMMENT '服务器名称',"
        "  url VARCHAR(256) NOT NULL COMMENT 'Streamable HTTP 接入地址',"
        "  api_key VARCHAR(256) NOT NULL DEFAULT '' COMMENT 'Bearer 鉴权密钥（可为空）',"
        "  status INT NOT NULL DEFAULT 1 COMMENT '状态: 1=启用, 2=停用',"
        "  create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "  update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',"
        "  PRIMARY KEY (id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='外部MCP服务器表'",
    ),
]


async def _table_exists(db, table: str) -> bool:
    """检查数据表是否已存在"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t"
    ), {"t": table})
    return bool(result.scalar())


async def probe(db) -> bool:
    """4 张 AI 表全部存在则视为已应用"""
    for table, _ in _TABLES_DDL:
        if not await _table_exists(db, table):
            return False
    return True


async def apply(db):
    """创建 AI 对话模块 4 张数据表（逐表幂等）"""
    for table, ddl in _TABLES_DDL:
        await db.execute(text(ddl))
        logger.info("[迁移] %s 表已就绪", table)
