"""硬件插件管理配置门面：固定 owner、掩码读取与空凭据保留。"""

from urllib.parse import urlsplit
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
