# -*- coding: utf-8 -*-
"""
高德气象数据源插件
对接高德开放平台天气查询 API，提供区县级实况与 3~4 天逐日预报

接口说明:
- 天气查询: https://restapi.amap.com/v3/weather/weatherInfo
    extensions=base 实况（lives）；extensions=all 逐日预报（forecasts.casts）
- 逆地理编码: https://restapi.amap.com/v3/geocode/regeo
    经纬度 -> adcode（天气接口仅支持行政区划码定位）

能力边界（DTO 对应字段返回空）:
- 无逐小时预报（hourly=[]）、无气象灾害预警（alerts=[]）
- 粒度为区县级（granularity="district"），前端提示"区县级数据"

配置说明:
- weather_amap.key 留空时回落使用产区管理已配置的 amap_web_service_key
  （由 core/weather_service.py 在加载配置时以 _fallback_key 注入）
"""
import logging
from typing import Dict, Any

import httpx

from core.plugins.weather_base import WeatherPluginBase

logger = logging.getLogger(__name__)

PLUGIN_NAME = "weather_amap"

# 高德开放平台 API 地址
WEATHER_API_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
REGEO_API_URL = "https://restapi.amap.com/v3/geocode/regeo"

# 单次请求超时（秒），防止拉取任务被单点拖垮
REQUEST_TIMEOUT = 10


class Plugin(WeatherPluginBase):
    """高德气象数据源插件"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "高德气象"
        self.version = "1.0.0"
        self.description = "高德开放平台气象数据源（区县级实况+逐日预报）"
        self.module = "weather"

    # ------------------------------------------------------------------
    # 配置声明
    # ------------------------------------------------------------------
    def get_config_schema(self) -> list:
        """天气管理页面动态渲染的配置表单定义"""
        return [
            {"key": "key", "label": "Web 服务 Key", "type": "input", "required": False,
             "placeholder": "高德 Web 服务 Key（留空则复用产区管理的 amap_web_service_key）"},
        ]

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _resolve_key(self) -> str:
        """解析可用 Key：插件配置优先，留空回落全局高德 Web 服务 Key"""
        config = self.config or {}
        return str(config.get("key") or config.get("_fallback_key") or "").strip()

    async def _regeo_adcode(self, client: httpx.AsyncClient, key: str,
                            longitude: float, latitude: float) -> str:
        """经纬度逆地理编码取 adcode（结果由服务层缓存到快照表，避免重复调用）"""
        resp = await client.get(REGEO_API_URL, params={
            "key": key,
            "location": f"{longitude},{latitude}",
        })
        data = resp.json()
        if data.get("status") != "1":
            raise ValueError(f"逆地理编码失败: {data.get('info', '未知错误')}")
        adcode = (data.get("regeocode", {}) or {}).get("addressComponent", {}).get("adcode", "")
        # 海域等无行政区划位置 adcode 为空列表
        if not adcode or isinstance(adcode, list):
            raise ValueError("该位置无法解析行政区划码（可能不在陆地行政区内）")
        return str(adcode)

    @staticmethod
    def _to_float(value, default=None):
        """安全转 float（高德字段均为字符串，异常时返回默认值）"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # 数据获取（归一化为统一 DTO）
    # ------------------------------------------------------------------
    async def fetch_weather(self, loc: Dict[str, Any]) -> Dict[str, Any]:
        """
        拉取实况 + 逐日预报并归一化

        流程: 无 adcode 时先逆地理编码 -> extensions=base 实况 -> extensions=all 预报
        """
        key = self._resolve_key()
        if not key:
            return {"status": "error", "source": PLUGIN_NAME,
                    "msg": "未配置高德 Web 服务 Key（插件配置与产区管理均为空）"}

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                # 1. 定位: adcode 优先复用快照缓存，否则按经纬度逆地理编码
                adcode = str(loc.get("adcode") or "").strip()
                if not adcode:
                    longitude = loc.get("longitude") or 0
                    latitude = loc.get("latitude") or 0
                    if not longitude or not latitude:
                        return {"status": "error", "source": PLUGIN_NAME,
                                "msg": "产区未设置经纬度且无行政区划码，无法定位"}
                    adcode = await self._regeo_adcode(client, key, longitude, latitude)

                # 2. 实况天气
                live_resp = await client.get(WEATHER_API_URL, params={
                    "key": key, "city": adcode, "extensions": "base",
                })
                live_data = live_resp.json()
                if live_data.get("status") != "1":
                    return {"status": "error", "source": PLUGIN_NAME,
                            "msg": f"实况查询失败: {live_data.get('info', '未知错误')}"}
                lives = live_data.get("lives") or []
                if not lives:
                    return {"status": "error", "source": PLUGIN_NAME,
                            "msg": f"实况数据为空（adcode={adcode}）"}
                live = lives[0]

                # 3. 逐日预报（3~4 天）
                cast_resp = await client.get(WEATHER_API_URL, params={
                    "key": key, "city": adcode, "extensions": "all",
                })
                cast_data = cast_resp.json()
                casts = []
                if cast_data.get("status") == "1" and cast_data.get("forecasts"):
                    casts = cast_data["forecasts"][0].get("casts") or []
        except httpx.HTTPError as e:
            logger.warning(f"[{PLUGIN_NAME}] 请求高德接口异常: {e}")
            return {"status": "error", "source": PLUGIN_NAME, "msg": f"网络请求失败: {e}"}
        except ValueError as e:
            return {"status": "error", "source": PLUGIN_NAME, "msg": str(e)}

        # 4. 归一化为统一 DTO（高德无逐时/无预警/无图标代码）
        return {
            "status": "success",
            "msg": "",
            "source": PLUGIN_NAME,
            "granularity": "district",
            "adcode": adcode,
            "realtime": {
                "temp": self._to_float(live.get("temperature")),
                "humidity": self._to_float(live.get("humidity")),
                "wind_dir": live.get("winddirection", ""),
                "wind_scale": live.get("windpower", ""),
                "text": live.get("weather", ""),
                "icon": "",
                "obs_time": live.get("reporttime", ""),
            },
            "hourly": [],
            "forecast": [
                {
                    "date": c.get("date", ""),
                    "text_day": c.get("dayweather", ""),
                    "text_night": c.get("nightweather", ""),
                    "temp_max": self._to_float(c.get("daytemp")),
                    "temp_min": self._to_float(c.get("nighttemp")),
                    "icon_day": "",
                    "wind_dir": c.get("daywind", ""),
                    "wind_scale": c.get("daypower", ""),
                    "humidity": None,
                    "precip": None,
                }
                for c in casts
            ],
            "alerts": [],
        }

    # ------------------------------------------------------------------
    # 连通性测试
    # ------------------------------------------------------------------
    async def test_connection(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        """用北京市 adcode 真实调用实况接口探活"""
        if config:
            self.config = {**(self.config or {}), **config}
        key = self._resolve_key()
        if not key:
            return {"success": False, "message": "未配置高德 Web 服务 Key"}
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(WEATHER_API_URL, params={
                    "key": key, "city": "110000", "extensions": "base",
                })
                data = resp.json()
            if data.get("status") == "1":
                return {"success": True, "message": "高德气象接口连通正常"}
            return {"success": False, "message": f"接口返回错误: {data.get('info', '未知错误')}"}
        except httpx.HTTPError as e:
            return {"success": False, "message": f"网络请求失败: {e}"}
