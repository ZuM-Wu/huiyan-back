"""实名认证 Pydantic Schema"""
import re

from pydantic import BaseModel, Field, field_validator


class CertReviewAction(BaseModel):
    """审批请求体"""

    status: int = Field(..., ge=0, le=2, description="审核状态: 1=通过, 2=拒绝")
    review_remark: str = Field(default="", max_length=512, description="审核备注")


class CertConfigUpdate(BaseModel):
    """实名认证配置更新请求体"""

    cert_enabled: str = Field(default="1", description="实名认证开关")
    cert_auto_update_name: str = Field(default="1", description="自动更新姓名")
    cert_show_id: str = Field(default="1", description="展示认证ID")
    cert_manual_review: str = Field(default="0", description="人工复审")
    cert_notify_user: str = Field(default="0", description="通知用户")
    cert_upload_image: str = Field(default="0", description="上传图片")
    cert_phone_match: str = Field(default="0", description="手机一致性校验")


class ChannelCreate(BaseModel):
    """渠道创建请求体"""

    plugin_name: str = Field(..., max_length=64, description="插件标识")
    channel_name: str = Field(..., max_length=64, description="渠道名称")
    channel_type: str = Field(default="personal", max_length=20, description="类型")
    config: str = Field(default="{}", description="配置参数JSON")


class ChannelConfigUpdate(BaseModel):
    """渠道配置更新请求体"""

    channel_name: str = Field(default="", max_length=64, description="渠道名称")
    channel_type: str = Field(default="personal", max_length=20, description="类型")
    config: str = Field(default="{}", description="配置参数JSON")


class ChannelStatusUpdate(BaseModel):
    """渠道状态切换请求体"""

    status: int = Field(..., ge=0, le=1, description="状态: 0=禁用, 1=启用")


class PluginConfigUpsert(BaseModel):
    """插件配置保存请求体（按插件标识 upsert 渠道）"""

    config: dict = Field(default_factory=dict, description="配置参数")
    channel_name: str = Field(default="", max_length=64, description="渠道名称")
    channel_type: str = Field(default="personal", max_length=20, description="类型")


class FarmerCertSubmit(BaseModel):
    """农户端认证提交请求体

    格式校验规则与前端 certification.html 的 certRules 保持一致，
    防止绕过前端直接调接口提交脏数据进入认证流程。
    """

    real_name: str = Field(..., max_length=64, description="真实姓名")
    id_card: str = Field(..., max_length=18, description="身份证号")
    phone: str = Field(default="", max_length=32, description="手机号")
    front_image: str = Field(default="", max_length=256, description="身份证正面照URL")
    back_image: str = Field(default="", max_length=256, description="身份证背面照URL")
    channel: str = Field(default="", max_length=64, description="认证渠道（插件标识），空则走人工审核")

    @field_validator("real_name")
    @classmethod
    def check_real_name(cls, v: str) -> str:
        """姓名：去首尾空白后 2-32 个汉字/字母（允许少数民族姓名中的间隔号）"""
        v = v.strip()
        if not re.fullmatch(r"[\u4e00-\u9fa5a-zA-Z\u00b7]{2,32}", v):
            raise ValueError("姓名格式不正确，应为2-32个汉字或字母")
        return v

    @field_validator("id_card")
    @classmethod
    def check_id_card(cls, v: str) -> str:
        """身份证号：15 位纯数字或 18 位（末位可为 X/x）"""
        v = v.strip()
        if not re.fullmatch(r"\d{15}|\d{17}[\dXx]", v):
            raise ValueError("身份证号格式不正确")
        return v

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str) -> str:
        """手机号：允许为空，非空时须为大陆 11 位手机号"""
        v = v.strip()
        if v and not re.fullmatch(r"1[3-9]\d{9}", v):
            raise ValueError("手机号格式不正确")
        return v
