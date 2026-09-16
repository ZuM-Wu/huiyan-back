# -*- coding: utf-8 -*-
"""把具备明文事务语义要求的表校准为 InnoDB。

覆盖范围与依据：
- `hy_event_outbox`：可靠事件 Outbox 必须与业务写入同事务原子提交，MyISAM 无事务与行锁；
- `hy_huiyan_iot_device`、`hy_hardware_device`、`hy_hardware_realtime_snapshot`：
  接口文档要求「三张设备数据表必须为 InnoDB」，删除设备时在同一事务内清理注册记录、
  公共镜像与实时快照。

成因：启动流程先 `Base.metadata.create_all`，模型未声明引擎时按服务器默认引擎落库
（本机默认 MyISAM），随后 DDL 迁移中的 `ENGINE=InnoDB` 因表已存在而失效。
本迁移只收敛引擎与缺失外键，不改写任何数据行；表不存在时跳过，交由插件建表脚本按
`ENGINE=InnoDB` 创建。
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

_REQUIRED_INNODB_TABLES = (
    "hy_event_outbox",
    "hy_huiyan_iot_device",
    "hy_hardware_device",
    "hy_hardware_realtime_snapshot",
)
_SNAPSHOT_TABLE = "hy_hardware_realtime_snapshot"
_SNAPSHOT_FK = "fk_hardware_realtime_device"


async def _table_engine(db, table: str) -> str | None:
    """读取表引擎；表不存在时返回 None。"""
    result = await db.execute(text(
        "SELECT ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t"
    ), {"t": table})
    return result.scalar_one_or_none()


async def _snapshot_fk_exists(db) -> bool:
    """实时快照表是否已存在设备外键。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=:t "
        "AND CONSTRAINT_NAME=:c AND CONSTRAINT_TYPE='FOREIGN KEY'"
    ), {"t": _SNAPSHOT_TABLE, "c": _SNAPSHOT_FK})
    return bool(result.scalar())


async def _snapshot_orphan_rows(db) -> int:
    """统计指向不存在设备的孤儿快照行，用于判断能否安全建立外键。"""
    result = await db.execute(text(
        f"SELECT COUNT(*) FROM {_SNAPSHOT_TABLE} snapshot "
        "LEFT JOIN hy_hardware_device device ON device.id = snapshot.device_id "
        "WHERE device.id IS NULL"
    ))
    return int(result.scalar() or 0)


async def probe(db) -> bool:
    """已存在的目标表全部为 InnoDB，且快照外键已就绪或无法建立时视为完成。"""
    for table in _REQUIRED_INNODB_TABLES:
        engine = await _table_engine(db, table)
        if engine and engine.lower() != "innodb":
            return False
    if not await _table_engine(db, _SNAPSHOT_TABLE):
        return True
    if await _snapshot_fk_exists(db):
        return True
    # 存在孤儿行时外键无法建立，属可满足的终态，避免每次启动重复执行补偿迁移。
    return await _snapshot_orphan_rows(db) > 0


async def apply(db):
    """逐表转换引擎并补齐快照外键（幂等，可重复执行）。"""
    for table in _REQUIRED_INNODB_TABLES:
        engine = await _table_engine(db, table)
        if not engine or engine.lower() == "innodb":
            continue
        await db.execute(text(f"ALTER TABLE {table} ENGINE=InnoDB"))
        logger.info("[迁移] %s 已由 %s 转换为 InnoDB", table, engine)
    if not await _table_engine(db, _SNAPSHOT_TABLE) or await _snapshot_fk_exists(db):
        return
    orphans = await _snapshot_orphan_rows(db)
    if orphans:
        logger.warning("[迁移] 实时快照存在 %d 条孤儿行，跳过外键创建", orphans)
        return
    await db.execute(text(
        f"ALTER TABLE {_SNAPSHOT_TABLE} ADD CONSTRAINT {_SNAPSHOT_FK} "
        "FOREIGN KEY (device_id) REFERENCES hy_hardware_device(id) ON DELETE CASCADE"
    ))
    logger.info("[迁移] 实时快照外键 %s 已创建", _SNAPSHOT_FK)
