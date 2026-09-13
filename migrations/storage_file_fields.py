# -*- coding: utf-8 -*-
"""为文件日志补充本地路径和对象键字段。"""

from sqlalchemy import text


async def _column_exists(db, column: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_file_log' AND COLUMN_NAME=:column"
    ), {"column": column})
    return bool(result.scalar())


async def _index_exists(db, name: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_file_log' AND INDEX_NAME=:name"
    ), {"name": name})
    return bool(result.scalar())


async def probe(db) -> bool:
    """新字段和索引齐备时视为已应用。"""
    return (
        await _column_exists(db, "local_path")
        and await _column_exists(db, "object_key")
        and await _column_exists(db, "local_path_hash")
        and await _index_exists(db, "uk_file_log_local_path")
    )


async def apply(db) -> None:
    """幂等补齐字段、允许私有上传空路径并建立公共路径唯一索引。"""
    if not await _column_exists(db, "local_path"):
        await db.execute(text(
            "ALTER TABLE hy_file_log ADD COLUMN local_path VARCHAR(512) NULL DEFAULT NULL "
            "COMMENT '本地 upload/ 下的相对路径' AFTER save_name"
        ))
    if not await _column_exists(db, "object_key"):
        await db.execute(text(
            "ALTER TABLE hy_file_log ADD COLUMN object_key VARCHAR(512) NOT NULL DEFAULT '' "
            "COMMENT '对象存储中的真实对象键' AFTER local_path"
        ))
    if not await _column_exists(db, "local_path_hash"):
        await db.execute(text(
            "ALTER TABLE hy_file_log ADD COLUMN local_path_hash VARCHAR(64) NULL DEFAULT NULL "
            "COMMENT '本地相对路径SHA-256摘要' AFTER local_path"
        ))
    if await _index_exists(db, "uk_file_log_save_name"):
        await db.execute(text("ALTER TABLE hy_file_log DROP INDEX uk_file_log_save_name"))
    if not await _index_exists(db, "idx_file_log_save_name"):
        await db.execute(text("ALTER TABLE hy_file_log ADD INDEX idx_file_log_save_name (save_name)"))
    # 旧版本字段可能为 NOT NULL；先允许 NULL，再将空路径转换为空值。
    await db.execute(text(
        "ALTER TABLE hy_file_log MODIFY COLUMN local_path VARCHAR(512) NULL DEFAULT NULL "
        "COMMENT '本地 upload/ 下的相对路径'"
    ))
    await db.execute(text("UPDATE hy_file_log SET local_path=NULL WHERE local_path=''"))
    await db.execute(text(
        "UPDATE hy_file_log SET local_path_hash=NULL WHERE local_path IS NULL"
    ))
    await db.execute(text(
        "UPDATE hy_file_log older JOIN hy_file_log newer "
        "ON older.local_path=newer.local_path AND older.id<newer.id "
        "SET older.local_path=NULL, older.local_path_hash=NULL "
        "WHERE older.local_path IS NOT NULL"
    ))
    await db.execute(text(
        "UPDATE hy_file_log SET local_path_hash=SHA2(local_path, 256) "
        "WHERE local_path IS NOT NULL"
    ))
    if await _index_exists(db, "idx_file_log_local_path"):
        await db.execute(text("ALTER TABLE hy_file_log DROP INDEX idx_file_log_local_path"))
    # 旧索引直接使用 VARCHAR(512)，在 utf8mb4 和旧 InnoDB 组合下会超过 1000 字节上限。
    if await _index_exists(db, "uk_file_log_local_path"):
        await db.execute(text("ALTER TABLE hy_file_log DROP INDEX uk_file_log_local_path"))
    await db.execute(text(
        "ALTER TABLE hy_file_log ADD UNIQUE INDEX uk_file_log_local_path (local_path_hash)"
    ))
