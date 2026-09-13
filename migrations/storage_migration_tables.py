# -*- coding: utf-8 -*-
"""为存量数据库补齐公共文件迁移任务表。"""

from sqlalchemy import text


async def _column_exists(db, column: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_storage_migration_item' "
        "AND COLUMN_NAME=:column"
    ), {"column": column})
    return bool(result.scalar())


async def _index_exists(db, name: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_storage_migration_item' "
        "AND INDEX_NAME=:name"
    ), {"name": name})
    return bool(result.scalar())


async def probe(db) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME IN ('hy_storage_migration_job','hy_storage_migration_item')"
    ))
    return (
        int(result.scalar() or 0) == 2
        and await _column_exists(db, "path_hash")
        and await _index_exists(db, "uk_storage_migration_item_path")
    )


async def apply(db) -> None:
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_storage_migration_job ("
        "id BIGINT NOT NULL AUTO_INCREMENT COMMENT '迁移任务ID',"
        "target_method VARCHAR(64) NOT NULL COMMENT '目标存储插件标识',"
        "phase VARCHAR(24) NOT NULL DEFAULT 'scanning' COMMENT '迁移阶段（含 empty 无待同步文件）',"
        "total_files INT NOT NULL DEFAULT 0 COMMENT '文件总数',"
        "total_bytes BIGINT NOT NULL DEFAULT 0 COMMENT '文件总字节数',"
        "processed_files INT NOT NULL DEFAULT 0 COMMENT '已处理文件数',"
        "processed_bytes BIGINT NOT NULL DEFAULT 0 COMMENT '已处理字节数',"
        "success_files INT NOT NULL DEFAULT 0 COMMENT '成功文件数',"
        "failed_files INT NOT NULL DEFAULT 0 COMMENT '失败文件数',"
        "error_msg TEXT NOT NULL COMMENT '任务错误信息',"
        "task_id BIGINT NULL COMMENT '任务队列ID',"
        "create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',"
        "PRIMARY KEY (id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='公共上传文件对象存储迁移任务'"
    ))
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_storage_migration_item ("
        "id BIGINT NOT NULL AUTO_INCREMENT COMMENT '明细ID',"
        "job_id BIGINT NOT NULL COMMENT '迁移任务ID',"
        "local_path VARCHAR(512) NOT NULL COMMENT 'upload/下相对路径',"
        "path_hash VARCHAR(64) NOT NULL COMMENT '本地相对路径SHA-256摘要',"
        "object_key VARCHAR(512) NOT NULL COMMENT '目标对象键',"
        "file_size BIGINT NOT NULL DEFAULT 0 COMMENT '文件大小',"
        "mtime_ns BIGINT NOT NULL DEFAULT 0 COMMENT '扫描时文件修改时间',"
        "status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '明细状态',"
        "attempts INT NOT NULL DEFAULT 0 COMMENT '尝试次数',"
        "error_msg TEXT NOT NULL COMMENT '失败原因',"
        "update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',"
        "PRIMARY KEY (id), UNIQUE KEY uk_storage_migration_item_path (job_id, path_hash),"
        "KEY idx_storage_migration_item_job_status (job_id, status)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='公共上传文件对象存储迁移明细'"
    ))
    if not await _column_exists(db, "path_hash"):
        await db.execute(text(
            "ALTER TABLE hy_storage_migration_item ADD COLUMN path_hash VARCHAR(64) NULL "
            "COMMENT '本地相对路径SHA-256摘要' AFTER local_path"
        ))
    await db.execute(text(
        "UPDATE hy_storage_migration_item SET path_hash=SHA2(local_path, 256) "
        "WHERE path_hash IS NULL OR path_hash=''"
    ))
    if await _index_exists(db, "uk_storage_migration_item_path"):
        await db.execute(text(
            "ALTER TABLE hy_storage_migration_item DROP INDEX uk_storage_migration_item_path"
        ))
    await db.execute(text(
        "ALTER TABLE hy_storage_migration_item MODIFY COLUMN path_hash VARCHAR(64) NOT NULL "
        "COMMENT '本地相对路径SHA-256摘要', "
        "ADD UNIQUE INDEX uk_storage_migration_item_path (job_id, path_hash)"
    ))
