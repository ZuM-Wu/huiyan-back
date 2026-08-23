# -*- coding: utf-8 -*-
"""任务平台与可靠事件 Outbox 的幂等迁移。"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

_QUEUE_COLUMNS = {
    "owner": "VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '任务定义所有者'",
    "definition": "VARCHAR(128) NOT NULL DEFAULT '' COMMENT '任务定义名称'",
    "group_name": "VARCHAR(64) NOT NULL DEFAULT 'default' COMMENT '并发隔离分组'",
    "attempt": "INT NOT NULL DEFAULT 0 COMMENT '已执行尝试次数'",
    "max_attempts": "INT NOT NULL DEFAULT 3 COMMENT '最大执行尝试次数'",
    "idempotency_key": "VARCHAR(191) NULL COMMENT '提交方显式幂等键'",
    "idempotency_digest": (
        "BINARY(32) GENERATED ALWAYS AS "
        "(IF(idempotency_key IS NULL, NULL, "
        "UNHEX(SHA2(CONCAT(definition, CHAR(0), idempotency_key), 256)))) STORED "
        "COMMENT '任务定义与幂等键SHA-256摘要'"
    ),
    "correlation_id": "VARCHAR(128) NOT NULL DEFAULT '' COMMENT '业务关联标识'",
    "event_id": "BIGINT NULL COMMENT '可靠事件ID'",
    "next_run_at": "DATETIME NULL COMMENT '下次允许执行时间'",
    "locked_at": "DATETIME NULL COMMENT '最近抢占时间'",
}

_LOG_COLUMNS = {
    "task_id": "BIGINT NULL COMMENT '队列任务ID'",
    "owner": "VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '任务定义所有者'",
    "definition": "VARCHAR(128) NOT NULL DEFAULT '' COMMENT '任务定义名称'",
    "attempt": "INT NOT NULL DEFAULT 1 COMMENT '本次执行尝试序号'",
    "correlation_id": "VARCHAR(128) NOT NULL DEFAULT '' COMMENT '业务关联标识'",
    "event_id": "BIGINT NULL COMMENT '可靠事件ID'",
}

# Outbox 只能与事务表形成原子提交。这里仅校准已经接入可靠事件的业务表，
# 不借任务平台迁移扩大到尚未接入 Outbox 的历史表。
_OUTBOX_TRANSACTION_TABLES = (
    "hy_task_queue",
    "hy_task_log",
    "hy_configuration",
    "hy_farmer",
    "hy_certification_record",
    "hy_area_farmer",
    "hy_plot",
    "hy_planting_batch",
    "hy_weather_alert",
)


async def _table_exists(db, table_name: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table_name"
    ), {"table_name": table_name})
    return bool(result.scalar())


async def _column_exists(db, table_name: str, column_name: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table_name "
        "AND COLUMN_NAME=:column_name"
    ), {"table_name": table_name, "column_name": column_name})
    return bool(result.scalar())


async def _index_exists(db, table_name: str, index_name: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table_name "
        "AND INDEX_NAME=:index_name"
    ), {"table_name": table_name, "index_name": index_name})
    return bool(result.scalar())


async def _ensure_innodb(db) -> None:
    """保证业务写入、任务状态和 Outbox 具备真实事务语义。"""
    for table_name in _OUTBOX_TRANSACTION_TABLES:
        if not await _table_exists(db, table_name):
            continue
        result = await db.execute(text(
            "SELECT ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table_name"
        ), {"table_name": table_name})
        if str(result.scalar() or "").upper() != "INNODB":
            # 历史 MyISAM 表可能带 ROW_FORMAT=FIXED；该选项不能直接沿用到
            # 含 varchar 的 InnoDB 临时表，因此转换时显式校准为 DYNAMIC。
            await db.execute(text(
                f"ALTER TABLE {table_name} ENGINE=InnoDB, ROW_FORMAT=DYNAMIC"
            ))


async def _add_columns(db, table_name: str, columns: dict[str, str]) -> None:
    for name, ddl in columns.items():
        if not await _column_exists(db, table_name, name):
            await db.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))


async def _create_queue(db) -> None:
    if await _table_exists(db, "hy_task_queue"):
        return
    await db.execute(text("""
        CREATE TABLE hy_task_queue (
            id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '任务ID',
            type VARCHAR(64) NOT NULL COMMENT '历史任务类型',
            status VARCHAR(20) NOT NULL DEFAULT 'Wait' COMMENT '任务状态',
            priority INT DEFAULT 0 COMMENT '优先级', retry INT DEFAULT 0 COMMENT '历史重试次数',
            max_retry INT DEFAULT 3 COMMENT '历史最大重试次数', task_data TEXT NOT NULL COMMENT '任务数据JSON',
            description VARCHAR(500) DEFAULT '' COMMENT '任务描述', version INT DEFAULT 0 COMMENT '乐观锁版本',
            error_msg TEXT COMMENT '错误信息', start_time DATETIME COMMENT '开始时间',
            finish_time DATETIME COMMENT '完成时间', run_at DATETIME COMMENT '历史计划执行时间',
            owner VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '任务定义所有者',
            definition VARCHAR(128) NOT NULL COMMENT '任务定义名称',
            group_name VARCHAR(64) NOT NULL DEFAULT 'default' COMMENT '并发隔离分组',
            attempt INT NOT NULL DEFAULT 0 COMMENT '已执行尝试次数',
            max_attempts INT NOT NULL DEFAULT 3 COMMENT '最大执行尝试次数',
            idempotency_key VARCHAR(191) NULL COMMENT '提交方显式幂等键',
            idempotency_digest BINARY(32) GENERATED ALWAYS AS
                (IF(idempotency_key IS NULL, NULL,
                UNHEX(SHA2(CONCAT(definition, CHAR(0), idempotency_key), 256)))) STORED
                COMMENT '任务定义与幂等键SHA-256摘要',
            correlation_id VARCHAR(128) NOT NULL DEFAULT '' COMMENT '业务关联标识',
            event_id BIGINT NULL COMMENT '可靠事件ID', next_run_at DATETIME NULL COMMENT '下次允许执行时间',
            locked_at DATETIME NULL COMMENT '最近抢占时间', create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            INDEX idx_queue_claim (status, next_run_at, priority, id),
            INDEX idx_queue_owner (owner, status), UNIQUE KEY uq_task_idempotency (idempotency_digest)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='任务队列表'
    """))


async def _create_outbox(db) -> None:
    if await _table_exists(db, "hy_event_outbox"):
        return
    await db.execute(text("""
        CREATE TABLE hy_event_outbox (
            id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '事件ID',
            event_name VARCHAR(128) NOT NULL COMMENT '事件名称', event_version INT NOT NULL DEFAULT 1 COMMENT '事件契约版本',
            owner VARCHAR(64) NOT NULL COMMENT '事件发布方', payload TEXT NOT NULL COMMENT '已校验事件数据JSON',
            correlation_id VARCHAR(128) NOT NULL DEFAULT '' COMMENT '业务关联标识',
            status VARCHAR(20) NOT NULL DEFAULT 'Pending' COMMENT '状态: Pending/Dispatched',
            dispatched_at DATETIME NULL COMMENT '分发完成时间', create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            INDEX idx_event_outbox_status (status, id), INDEX idx_event_outbox_name (event_name, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='可靠业务事件Outbox表'
    """))


async def _migrate_existing_rows(db) -> None:
    await db.execute(text("""
        UPDATE hy_task_queue SET
            definition=IF(definition='', type, definition),
            owner=CASE
                WHEN type='push_execute' THEN 'push'
                WHEN type='wecom_notice' THEN 'wecom_webhook'
                WHEN type LIKE 'weather_%' THEN 'weather'
                ELSE 'system' END,
            group_name=CASE
                WHEN type='notice' THEN 'notice'
                WHEN type='push_execute' THEN 'push'
                WHEN type='wecom_notice' THEN 'wecom_webhook'
                WHEN type LIKE 'weather_%' THEN 'weather'
                ELSE 'system' END,
            attempt=retry,
            max_attempts=GREATEST(max_retry + 1, 1),
            next_run_at=COALESCE(next_run_at, run_at, create_time)
    """))
    known = (
        "'notice','clean_repeat_cache','clean_old_logs','clean_task_logs',"
        "'sweep_expired_cache','weather_pull','weather_daily_finalize',"
        "'weather_clean','weather_alert_notify','push_execute','wecom_notice'"
    )
    await db.execute(text(
        "UPDATE hy_task_queue SET status='Dead', "
        "error_msg=CASE WHEN type='hook' THEN '迁移终止：旧 hook 任务不再支持' "
        "ELSE CONCAT('迁移终止：未知任务定义 ', type) END "
        f"WHERE status IN ('Wait','Exec','Failed') AND (type='hook' OR type NOT IN ({known}))"
    ))
    await db.execute(text(
        "UPDATE hy_task_queue SET "
        "status=CASE WHEN attempt < max_attempts THEN 'Wait' ELSE 'Dead' END, "
        "next_run_at=CASE WHEN attempt < max_attempts THEN NOW() ELSE next_run_at END, "
        "error_msg=CASE WHEN attempt >= max_attempts "
        "THEN CONCAT('迁移终止：历史失败任务已耗尽尝试次数。', COALESCE(error_msg, '')) "
        "ELSE error_msg END "
        f"WHERE status='Failed' AND type IN ({known})"
    ))


async def migrate(db) -> None:
    """幂等升级任务队列、执行日志及 Outbox。"""
    await _create_queue(db)
    await _add_columns(db, "hy_task_queue", _QUEUE_COLUMNS)
    if await _table_exists(db, "hy_task_log"):
        await _add_columns(db, "hy_task_log", _LOG_COLUMNS)
    await _create_outbox(db)
    await _migrate_existing_rows(db)
    if not await _index_exists(db, "hy_task_queue", "idx_queue_claim"):
        await db.execute(text("CREATE INDEX idx_queue_claim ON hy_task_queue(status, next_run_at, priority, id)"))
    if not await _index_exists(db, "hy_task_queue", "idx_queue_owner"):
        await db.execute(text("CREATE INDEX idx_queue_owner ON hy_task_queue(owner, status)"))
    if not await _index_exists(db, "hy_task_queue", "uq_task_idempotency"):
        # 旧版本没有数据库唯一约束，并发提交可能留下重复键。保留最早一条的
        # 幂等语义，其余任务与历史不删除，仅清空重复键后再建立摘要唯一索引。
        await db.execute(text("""
            UPDATE hy_task_queue AS task
            JOIN (
                SELECT definition, idempotency_key, MIN(id) AS keep_id
                FROM hy_task_queue
                WHERE idempotency_key IS NOT NULL
                GROUP BY definition, idempotency_key
                HAVING COUNT(*) > 1
            ) AS duplicate_keys
              ON duplicate_keys.definition = task.definition
             AND duplicate_keys.idempotency_key = task.idempotency_key
            SET task.idempotency_key = NULL
            WHERE task.id <> duplicate_keys.keep_id
        """))
        await db.execute(text(
            "CREATE UNIQUE INDEX uq_task_idempotency "
            "ON hy_task_queue(idempotency_digest)"
        ))
    await _ensure_innodb(db)
    await db.commit()
    logger.info("[迁移] 任务平台与可靠事件 Outbox 已校准")
