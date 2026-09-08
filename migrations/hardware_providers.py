"""硬件来源结构的停机迁移；不接入 Web lifespan 的自动迁移列表。"""

import re

from sqlalchemy import text

MIGRATION_NAME = "hardware_providers_v1"


async def inspect_schema(db) -> dict:
    """只读探测可恢复的迁移阶段，MySQL DDL 自动提交不能伪装成可回滚事务。"""
    rows = (await db.execute(text(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_hardware_device'"
    ))).scalars().all()
    columns = set(rows)
    if not columns:
        raise RuntimeError("硬件表尚未初始化，请先完成基础数据库安装")
    indexes = (await db.execute(text(
        "SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns_list "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='hy_hardware_device' AND NON_UNIQUE=0 GROUP BY INDEX_NAME"
    ))).all()
    count = int(await db.scalar(text("SELECT COUNT(*) FROM hy_hardware_device")) or 0)
    return {"columns": sorted(columns), "indexes": {row[0]: row[1] for row in indexes}, "device_count": count}


async def apply_schema(db) -> None:
    """每个 DDL 均由真实结构探测保护，中断后可从已完成阶段继续。"""
    state = await inspect_schema(db)
    fields = {
        "provider_id": "VARCHAR(64) NOT NULL DEFAULT 'hardware_jjr' COMMENT '硬件来源插件标识'",
        "provider_device_id": "VARCHAR(128) COLLATE utf8mb4_bin NOT NULL DEFAULT '' COMMENT '来源内稳定设备ID'",
        "provider_title": "VARCHAR(128) NOT NULL DEFAULT 'JJR' COMMENT '来源名称快照'",
        "capabilities": "JSON NULL COMMENT '设备支持的操作能力'",
    }
    for name, definition in fields.items():
        if name not in state["columns"]:
            await db.execute(text(f"ALTER TABLE hy_hardware_device ADD COLUMN {name} {definition}"))
    await db.execute(text(
        "UPDATE hy_hardware_device SET provider_device_id=device_name, "
        "capabilities=IF(device_type='growth', JSON_ARRAY('realtime','history','take_photo'), "
        "JSON_ARRAY('realtime','history')) WHERE provider_id='hardware_jjr' AND provider_device_id=''"
    ))
    await db.commit()
    # 先建立来源唯一键，再删除旧唯一键，避免中断时出现完全无唯一保护的窗口。
    if "uk_hardware_provider_device" not in state["indexes"]:
        await db.execute(text(
            "ALTER TABLE hy_hardware_device ADD UNIQUE KEY uk_hardware_provider_device(provider_id,provider_device_id)"
        ))
    for name, columns in state["indexes"].items():
        if columns == "device_name":
            if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
                raise RuntimeError("旧设备唯一索引名称异常，请人工核验")
            await db.execute(text(f"ALTER TABLE hy_hardware_device DROP INDEX `{name}`"))
    await db.commit()


async def migration_complete(db) -> bool:
    exists = await db.scalar(text(
        "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='hy_schema_version'"
    ))
    if not exists:
        return False
    return bool(await db.scalar(text("SELECT COUNT(*) FROM hy_schema_version WHERE name=:name"), {"name": MIGRATION_NAME}))


async def mark_complete(db) -> None:
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_schema_version (name VARCHAR(128) PRIMARY KEY COMMENT '迁移名称', "
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '应用时间') "
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='迁移版本登记表'"
    ))
    await db.execute(text("INSERT IGNORE INTO hy_schema_version(name) VALUES (:name)"), {"name": MIGRATION_NAME})
    await db.commit()
