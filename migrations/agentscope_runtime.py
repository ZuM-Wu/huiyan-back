# -*- coding: utf-8 -*-
"""AgentScope 运行时资源与 MySQL MessageBus 表。

AgentScope 自带 SQL Storage 的 ``create_tables`` 和 Alembic 自动迁移在本项目
中关闭，所有结构由本迁移注册表统一管理。旧 ``hy_ai_*`` 表由后续
``agentscope_hard_cut`` 迁移删除，不保留兼容读写链路。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


_DDL = [
    ("agents", """
        CREATE TABLE IF NOT EXISTS agents (
            user_id VARCHAR(255) NOT NULL COMMENT 'AgentScope 用户标识：admin:{id} 或 farmer:{id}',
            source VARCHAR(16) NOT NULL COMMENT 'Agent 来源：user/team',
            id VARCHAR(255) NOT NULL COMMENT 'Agent 唯一标识',
            created_at DATETIME NOT NULL COMMENT '创建时间（UTC）',
            updated_at DATETIME NOT NULL COMMENT '更新时间（UTC）',
            payload JSON NOT NULL COMMENT 'AgentScope AgentRecord JSON 负载',
            PRIMARY KEY (id),
            KEY ix_agents_user_id (user_id),
            KEY ix_agents_source (source),
            KEY ix_agents_user_source (user_id, source)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope Agent 资源表'
    """),
    ("credentials", """
        CREATE TABLE IF NOT EXISTS credentials (
            user_id VARCHAR(255) NOT NULL COMMENT '凭据所属用户标识',
            id VARCHAR(255) NOT NULL COMMENT '凭据唯一标识',
            created_at DATETIME NOT NULL COMMENT '创建时间（UTC）',
            updated_at DATETIME NOT NULL COMMENT '更新时间（UTC）',
            payload JSON NOT NULL COMMENT 'CredentialRecord JSON 负载，密钥按 AgentScope 规则处理',
            PRIMARY KEY (id),
            KEY ix_credentials_user_id (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope Credential 资源表'
    """),
    ("sessions", """
        CREATE TABLE IF NOT EXISTS sessions (
            user_id VARCHAR(255) NOT NULL COMMENT '会话所属用户标识',
            agent_id VARCHAR(255) NOT NULL COMMENT '绑定 Agent 标识',
            source VARCHAR(16) NOT NULL COMMENT '会话来源：user/schedule/channel',
            source_schedule_id VARCHAR(255) NULL COMMENT '来源计划任务标识',
            team_id VARCHAR(255) NULL COMMENT '所属团队标识',
            id VARCHAR(255) NOT NULL COMMENT '会话唯一标识',
            created_at DATETIME NOT NULL COMMENT '创建时间（UTC）',
            updated_at DATETIME NOT NULL COMMENT '更新时间（UTC）',
            payload JSON NOT NULL COMMENT 'SessionRecord JSON 负载',
            PRIMARY KEY (id),
            KEY ix_sessions_user_id (user_id),
            KEY ix_sessions_agent_id (agent_id),
            KEY ix_sessions_user_agent (user_id, agent_id),
            KEY ix_sessions_source_schedule_id (source_schedule_id),
            KEY ix_sessions_team_id (team_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope Session 资源表'
    """),
    ("messages", """
        CREATE TABLE IF NOT EXISTS messages (
            session_id VARCHAR(255) NOT NULL COMMENT '会话标识',
            msg_id VARCHAR(255) NOT NULL COMMENT '会话内消息标识',
            created_at DATETIME NOT NULL COMMENT '消息创建时间（UTC）',
            payload JSON NOT NULL COMMENT 'AgentScope Msg JSON 负载',
            PRIMARY KEY (session_id, msg_id),
            KEY ix_messages_session_created (session_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope 持久化消息表'
    """),
    ("schedules", """
        CREATE TABLE IF NOT EXISTS schedules (
            user_id VARCHAR(255) NOT NULL COMMENT '计划任务所属用户标识',
            agent_id VARCHAR(255) NOT NULL COMMENT '执行 Agent 标识',
            id VARCHAR(255) NOT NULL COMMENT '计划任务唯一标识',
            created_at DATETIME NOT NULL COMMENT '创建时间（UTC）',
            updated_at DATETIME NOT NULL COMMENT '更新时间（UTC）',
            payload JSON NOT NULL COMMENT 'ScheduleRecord JSON 负载',
            PRIMARY KEY (id),
            KEY ix_schedules_user_id (user_id),
            KEY ix_schedules_agent_id (agent_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope Schedule 资源表'
    """),
    ("teams", """
        CREATE TABLE IF NOT EXISTS teams (
            user_id VARCHAR(255) NOT NULL COMMENT '团队所属用户标识',
            session_id VARCHAR(255) NOT NULL COMMENT '团队领导会话标识',
            id VARCHAR(255) NOT NULL COMMENT '团队唯一标识',
            created_at DATETIME NOT NULL COMMENT '创建时间（UTC）',
            updated_at DATETIME NOT NULL COMMENT '更新时间（UTC）',
            payload JSON NOT NULL COMMENT 'TeamRecord JSON 负载',
            PRIMARY KEY (id),
            KEY ix_teams_user_id (user_id),
            KEY ix_teams_session_id (session_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope Team 资源表'
    """),
    ("mcps", """
        CREATE TABLE IF NOT EXISTS mcps (
            user_id VARCHAR(255) NOT NULL COMMENT 'MCP 所属用户标识',
            name VARCHAR(255) NOT NULL COMMENT 'MCP 名称',
            id VARCHAR(255) NOT NULL COMMENT 'MCP 资源唯一标识',
            created_at DATETIME NOT NULL COMMENT '创建时间（UTC）',
            updated_at DATETIME NOT NULL COMMENT '更新时间（UTC）',
            payload JSON NOT NULL COMMENT 'MCPRecord JSON 负载，不含密钥',
            PRIMARY KEY (id),
            UNIQUE KEY uq_mcps_user_name (user_id, name),
            KEY ix_mcps_user_id (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope MCP 资源表'
    """),
    ("skills", """
        CREATE TABLE IF NOT EXISTS skills (
            user_id VARCHAR(255) NOT NULL COMMENT '技能所属用户标识',
            name VARCHAR(255) NOT NULL COMMENT '技能名称',
            id VARCHAR(255) NOT NULL COMMENT '技能资源唯一标识',
            created_at DATETIME NOT NULL COMMENT '创建时间（UTC）',
            updated_at DATETIME NOT NULL COMMENT '更新时间（UTC）',
            payload JSON NOT NULL COMMENT 'SkillRecord JSON 负载',
            PRIMARY KEY (id),
            UNIQUE KEY uq_skills_user_name (user_id, name),
            KEY ix_skills_user_id (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope Skill 资源表'
    """),
    ("hy_agentscope_tool_policy", """
        CREATE TABLE IF NOT EXISTS hy_agentscope_tool_policy (
            id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
            tool_name VARCHAR(128) NOT NULL COMMENT 'AgentScope 工具名称',
            audience VARCHAR(32) NOT NULL DEFAULT 'admin,farmer' COMMENT '可用用户范围',
            permission_code VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'RBAC 权限码',
            is_read_only TINYINT NOT NULL DEFAULT 0 COMMENT '是否只读工具',
            is_concurrency_safe TINYINT NOT NULL DEFAULT 0 COMMENT '是否允许并发执行',
            area_scoped TINYINT NOT NULL DEFAULT 0 COMMENT '是否需要农户产区作用域',
            schema_json JSON NULL COMMENT '工具 JSON Schema',
            status TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用，2停用',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            PRIMARY KEY (id),
            UNIQUE KEY uq_agentscope_tool_name (tool_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope 工具权限与并发策略表'
    """),
    ("hy_agentscope_model_card", """
        CREATE TABLE IF NOT EXISTS hy_agentscope_model_card (
            id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
            provider VARCHAR(64) NOT NULL COMMENT 'AgentScope Credential/Provider 类型',
            model_name VARCHAR(128) NOT NULL COMMENT '模型名称',
            label VARCHAR(128) NOT NULL DEFAULT '' COMMENT '模型展示名称',
            input_types JSON NOT NULL COMMENT '输入模态列表',
            output_types JSON NOT NULL COMMENT '输出能力列表',
            context_size INT NOT NULL DEFAULT 32768 COMMENT '上下文长度',
            output_size INT NOT NULL DEFAULT 4096 COMMENT '最大输出长度',
            parameter_schema JSON NOT NULL COMMENT '参数 Schema',
            status TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用，2停用',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            PRIMARY KEY (id),
            UNIQUE KEY uq_agentscope_model_provider_name (provider, model_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope ModelCard 目录'
    """),
    ("hy_agentscope_bus_entry", """
        CREATE TABLE IF NOT EXISTS hy_agentscope_bus_entry (
            id BIGINT NOT NULL AUTO_INCREMENT COMMENT '消息总线序列号',
            bus_key VARCHAR(255) NOT NULL COMMENT '消息总线逻辑键',
            mode VARCHAR(16) NOT NULL COMMENT '队列模式：queue/log/broadcast',
            payload JSON NOT NULL COMMENT '消息总线事件负载',
            expires_at DATETIME NULL COMMENT '过期时间',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '写入时间',
            PRIMARY KEY (id),
            KEY ix_agentscope_bus_key_id (bus_key, id),
            KEY ix_agentscope_bus_expire (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope MySQL 消息总线事件表'
    """),
    ("hy_agentscope_bus_lock", """
        CREATE TABLE IF NOT EXISTS hy_agentscope_bus_lock (
            lock_key VARCHAR(255) NOT NULL COMMENT '租约锁键',
            token VARCHAR(64) NOT NULL COMMENT '租约持有者令牌',
            expires_at DATETIME NOT NULL COMMENT '租约过期时间',
            PRIMARY KEY (lock_key),
            KEY ix_agentscope_lock_expire (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope MySQL 会话锁与租约表'
    """),
    ("hy_agentscope_bus_registry", """
        CREATE TABLE IF NOT EXISTS hy_agentscope_bus_registry (
            namespace VARCHAR(255) NOT NULL COMMENT '注册表命名空间',
            field_name VARCHAR(255) NOT NULL COMMENT '注册表字段',
            field_value TEXT NOT NULL COMMENT '注册表值',
            expires_at DATETIME NULL COMMENT '命名空间过期时间',
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            PRIMARY KEY (namespace, field_name),
            KEY ix_agentscope_registry_expire (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope MySQL 注册表'
    """),
]


async def _table_exists(db, table: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"
    ), {"table": table})
    return bool(result.scalar())


async def probe(db) -> bool:
    """所有 AgentScope 运行时表存在时视为迁移完成。"""
    return all([await _table_exists(db, name) for name, _ in _DDL])


async def apply(db):
    """按清单创建 AgentScope 资源、策略和消息总线表。"""
    for name, ddl in _DDL:
        await db.execute(text(ddl))
        logger.info("[AgentScope迁移] %s 表已就绪", name)
