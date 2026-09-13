"""
产区三级写操作 MCP 核心工具（audience=admin，不含删除）

- area_create / area_update    产区创建与更新（area:create / area:update）
- plot_create / plot_update    地块创建与更新（plot:create / plot:update）
- batch_create / batch_update  种植批次创建与更新（batch:create / batch:update）

实现约定:
1. 工具签名全部用基础标量类型（与既有工具风格一致，规避 FastMCP 复杂参数
   schema 的不确定性），handler 内经 tool_utils.validate_payload 构造
   schemas/production_area.py 的对应模型完成与后台接口同等的二次校验。
2. update 类工具只 setattr 暴露给 AI 的字段（与后台 model_dump 全量覆盖是
   有意偏差）：boundary/sort_order/actual_harvest_date 等未暴露字段不被
   默认值清空。
3. 归属关系不可改: plot_update 不改 area_id，batch_update 不改 plot_id。
4. 写逻辑为薄 ORM 实现（与 API 路由内联逻辑同构，不下沉只读契约服务
   production_area_service.py）；commit 后写 active_log（MCP 前缀文案）。
"""
import logging
from datetime import datetime

from sqlalchemy import select
from fastmcp.exceptions import ToolError

from core.db.base import async_session_factory
from core.db.production_area import ProductionArea, Plot, PlantingBatch
from services.mcp.tool_utils import validate_payload
from schemas.production_area import (
    AreaCreate, AreaUpdate, PlotCreate, PlotUpdate, BatchCreate, BatchUpdate,
)

logger = logging.getLogger(__name__)

# update 工具允许写入的字段白名单（未列出的字段保持原值不动）
_AREA_FIELDS = ("name", "code", "province", "city", "district", "address",
                "longitude", "latitude", "area_size", "crop_category", "description")
_PLOT_FIELDS = ("name", "code", "longitude", "latitude", "area_size",
                "soil_type", "description")
_BATCH_FIELDS = ("batch_no", "crop_name", "crop_variety", "season", "plant_count",
                 "description")


def _parse_date_strict(value: str, field: str):
    """YYYY-MM-DD 严格解析（空串返回 None；非法抛中文 ToolError，
    与后台 _parse_date 静默返回 None 不同——AI 传错日期必须显式报错）"""
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except (ValueError, TypeError) as e:
        raise ToolError(f"{field} 日期格式非法（应为 YYYY-MM-DD）: {value}") from e


# ---------------------------------------------------------------- 产区

async def area_create(name: str, *, code: str = "", province: str = "",
                      city: str = "", district: str = "", address: str = "",
                      longitude: float = 0.0, latitude: float = 0.0,
                      area_size: float = 0.0, crop_category: str = "",
                      description: str = "") -> dict:
    """创建产区。

    :param name: 产区名称（必填）
    :param code: 产区编号
    :param province: 省
    :param city: 市
    :param district: 区/县
    :param address: 详细地址
    :param longitude: 中心点经度（-180~180，天气拉取定位用）
    :param latitude: 中心点纬度（-90~90）
    :param area_size: 面积（亩，≥0）
    :param crop_category: 主要作物类别
    :param description: 备注说明
    :return: 创建结果，含新产区 id
    """
    data = validate_payload(AreaCreate, {
        "name": name, "code": code, "province": province, "city": city,
        "district": district, "address": address, "longitude": longitude,
        "latitude": latitude, "area_size": area_size,
        "crop_category": crop_category, "description": description,
    })

    from core.log.active_log import active_log

    async with async_session_factory() as db:
        # boundary/sort_order 不暴露给 AI，走模型默认值
        area = ProductionArea(
            name=data.name, code=data.code,
            province=data.province, city=data.city, district=data.district,
            address=data.address, longitude=data.longitude, latitude=data.latitude,
            area_size=data.area_size, crop_category=data.crop_category,
            description=data.description, status=1,
        )
        db.add(area)
        await db.commit()
        await db.refresh(area)
        await active_log(f"MCP新增产区: {area.name}", "area",
                         rel_id=area.id, db=db)
        return {"id": area.id, "msg": f"产区已创建: {area.name}"}


async def area_update(area_id: int, name: str, *, code: str = "", province: str = "",
                      city: str = "", district: str = "", address: str = "",
                      longitude: float = 0.0, latitude: float = 0.0,
                      area_size: float = 0.0, crop_category: str = "",
                      description: str = "") -> dict:
    """更新产区基础信息（未提供的可选参数会写为默认值，建议先查询现状再全量传参）。

    :param area_id: 产区ID
    :param name: 产区名称（必填）
    :return: 更新结果
    """
    data = validate_payload(AreaUpdate, {
        "name": name, "code": code, "province": province, "city": city,
        "district": district, "address": address, "longitude": longitude,
        "latitude": latitude, "area_size": area_size,
        "crop_category": crop_category, "description": description,
    })

    from core.log.active_log import active_log

    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if area is None:
            raise ToolError(f"产区不存在: {area_id}")

        # 只写暴露字段，boundary/sort_order/status 保持原值
        for field in _AREA_FIELDS:
            setattr(area, field, getattr(data, field))
        await db.commit()
        await active_log(f"MCP编辑产区: {area.name}", "area",
                         rel_id=area_id, db=db)
        return {"id": area_id, "msg": f"产区已更新: {area.name}"}


