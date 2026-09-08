# -*- coding: utf-8 -*-
"""智能识别插件请求 Schema。"""

from datetime import datetime
from math import isfinite
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class ModelMetadataUpdate(BaseModel):
    """仅更新模型可读元数据，不允许通过该接口替换物理文件。"""

    name: str = Field(..., min_length=1, max_length=128, description="模型显示名称")
    version: str = Field(default="", max_length=64, description="模型业务版本")
    description: str = Field(default="", max_length=1000, description="模型说明")
    default_confidence: float = Field(
        default=0.25,
        ge=0.01,
        le=1,
        description="模型默认识别置信度，范围0.01到1",
    )


class ModelPlotsUpdate(BaseModel):
    """模型目标地块整集；空数组表示解绑该模型的全部地块。"""

    plot_ids: list[int] = Field(default_factory=list, max_length=10000, description="地块ID整集")

    @field_validator("plot_ids")
    @classmethod
    def validate_plot_ids(cls, value: list[int]) -> list[int]:
        """拒绝非正数和重复ID，避免请求语义含糊。"""
        if any(item < 1 for item in value):
            raise ValueError("地块ID必须为正整数")
        if len(value) != len(set(value)):
            raise ValueError("地块ID不能重复")
        return value


class RecognitionDetection(BaseModel):
    """单个识别目标；坐标采用调用方模型输出的左上、右下顺序。"""

    label: str = Field(..., min_length=1, max_length=128, description="识别标签")
    confidence: float = Field(..., ge=0, le=1, description="识别置信度，范围0到1")
    bbox: list[float] | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        description="可选目标框坐标[x1,y1,x2,y2]",
    )

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        """去除标签两侧空白，拒绝只包含空白的标签。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("识别标签不能为空")
        return normalized

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float] | None) -> list[float] | None:
        """坐标必须有限且右下角不得位于左上角之前。"""
        if value is None:
            return None
        if any(not isfinite(item) for item in value):
            raise ValueError("目标框坐标必须是有限数值")
        x1, y1, x2, y2 = value
        if x2 < x1 or y2 < y1:
            raise ValueError("目标框右下角不能位于左上角之前")
        return value


class RecognitionCreate(BaseModel):
    """管理员写入识别执行方已经生成的结构化结果。"""

    plot_id: int = Field(..., ge=1, description="识别地块ID")
    model_id: int = Field(..., ge=1, description="实际使用的模型ID")
    image_url: str = Field(default="", max_length=1000, description="识别原图地址")
    detections: list[RecognitionDetection] = Field(
        default_factory=list,
        max_length=1000,
        description="识别目标明细，空数组表示未识别到目标",
    )
    recognized_at: datetime | None = Field(default=None, description="业务识别时间")

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, value: str) -> str:
        """仅允许站内绝对路径和不携带凭据的HTTP(S)图片地址。"""
        normalized = value.strip()
        if not normalized:
            return ""
        if normalized.startswith("/"):
            if normalized.startswith("//") or "\\" in normalized:
                raise ValueError("站内图片地址格式无效")
            return normalized
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise ValueError("图片地址仅支持站内绝对路径或HTTP(S) URL")
        return normalized


class DetectionTaskCreate(BaseModel):
    """快捷检测仅接收设备ID，图片和模型均由服务端可信解析。"""

    device_id: int = Field(..., ge=1, description="植物生长记录仪本地设备ID")
