"""硬件插件与平台之间的公开数据协议，不暴露数据库或厂商凭据。"""

from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.time_utils import CHINA_TIMEZONE


def _normalize_observed_time(value: str) -> str:
    """协议边界统一明确时区，禁止把无时区厂商值静默按服务器时区解释。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("硬件时间必须包含时区")
    return parsed.astimezone(CHINA_TIMEZONE).isoformat(timespec="seconds")


class HardwareError(RuntimeError):
    """只允许携带可展示给管理员的脱敏中文错误。"""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class HardwareDeviceData(BaseModel):
    """供应商返回的完整设备镜像；本地业务字段不属于插件写入范围。"""

    model_config = ConfigDict(extra="forbid")
    provider_device_id: str = Field(min_length=1, max_length=128, description="来源内稳定设备ID")
    device_name: str = Field(min_length=1, max_length=128, description="设备编号")
    device_type: str = Field(default="", max_length=32, description="设备类型编码")
    device_type_label: str = Field(default="", max_length=64, description="设备类型名称")
    nickname: str = Field(default="", max_length=128, description="设备名称")
    capabilities: list[Literal["realtime", "history", "take_photo"]] = Field(default_factory=list, description="支持的操作")
    external_id: int = Field(default=0, description="旧数字平台ID兼容值")
    bot_id: int = Field(default=0, description="旧机器人ID兼容值")
    lat: str = Field(default="", max_length=32, description="平台纬度")
    lng: str = Field(default="", max_length=32, description="平台经度")
    address: str = Field(default="", max_length=256, description="平台地址")

    @field_validator("provider_device_id", "device_name")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("设备标识不能为空白或包含首尾空白")
        return value


class HardwareMetric(BaseModel):
    """保留现有前端字段别名，内部使用蛇形命名。"""

    model_config = ConfigDict(populate_by_name=True)
    identifier: str = Field(min_length=1, max_length=128, description="设备内指标标识")
    name: str = Field(default="", description="指标名称")
    value: Any = Field(default=None, description="当前指标值")
    unit: str = Field(default="", description="指标单位")
    data_type: str = Field(default="string", alias="dataType", description="值类型")
    last_update_time: str = Field(default="", alias="lastUpdateTime", description="带时区采样时间")

    @field_validator("last_update_time")
    @classmethod
    def normalize_time(cls, value: str) -> str:
        return _normalize_observed_time(value) if value else ""


class HardwareHistoryItem(BaseModel):
    identifier: str = Field(default="", description="指标标识")
    value: Any = Field(default=None, description="观测值")
    time: str = Field(description="带时区观测时间")

    @field_validator("time")
    @classmethod
    def normalize_time(cls, value: str) -> str:
        return _normalize_observed_time(value)


class HardwareHistory(BaseModel):
    items: list[HardwareHistoryItem] = Field(alias="list", description="时间倒序观测列表")
    total: int = Field(ge=0, description="筛选结果总数")
    page: int = Field(ge=1, description="页码")
    size: int = Field(ge=1, description="每页数量")

    @field_validator("items")
    @classmethod
    def validate_descending(cls, items: list[HardwareHistoryItem]) -> list[HardwareHistoryItem]:
        if any(left.time < right.time for left, right in pairwise(items)):
            raise ValueError("硬件历史必须按时间倒序返回")
        return items


class HardwareHistoryAdapter(Protocol):
    """只读历史门面使用的最小接口，便于多来源联合查询。"""

    async def list_identifiers(self, device_id: str) -> list[dict]: ...
    async def get_history(self, device_id: str, *, identifier: str = "", start_time: datetime | None = None,
                          end_time: datetime | None = None, page: int = 1, size: int = 20) -> dict: ...


class HardwareAdapter(HardwareHistoryAdapter, Protocol):
    """异步协议；不支持的设备操作由平台在调用前按能力拒绝。"""

    async def list_devices(self) -> list[HardwareDeviceData]: ...
    async def list_identifiers(self, device_id: str) -> list[dict]: ...
    async def read_realtime(self, device_id: str) -> list[dict]: ...
    async def get_history(self, device_id: str, *, identifier: str = "", start_time: datetime | None = None,
                          end_time: datetime | None = None, page: int = 1, size: int = 20) -> dict: ...
    async def take_photo(self, device_id: str) -> Any: ...


@dataclass(frozen=True)
class HardwareDetailView:
    """引用当前 owner 在 manifest 中登记的资源 key。"""

    entry: str
    styles: tuple[str, ...] = ()


@dataclass(frozen=True)
class HardwareProvider:
    """一个 Addon 的硬件来源声明，工厂每次得到最新插件配置。"""

    provider_id: str
    title: str
    factory: Callable[[dict], HardwareAdapter]
    device_types: tuple[dict, ...] = field(default_factory=tuple)
    detail_view: HardwareDetailView | None = None
