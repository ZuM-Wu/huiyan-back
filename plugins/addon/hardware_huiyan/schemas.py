"""慧眼设备注册边界：稳定编号与公共硬件 DTO 保持一致。"""

import json
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator
from core.hardware_types import HardwareMetric


class DeviceRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    device_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128, description="设备稳定注册标识，省略时自动生成 UUID")
    device_name: str = Field(min_length=1, max_length=128, description="设备编号或名称")
    device_type: str = Field(default="", max_length=32, description="设备类型编码")
    device_type_label: str = Field(default="", max_length=64, description="设备类型名称")
    nickname: str = Field(default="", max_length=128, description="设备显示名称")
    address: str = Field(default="", max_length=256, description="安装地址")
    latitude: str = Field(default="", max_length=32, description="纬度")
    longitude: str = Field(default="", max_length=32, description="经度")
    capabilities: list[Literal["realtime", "history", "take_photo"]] = Field(default_factory=lambda: ["realtime"], description="申报能力；平台仅开放已实现能力")
    metadata: dict = Field(default_factory=dict, description="扩展信息，identifiers 为指标数组")

    @field_validator("device_id", "device_name")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("设备标识和名称不能为空白、包含首尾空白或控制字符")
        if "/" in value or chr(92) in value:
            raise ValueError("设备标识和名称不能包含路径分隔符")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metrics(cls, value: dict) -> dict:
        if "identifiers" in value:
            rows = value["identifiers"]
            if not isinstance(rows, list):
                raise ValueError("identifiers 必须为数组")
            value = {**value, "identifiers": [HardwareMetric.model_validate(row).model_dump(by_alias=True) for row in rows]}
        return value


class AdminDeviceRegisterRequest(DeviceRegisterRequest):
    replace_existing: bool = Field(default=False, description="确认更新已有设备基础信息")


class DeviceReportMetric(HardwareMetric):
    """设备只须提交标识和值；其余未提交属性由存储层保留原值。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", allow_inf_nan=False)
    value: Any = Field(description="本次采集值，必须提供，可显式提交 null")

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("指标标识不能为空白、包含首尾空白或控制字符")
        return value

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: Any) -> Any:
        try:
            json.dumps(value, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("指标值必须是合法 JSON，数值必须有限") from exc
        return value


class DeviceReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifiers: list[DeviceReportMetric] = Field(min_length=1, description="本次上报指标，同批标识不得重复")

    @field_validator("identifiers")
    @classmethod
    def validate_identifiers(cls, rows: list[DeviceReportMetric]) -> list[DeviceReportMetric]:
        if len({row.identifier for row in rows}) != len(rows):
            raise ValueError("同一次上报不能包含重复指标标识")
        return rows
