"""慧眼设备私有存储，供外部 API、管理员 API 和本地适配器复用。"""

import json
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from core.db.base import async_session_factory
from core.hardware_management import delete_hardware_mirror
from core.hardware_sync import hardware_discovery_guard
from core.hardware_provider import hardware_provider_registry
from core.time_utils import china_now, CHINA_TIMEZONE
from core.hardware_types import HardwareMetric
from .schemas import DeviceRegisterRequest, DeviceReportRequest


def _serialize(row) -> dict:
    result = dict(row)
    for key in ("capabilities", "metadata"):
        if isinstance(result[key], str):
            result[key] = json.loads(result[key])
    for key in ("last_seen", "create_time", "update_time"):
        if result.get(key):
            result[key] = result[key].replace(tzinfo=CHINA_TIMEZONE).isoformat(timespec="seconds")
    return result


async def list_registered_devices() -> list[dict]:
    async with async_session_factory() as db:
        rows = (await db.execute(text("SELECT * FROM hy_huiyan_iot_device ORDER BY id"))).mappings().all()
    return [_serialize(row) for row in rows]


async def read_registered_device(device_id: str) -> dict:
    async with async_session_factory() as db:
        row = (await db.execute(text("SELECT * FROM hy_huiyan_iot_device WHERE device_id=:device_id"),
                                {"device_id": device_id})).mappings().first()
    if not row:
        raise HTTPException(404, "设备不存在")
    return _serialize(row)


async def register_device(data: DeviceRegisterRequest, *, replace_existing: bool = True) -> dict:
    """原子插入或更新；管理员表单不覆盖设备已有的扩展指标与申报能力。"""
    connection = await hardware_provider_registry.connect("hardware_huiyan")
    values = data.model_dump(exclude={"replace_existing"})
    now = china_now()
    values.update(capabilities=json.dumps(values["capabilities"], ensure_ascii=False),
                  metadata=json.dumps(values["metadata"], ensure_ascii=False),
                  last_seen=None, create_time=now, update_time=now)
    fields = tuple(values)
    sql = f"INSERT INTO hy_huiyan_iot_device ({', '.join(fields)}) VALUES ({', '.join(':' + key for key in fields)})"
    if replace_existing:
        updates = [key for key in fields if key in data.model_fields_set and key != "device_id"]
        updates.append("update_time")
        sql += " ON DUPLICATE KEY UPDATE " + ", ".join(f"{key}=VALUES({key})" for key in updates)
    async with connection.commit_guard(), async_session_factory() as db:
        try:
            await db.execute(text(sql), values)
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            if getattr(exc.orig, "args", (None,))[0] != 1062:
                raise
            raise HTTPException(409, "设备编号已注册，请确认后更新基础信息") from exc
    return {"device_id": data.device_id, "registered": True}


def _merge_metrics(existing: list[dict], data: DeviceReportRequest, received_at: str) -> list[dict]:
    """逐指标保留属性和采集时间，迟到数据不能覆盖更新的值；同时间以后提交为准。"""
    merged = {row["identifier"]: row for row in existing}
    for metric in data.identifiers:
        previous = merged.get(metric.identifier, {})
        observed_at = metric.last_update_time or received_at
        previous_time = previous.get("lastUpdateTime")
        if previous_time and datetime.fromisoformat(observed_at) < datetime.fromisoformat(previous_time):
            continue
        incoming = metric.model_dump(by_alias=True, exclude_unset=True)
        incoming["lastUpdateTime"] = observed_at
        merged[metric.identifier] = HardwareMetric.model_validate({**previous, **incoming}).model_dump(by_alias=True)
    return list(merged.values())


async def report_device_data(device_id: str, data: DeviceReportRequest) -> dict:
    """行锁内合并私有表指标，避免传感器并发上报覆盖彼此；不改公共镜像或绑定。"""
    connection = await hardware_provider_registry.connect("hardware_huiyan")
    async with connection.commit_guard(), async_session_factory() as db:
        row = (await db.execute(text("SELECT metadata FROM hy_huiyan_iot_device WHERE device_id=:device_id FOR UPDATE"),
                                {"device_id": device_id})).mappings().first()
        if not row:
            raise HTTPException(404, "设备不存在")
        now = china_now()
        received_at = now.replace(tzinfo=CHINA_TIMEZONE).isoformat(timespec="seconds")
        metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"])
        metadata["identifiers"] = _merge_metrics(metadata.get("identifiers", []), data, received_at)
        await db.execute(text("UPDATE hy_huiyan_iot_device SET metadata=:metadata, last_seen=:now, update_time=:now "
                              "WHERE device_id=:device_id"),
                         {"device_id": device_id, "metadata": json.dumps(metadata, ensure_ascii=False), "now": now})
        await db.commit()
    return {"device_id": device_id, "identifiers": metadata["identifiers"], "last_seen": received_at}


async def read_device_realtime(device_id: str) -> dict:
    """公开查询直接读取最近上报数据，不经过后台的周期采集快照。"""
    row = await read_registered_device(device_id)
    return {"device_id": device_id, "identifiers": row["metadata"].get("identifiers", []), "last_seen": row["last_seen"]}


async def delete_registered_device(device_id: str) -> dict:
    """删除注册和镜像必须原子提交；与同步串行，避免删除前的发现结果晚到重建镜像。"""
    async with hardware_discovery_guard("hardware_huiyan"):
        connection = await hardware_provider_registry.connect("hardware_huiyan")
        async with connection.commit_guard(), async_session_factory() as db, db.begin():
            # 插件自行检查私有表，不让平台公共门面依赖插件表名。
            engine = await db.scalar(text(
                "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                "AND TABLE_NAME='hy_huiyan_iot_device'"
            ))
            if not engine or engine.lower() != "innodb":
                raise HTTPException(409, "设备数据表不支持安全事务删除，请先停机备份并转换为 InnoDB")
            row = (await db.execute(text(
                "SELECT device_id FROM hy_huiyan_iot_device WHERE device_id=:device_id FOR UPDATE"
            ), {"device_id": device_id})).first()
            if row is None:
                raise HTTPException(404, "设备不存在")
            await delete_hardware_mirror(db, "hardware_huiyan", device_id)
            await db.execute(text("DELETE FROM hy_huiyan_iot_device WHERE device_id=:device_id"),
                             {"device_id": device_id})
    return {"device_id": device_id, "deleted": True}