# ---------------------------------------------------------------- 地块

async def plot_create(area_id: int, name: str, code: str = "",
                      longitude: float = 0.0, latitude: float = 0.0,
                      area_size: float = 0.0, soil_type: str = "",
                      description: str = "") -> dict:
    """在指定产区下创建地块。

    :param area_id: 所属产区ID（必填）
    :param name: 地块名称（必填）
    :param code: 地块编号
    :param longitude: 中心点经度（-180~180）
    :param latitude: 中心点纬度（-90~90）
    :param area_size: 面积（亩，≥0）
    :param soil_type: 土壤类型
    :param description: 备注说明
    :return: 创建结果，含新地块 id
    """
    data = validate_payload(PlotCreate, {
        "area_id": area_id, "name": name, "code": code,
        "longitude": longitude, "latitude": latitude, "area_size": area_size,
        "soil_type": soil_type, "description": description,
    })

    from core.log.active_log import active_log

    async with async_session_factory() as db:
        exists = (await db.execute(
            select(ProductionArea.id).where(ProductionArea.id == data.area_id)
        )).scalar_one_or_none()
        if exists is None:
            raise ToolError(f"所属产区不存在: {area_id}")

        # farmer_id 固定写 0（绑定改走产区↔农户关联表，该列退役保留）
        plot = Plot(
            area_id=data.area_id, farmer_id=0,
            name=data.name, code=data.code,
            longitude=data.longitude, latitude=data.latitude,
            area_size=data.area_size, soil_type=data.soil_type,
            description=data.description, status=1,
        )
        db.add(plot)
        await db.commit()
        await db.refresh(plot)
        await active_log(f"MCP新增地块: {plot.name}", "plot",
                         rel_id=plot.id, db=db)
        return {"id": plot.id, "msg": f"地块已创建: {plot.name}"}


async def plot_update(plot_id: int, name: str, code: str = "",
                      longitude: float = 0.0, latitude: float = 0.0,
                      area_size: float = 0.0, soil_type: str = "",
                      description: str = "") -> dict:
    """更新地块基础信息（所属产区不可改；建议先查询现状再全量传参）。

    :param plot_id: 地块ID
    :param name: 地块名称（必填）
    :return: 更新结果
    """
    data = validate_payload(PlotUpdate, {
        "name": name, "code": code, "longitude": longitude,
        "latitude": latitude, "area_size": area_size,
        "soil_type": soil_type, "description": description,
    })

    from core.log.active_log import active_log

    async with async_session_factory() as db:
        plot = (await db.execute(
            select(Plot).where(Plot.id == plot_id)
        )).scalar_one_or_none()
        if plot is None:
            raise ToolError(f"地块不存在: {plot_id}")

        # 只写暴露字段，area_id/boundary/sort_order/status 保持原值
        for field in _PLOT_FIELDS:
            setattr(plot, field, getattr(data, field))
        await db.commit()
        await active_log(f"MCP编辑地块: {plot.name}", "plot",
                         rel_id=plot_id, db=db)
        return {"id": plot_id, "msg": f"地块已更新: {plot.name}"}


# ---------------------------------------------------------------- 种植批次

async def batch_create(plot_id: int, crop_name: str, *, batch_no: str = "",
                       crop_variety: str = "", season: str = "",
                       plant_date: str = "", expected_harvest_date: str = "",
                       plant_count: int = 0, description: str = "") -> dict:
    """在指定地块下创建种植批次（area_id/farmer_id 从所属地块继承）。

    :param plot_id: 所属地块ID（必填）
    :param crop_name: 作物名称（必填）
    :param batch_no: 批次编号
    :param crop_variety: 作物品种
    :param season: 茬口/季别
    :param plant_date: 种植日期 YYYY-MM-DD（空串表示未定）
    :param expected_harvest_date: 预计采收日期 YYYY-MM-DD（空串表示未定）
    :param plant_count: 种植株数（≥0）
    :param description: 备注说明
    :return: 创建结果，含新批次 id
    """
    data = validate_payload(BatchCreate, {
        "plot_id": plot_id, "crop_name": crop_name, "batch_no": batch_no,
        "crop_variety": crop_variety, "season": season,
        "plant_date": plant_date, "expected_harvest_date": expected_harvest_date,
        "plant_count": plant_count, "description": description,
    })
    # 日期 fail-fast: 校验之后、开库之前解析，非法立即报错
    plant_dt = _parse_date_strict(data.plant_date, "plant_date")
    expected_dt = _parse_date_strict(data.expected_harvest_date,
                                     "expected_harvest_date")

    from core.log.active_log import active_log

    async with async_session_factory() as db:
        plot = (await db.execute(
            select(Plot).where(Plot.id == data.plot_id)
        )).scalar_one_or_none()
        if plot is None:
            raise ToolError(f"所属地块不存在: {plot_id}")

        batch = PlantingBatch(
            plot_id=data.plot_id, area_id=plot.area_id, farmer_id=plot.farmer_id,
            batch_no=data.batch_no, crop_name=data.crop_name,
            crop_variety=data.crop_variety, season=data.season,
            plant_date=plant_dt, expected_harvest_date=expected_dt,
            plant_count=data.plant_count, description=data.description, status=1,
        )
        db.add(batch)
        await db.commit()
        await db.refresh(batch)
        await active_log(f"MCP新增种植批次: {batch.batch_no or batch.crop_name}",
                         "batch", rel_id=batch.id, db=db)
        return {"id": batch.id, "msg": f"批次已创建: {batch.batch_no or batch.crop_name}"}


