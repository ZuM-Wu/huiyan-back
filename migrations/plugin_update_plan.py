# -*- coding: utf-8 -*-
"""创建插件更新计划表，支持跨后端重启保留待生效状态。"""

from sqlalchemy import text


async def probe(db) -> bool:
    """目标表已存在即视为迁移完成。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_plugin_update_plan'"
    ))
    return bool(result.scalar())


async def apply(db) -> None:
    """幂等创建更新计划表及必要索引。"""
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_plugin_update_plan ("
        " operation_id VARCHAR(64) NOT NULL COMMENT '更新操作唯一标识',"
        " plugin_name VARCHAR(64) NOT NULL COMMENT '插件唯一标识',"
        " current_version VARCHAR(32) NOT NULL COMMENT '确认时的当前运行版本',"
        " target_version VARCHAR(32) NOT NULL COMMENT '待应用目标版本',"
        " package_ref VARCHAR(512) NOT NULL DEFAULT '' COMMENT '规范化暂存插件包路径',"
        " package_digest VARCHAR(64) NOT NULL DEFAULT '' COMMENT '暂存插件包 SHA-256 摘要',"
        " package_module VARCHAR(32) NOT NULL DEFAULT '' COMMENT '插件所属模块目录',"
        " status VARCHAR(32) NOT NULL COMMENT '计划状态: prepared/awaiting_restart/applying/applied/cancelled/failed',"
        " restart_required TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否必须重启后生效',"
        " created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '计划创建时间',"
        " confirmed_at DATETIME NULL COMMENT '计划确认时间',"
        " applied_at DATETIME NULL COMMENT '计划应用完成时间',"
        " error_reason VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '失败原因',"
        " identity VARCHAR(128) NOT NULL DEFAULT 'system' COMMENT '创建计划的身份',"
        " confirmed_by VARCHAR(128) NOT NULL DEFAULT '' COMMENT '确认计划的身份',"
        " task_id VARCHAR(64) NULL COMMENT '预检任务编号',"
        " PRIMARY KEY (operation_id),"
        " KEY idx_plugin_update_plan_plugin_status (plugin_name, status),"
        " KEY idx_plugin_update_plan_created_at (created_at)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='插件更新计划表'"
    ))
