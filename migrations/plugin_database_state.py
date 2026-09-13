"""插件数据库体检状态表迁移。"""

from sqlalchemy import text


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS hy_plugin_database_state (
    plugin_name VARCHAR(64) NOT NULL COMMENT '插件唯一标识',
    module VARCHAR(32) NOT NULL DEFAULT '' COMMENT '插件模块',
    title VARCHAR(128) NOT NULL DEFAULT '' COMMENT '插件显示名称',
    db_version VARCHAR(32) NOT NULL DEFAULT '' COMMENT '数据库登记插件版本',
    disk_version VARCHAR(32) NOT NULL DEFAULT '' COMMENT '磁盘 plugin.json 版本',
    status VARCHAR(32) NOT NULL DEFAULT 'unverified' COMMENT '总体状态',
    schema_status VARCHAR(32) NOT NULL DEFAULT 'unverified' COMMENT '结构状态',
    report_json TEXT NOT NULL COMMENT '体检报告 JSON',
    expected_schema_digest VARCHAR(64) NOT NULL DEFAULT '' COMMENT '声明式结构摘要',
    repair_supported TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否提供数据库修复钩子',
    pending_operation_id VARCHAR(64) NOT NULL DEFAULT '' COMMENT '待重启计划编号',
    last_scan_at DATETIME NULL COMMENT '最近扫描时间',
    last_success_at DATETIME NULL COMMENT '最近成功扫描时间',
    last_repair_at DATETIME NULL COMMENT '最近修复成功时间',
    last_failure_at DATETIME NULL COMMENT '最近失败时间',
    error_reason VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '最近失败原因',
    PRIMARY KEY (plugin_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='插件数据库体检最新状态表'
"""


async def probe(db) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_plugin_database_state'"
    ))
    return bool(result.scalar())


async def apply(db) -> None:
    await db.execute(text(TABLE_SQL))
