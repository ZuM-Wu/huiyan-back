"""硬件插件管理配置门面：固定 owner、掩码读取与空凭据保留。"""

from urllib.parse import urlsplit
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from core.db.hardware_device import HardwareDevice, HardwareRealtimeSnapshot
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from core.config_manager import ConfigManager
from core.db.base import async_session_factory

_TOKEN_KEYS = {"hardware_jjr": "owner_token"}


class HardwareConfigUpdate(BaseModel):
    """管理页使用统一凭据输入名，不允许提交任意配置键。"""

    model_config = ConfigDict(extra="forbid")
    base_url: str | None = Field(default=None, max_length=512, description="JJR 平台地址")
    credential: str = Field(default="", max_length=2048, description="新凭据，留空保持原值")


async def read_hardware_config(owner: str) -> dict:
    """仅返回凭据是否已配置；不把平台访问凭据交给浏览器。"""
    if owner == "hardware_huiyan":
        return {"auth_required": False, "credential_configured": False}
    token_key = _TOKEN_KEYS[owner]
    async with async_session_factory() as db:
        config = await ConfigManager().get_plugin_config(owner, db)
    result: dict = {"credential_configured": bool(config.get(token_key))}
    if owner == "hardware_jjr":
        result["base_url"] = config.get("base_url") or "https://farmbot-jjr.jjr.vip"
    return result


async def save_hardware_config(owner: str, data: HardwareConfigUpdate) -> dict:
    """先完成字段校验再写入；慧眼本地适配器不接受自身 HTTP 地址。"""
    if owner == "hardware_huiyan":
        if data.base_url is not None or data.credential:
            raise HTTPException(422, "慧眼学习测试设备接口无需凭据或平台地址")
        return await read_hardware_config(owner)
    token_key = _TOKEN_KEYS[owner]
    values = {}
    if data.base_url is not None:
        parsed = urlsplit(data.base_url.strip())
        if owner != "hardware_jjr" or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(422, "平台地址必须是无凭据、查询参数和片段的 HTTP(S) 地址")
        values["base_url"] = data.base_url.strip().rstrip("/")
    if data.credential.strip():
        values[token_key] = data.credential.strip()
    async with async_session_factory() as db:
        for key, value in values.items():
            await ConfigManager().set(f"{owner}.{key}", value, db, description="物联网插件配置")
    return await read_hardware_config(owner)


async def delete_hardware_mirror(db: AsyncSession, owner: str, provider_device_id: str) -> bool:
    """在调用方事务内删除未绑定镜像；调用方须持有发现锁和来源提交门禁，不在此提交。"""
    # 旧安装可能继承 MyISAM；不能在无事务/行锁保证的表上执行不可回滚删除。
    engines = (await db.execute(text(
        "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME IN ('hy_hardware_device', 'hy_hardware_realtime_snapshot')"
    ))).scalars().all()
    if len(engines) != 2 or any(engine.lower() != "innodb" for engine in engines):
        raise HTTPException(409, "设备数据表不支持安全事务删除，请先停机备份并转换为 InnoDB")
    device = await db.scalar(select(HardwareDevice).where(
        HardwareDevice.provider_id == owner, HardwareDevice.provider_device_id == provider_device_id,
    ).with_for_update())
    if device is None:
        return False
    if device.area_id is not None or device.plot_id is not None:
        raise HTTPException(409, "设备已绑定产区或地块，请先解除绑定")
    # 兼容旧表虽为 InnoDB 但未实际安装级联约束的情况；仍在相同事务内清理。
    await db.execute(delete(HardwareRealtimeSnapshot).where(HardwareRealtimeSnapshot.device_id == device.id))
    await db.delete(device)
    await db.flush()
    return True
