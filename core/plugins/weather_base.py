# -*- coding: utf-8 -*-
"""
天气数据源插件抽象基类
定义天气插件（和风天气/高德气象等）的统一接口规范

- WeatherPluginBase: 天气插件基类（fetch_weather 方法）

设计对标 core/plugins/base.py 中的 SmsPluginBase：
插件仅作为"数据源渠道"，不注册自身页面，由 core/weather_service.py
统一调度，配置通过 hy_configuration 表以 "{插件名}.{键}" 前缀存储。

插件返回值统一 DTO 约定（归一化在插件内完成，核心模块不感知源差异）:
    {
        "status": "success" / "error",
        "msg": "错误原因（成功时为空）",
        "source": "插件名（weather_qweather / weather_amap）",
        "granularity": "point"（经纬度点位）/ "district"（区县级）,
        "realtime": {  # 实况天气
            "temp": 温度（摄氏度，float）,
            "humidity": 相对湿度（%）,
            "wind_dir": 风向文字,
            "wind_scale": 风力等级,
            "text": 天气现象文字,
            "icon": 天气图标代码（源提供则填，否则空）,
            "obs_time": 观测时间字符串,
        },
        "hourly": [  # 24 小时逐时预报（可选，仅和风提供，高德返回空列表）
            {"time": ..., "temp": ..., "text": ..., "icon": ..., "precip_prob": ...},
        ],
        "forecast": [  # 逐日预报（3~7 天）
            {"date": ..., "text_day": ..., "text_night": ...,
             "temp_max": ..., "temp_min": ..., "icon_day": ...,
             "wind_dir": ..., "wind_scale": ..., "humidity": ..., "precip": ...},
        ],
        "alerts": [  # 官方气象灾害预警（可选，仅和风提供）
            {"alert_id": 第三方预警唯一ID, "type": 预警类型, "level": 预警等级,
             "title": 标题, "text": 详情, "start_time": ..., "end_time": ...},
        ],
    }
"""
import logging
from abc import abstractmethod
from typing import Dict, Any, Optional

from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)


class WeatherPluginBase(BasePlugin):
    """
    天气数据源插件抽象基类

    子类必须实现 fetch_weather()，其余方法按需覆写。

    fetch_weather 入参为统一位置描述符 loc:
        longitude: float  经度（产区中心点）
        latitude: float   纬度（产区中心点）
        adcode: str       高德行政区划码（可选，快照表有缓存时传入，省逆地理调用）
        province: str     省（可选，经纬度缺失时的回退定位依据）
        city: str         市（可选）
        district: str     区县（可选）
    """

    module = "weather"

    async def install(self) -> bool:
        """默认安装：写入 plugin.json 中声明的默认配置（已存在的键不覆盖）"""
        if not self.db:
            return True
        from core.config_manager import ConfigManager
        cm = ConfigManager()
        for key, value in (self.config or {}).items():
            existing = await cm.get(key, self.db)
            if existing is None:
                await cm.set(key, str(value), self.db, description=f"{self.name} 天气插件配置")
        logger.info(f"[{self.name}] 天气插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """默认卸载：清理以插件名为前缀的配置"""
        if not self.db:
            return True
        from core.config_manager import ConfigManager
        await ConfigManager().delete_plugin_config(self.name, self.db)
        logger.info(f"[{self.name}] 天气插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 数据获取接口
    # ------------------------------------------------------------------
    @abstractmethod
    async def fetch_weather(self, loc: Dict[str, Any]) -> Dict[str, Any]:
        """
        按位置拉取天气数据并归一化为统一 DTO（见模块 docstring）

        参数:
            loc: 统一位置描述符 {longitude, latitude, adcode?, province?, city?, district?}
        返回:
            统一 DTO；失败时 {"status": "error", "msg": "原因", "source": 插件名}
        """
        ...

    async def test_connection(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """测试插件连通性（配置有效性校验，可选覆写为真实探活）"""
        return {"success": True, "message": "插件已就绪（未实现真实连接测试）"}
