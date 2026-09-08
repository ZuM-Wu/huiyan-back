"""硬件设备管理请求 Schema。"""

from typing import Annotated

from pydantic import BaseModel, Field, model_validator


class HardwareAppearanceUpdate(BaseModel):
    """设备图片更新请求。"""

    image_url: str = Field(default="", max_length=512, description="设备自定义图片URL")


class HardwareMetricVisibilityUpdate(BaseModel):
    """设备实时指标卡片显示配置。"""

    identifiers: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=256, description="显示的实时数据标识列表"
    )


class HardwareBindingUpdate(BaseModel):
    """设备产区、地块绑定请求。"""

    area_id: int = Field(..., ge=1, description="绑定产区ID")
    plot_id: int = Field(..., ge=1, description="绑定地块ID")


class HardwareMarkerUpdate(BaseModel):
    """地图拖动后更新设备地块内位置请求。"""

    marker_longitude: float = Field(..., ge=-180, le=180, description="地块内经度")
    marker_latitude: float = Field(..., ge=-90, le=90, description="地块内纬度")


class HardwareAutoFetchSettings(BaseModel):
    """硬件实时数据自动获取设置。"""

    enabled: bool = Field(default=True, description="是否开启自动获取")
    interval_seconds: int | None = Field(
        default=None, ge=1, le=86400, description="自动获取间隔秒数"
    )
    interval_minutes: int | None = Field(
        default=None,
        ge=1,
        le=1440,
        description="兼容旧客户端的自动获取间隔分钟数",
        json_schema_extra={"deprecated": True},
    )

    @model_validator(mode="after")
    def normalize_legacy_interval(self):
        """统一转换为秒，旧客户端仍可提交分钟字段。"""
        if self.interval_seconds is None:
            self.interval_seconds = (self.interval_minutes or 15) * 60
        return self
