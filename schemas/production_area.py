"""产区管理 Pydantic Schema

按实体分组：产区(Area) / 地块(Plot) / 种植批次(Batch) / 地图配置(MapConfig)。
经纬度、GeoJSON boundary、面积等字段用 Field 声明约束与中文 description。
"""
from pydantic import BaseModel, Field
from typing import List


# ========== 产区 ==========

class AreaCreate(BaseModel):
    """产区创建请求体（绑定统一在农户绑定页多对多管理，创建时不指定农户）"""

    name: str = Field(..., max_length=128, description="产区名称")
    code: str = Field(default="", max_length=64, description="产区编号")
    province: str = Field(default="", max_length=64, description="省")
    city: str = Field(default="", max_length=64, description="市")
    district: str = Field(default="", max_length=64, description="区/县")
    address: str = Field(default="", max_length=256, description="详细地址")
    longitude: float = Field(default=0, ge=-180, le=180, description="中心点经度")
    latitude: float = Field(default=0, ge=-90, le=90, description="中心点纬度")
    boundary: str = Field(default="", description="边界GeoJSON多边形")
    area_size: float = Field(default=0, ge=0, description="面积（亩）")
    crop_category: str = Field(default="", max_length=64, description="主要作物类别")
    sort_order: int = Field(default=0, description="排序")
    description: str = Field(default="", max_length=512, description="备注说明")


class AreaUpdate(BaseModel):
    """产区更新请求体（绑定统一在农户绑定页多对多管理，不在此处处理农户）"""

    name: str = Field(..., max_length=128, description="产区名称")
    code: str = Field(default="", max_length=64, description="产区编号")
    province: str = Field(default="", max_length=64, description="省")
    city: str = Field(default="", max_length=64, description="市")
    district: str = Field(default="", max_length=64, description="区/县")
    address: str = Field(default="", max_length=256, description="详细地址")
    longitude: float = Field(default=0, ge=-180, le=180, description="中心点经度")
    latitude: float = Field(default=0, ge=-90, le=90, description="中心点纬度")
    boundary: str = Field(default="", description="边界GeoJSON多边形")
    area_size: float = Field(default=0, ge=0, description="面积（亩）")
    crop_category: str = Field(default="", max_length=64, description="主要作物类别")
    sort_order: int = Field(default=0, description="排序")
    description: str = Field(default="", max_length=512, description="备注说明")


class AreaStatusUpdate(BaseModel):
    """产区状态切换请求体"""

    status: int = Field(..., ge=0, le=1, description="状态: 0=停用, 1=正常")


class AreaFarmersBind(BaseModel):
    """产区多农户绑定请求体（整集替换：传入当前应绑定的全部农户 ID，空数组=解绑全部）"""

    farmer_ids: List[int] = Field(default_factory=list, description="农户ID列表（整集替换，空=解绑全部）")


# ========== 地块 ==========

class PlotCreate(BaseModel):
    """地块创建请求体"""

    area_id: int = Field(..., ge=1, description="所属产区ID")
    name: str = Field(..., max_length=128, description="地块名称")
    code: str = Field(default="", max_length=64, description="地块编号")
    longitude: float = Field(default=0, ge=-180, le=180, description="中心点经度")
    latitude: float = Field(default=0, ge=-90, le=90, description="中心点纬度")
    boundary: str = Field(default="", description="边界GeoJSON多边形")
    area_size: float = Field(default=0, ge=0, description="面积（亩）")
    soil_type: str = Field(default="", max_length=64, description="土壤类型")
    sort_order: int = Field(default=0, description="排序")
    description: str = Field(default="", max_length=512, description="备注说明")


class PlotUpdate(BaseModel):
    """地块更新请求体（area_id 不可改，保护归属稳定）"""

    name: str = Field(..., max_length=128, description="地块名称")
    code: str = Field(default="", max_length=64, description="地块编号")
    longitude: float = Field(default=0, ge=-180, le=180, description="中心点经度")
    latitude: float = Field(default=0, ge=-90, le=90, description="中心点纬度")
    boundary: str = Field(default="", description="边界GeoJSON多边形")
    area_size: float = Field(default=0, ge=0, description="面积（亩）")
    soil_type: str = Field(default="", max_length=64, description="土壤类型")
    sort_order: int = Field(default=0, description="排序")
    description: str = Field(default="", max_length=512, description="备注说明")


# ========== 种植批次 ==========

class BatchCreate(BaseModel):
    """种植批次创建请求体"""

    plot_id: int = Field(..., ge=1, description="所属地块ID")
    batch_no: str = Field(default="", max_length=64, description="批次编号")
    crop_name: str = Field(..., max_length=64, description="作物名称")
    crop_variety: str = Field(default="", max_length=64, description="作物品种")
    season: str = Field(default="", max_length=32, description="茬口/季别")
    plant_date: str = Field(default="", description="种植日期 YYYY-MM-DD")
    expected_harvest_date: str = Field(default="", description="预计采收日期 YYYY-MM-DD")
    actual_harvest_date: str = Field(default="", description="实际采收日期 YYYY-MM-DD")
    plant_count: int = Field(default=0, ge=0, description="种植株数")
    description: str = Field(default="", max_length=512, description="备注说明")


class BatchUpdate(BaseModel):
    """种植批次更新请求体（plot_id 不可改）"""

    batch_no: str = Field(default="", max_length=64, description="批次编号")
    crop_name: str = Field(..., max_length=64, description="作物名称")
    crop_variety: str = Field(default="", max_length=64, description="作物品种")
    season: str = Field(default="", max_length=32, description="茬口/季别")
    plant_date: str = Field(default="", description="种植日期 YYYY-MM-DD")
    expected_harvest_date: str = Field(default="", description="预计采收日期 YYYY-MM-DD")
    actual_harvest_date: str = Field(default="", description="实际采收日期 YYYY-MM-DD")
    plant_count: int = Field(default=0, ge=0, description="种植株数")
    description: str = Field(default="", max_length=512, description="备注说明")


class BatchStatusUpdate(BaseModel):
    """种植批次状态切换请求体"""

    status: int = Field(..., ge=0, le=3, description="状态: 0=未开始, 1=种植中, 2=已采收, 3=异常")


# ========== 地图配置 ==========

class MapConfigUpdate(BaseModel):
    """高德地图配置更新请求体"""

    amap_web_key: str = Field(default="", max_length=128, description="高德 Web JS API Key")
    amap_js_security_code: str = Field(default="", max_length=128, description="高德安全密钥 jscode")
    amap_web_service_key: str = Field(default="", max_length=128, description="高德 Web 服务 Key（静态地图缩略图用）")
    production_area_map_enabled: str = Field(default="1", description="地图选址开关: 0=关闭, 1=开启")