async def batch_update(batch_id: int, crop_name: str, *, batch_no: str = "",
                       crop_variety: str = "", season: str = "",
                       plant_date: str = "", expected_harvest_date: str = "",
                       plant_count: int = 0, description: str = "") -> dict:
    """更新种植批次（所属地块不可改；建议先查询现状再全量传参）。

    :param batch_id: 批次ID
    :param crop_name: 作物名称（必填）
    :return: 更新结果
    """
    data = validate_payload(BatchUpdate, {
        "crop_name": crop_name, "batch_no": batch_no,
        "crop_variety": crop_variety, "season": season,
        "plant_date": plant_date, "expected_harvest_date": expected_harvest_date,
        "plant_count": plant_count, "description": description,
    })
    plant_dt = _parse_date_strict(data.plant_date, "plant_date")
    expected_dt = _parse_date_strict(data.expected_harvest_date,
                                     "expected_harvest_date")

    from core.log.active_log import active_log

    async with async_session_factory() as db:
        batch = (await db.execute(
            select(PlantingBatch).where(PlantingBatch.id == batch_id)
        )).scalar_one_or_none()
        if batch is None:
            raise ToolError(f"批次不存在: {batch_id}")

        # 只写暴露字段，plot_id/actual_harvest_date/status 保持原值
        for field in _BATCH_FIELDS:
            setattr(batch, field, getattr(data, field))
        batch.plant_date = plant_dt
        batch.expected_harvest_date = expected_dt
        await db.commit()
        await active_log(f"MCP编辑种植批次: {batch.batch_no or batch.crop_name}",
                         "batch", rel_id=batch_id, db=db)
        return {"id": batch_id, "msg": f"批次已更新: {batch.batch_no or batch.crop_name}"}


# 产区三级写操作工具声明列表（tools_core.py 聚合进 CORE_TOOLS）
AREA_WRITE_TOOLS: list[dict] = [
    {
        "name": "agri_area_create",
        "description": "创建产区。必填 name 产区名称；可选 code/province/city/district/"
                       "address/longitude/latitude/area_size/crop_category/description。"
                       "返回新产区 id。注意：本工具不设置地图边界，边界需后续在后台"
                       "产区「编辑」弹窗内拉框补充；建议提供 longitude/latitude 中心点以支持天气定位。",
        "handler": area_create,
        "audience": "admin",
        "permission_code": "area:create",
    },
    {
        "name": "agri_area_update",
        "description": "更新产区基础信息。参数 area_id 产区ID，其余同 core_area_create；"
                       "未提供的可选参数会写为默认值，建议先用 core_area_tree 查询现状"
                       "后全量传参。边界与排序等字段不受影响。",
        "handler": area_update,
        "audience": "admin",
        "permission_code": "area:update",
    },
    {
        "name": "agri_plot_create",
        "description": "在产区下创建地块。必填 area_id 所属产区ID、name 地块名称；"
                       "可选 code/longitude/latitude/area_size/soil_type/description。"
                       "返回新地块 id。注意：本工具不设置地图边界，无边界地块在地图上不显示，"
                       "需后续在后台产区详情页为该地块点「补画边界」拉框补充。",
        "handler": plot_create,
        "audience": "admin",
        "permission_code": "plot:create",
    },
    {
        "name": "agri_plot_update",
        "description": "更新地块基础信息（所属产区不可改）。参数 plot_id 地块ID，"
                       "其余同 core_plot_create；未提供的可选参数会写为默认值，"
                       "建议先查询现状后全量传参。",
        "handler": plot_update,
        "audience": "admin",
        "permission_code": "plot:update",
    },
    {
        "name": "agri_batch_create",
        "description": "在地块下创建种植批次（产区/农户归属自动继承）。必填 plot_id "
                       "所属地块ID、crop_name 作物名称；可选 batch_no/crop_variety/"
                       "season/plant_date/expected_harvest_date（YYYY-MM-DD）/"
                       "plant_count/description。返回新批次 id。",
        "handler": batch_create,
        "audience": "admin",
        "permission_code": "batch:create",
    },
    {
        "name": "agri_batch_update",
        "description": "更新种植批次（所属地块与实际采收日期不可改）。参数 batch_id "
                       "批次ID，其余同 core_batch_create；未提供的可选参数会写为默认值，"
                       "建议先查询现状后全量传参。",
        "handler": batch_update,
        "audience": "admin",
        "permission_code": "batch:update",
    },
]
