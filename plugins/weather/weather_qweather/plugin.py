# -*- coding: utf-8 -*-
"""
和风天气数据源插件（v1.1.0）
对接和风天气开发服务，经纬度点位精度，免费额度内提供（v7 为当前最新免费接口）:
- /v7/weather/now      实况天气
- /v7/weather/24h      24 小时逐时预报（路径 /v7/weather/{hours}，支持 24h/72h/168h）
- /v7/weather/3d       3 天逐日预报（路径 /v7/weather/{days}，支持 3d/7d/10d/15d/30d）
- /weatheralert/v1/current/{lat}/{lon}  和风新版灾害预警（旧 /v7/warning/now 已废弃返回403）

认证方式（官方推荐 JWT，兼容 API KEY）:
- jwt: Ed25519 私钥（PEM）+ kid（凭据ID）+ sub（项目ID），
       插件内生成短时效 JWT 置于 Authorization: Bearer 头
- key: X-QW-Api-Key 请求头直传 API KEY

注意: 免费订阅的 API Host 为账号专属域名（形如 xxx.re.qweatherapi.com），
必须在配置中填写，不能使用通用域名。
"""
import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional

import httpx
import jwt as pyjwt

from core.plugins.weather_base import WeatherPluginBase

logger = logging.getLogger(__name__)

PLUGIN_NAME = "weather_qweather"

# 单次请求超时（秒）
REQUEST_TIMEOUT = 10
# JWT 有效期（秒），和风要求不超过 24 小时，短时效更安全
JWT_TTL = 900


class Plugin(WeatherPluginBase):
    """和风天气数据源插件"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "和风天气"
        self.version = "1.1.1"
        self.description = "和风天气数据源（点位实况+逐时+预报+官方预警）"
        self.module = "weather"

    # ------------------------------------------------------------------
    # 配置声明
    # ------------------------------------------------------------------
    def get_config_schema(self) -> list:
        """天气管理页面动态渲染的配置表单定义（按认证方式分组展示）"""
        return [
            {"key": "auth_type", "label": "认证方式", "type": "select", "required": True,
             "options": [{"label": "JWT（官方推荐）", "value": "jwt"},
                         {"label": "API KEY", "value": "key"}],
             "placeholder": "选择认证方式"},
            {"key": "api_host", "label": "API Host", "type": "input", "required": True,
             "placeholder": "账号专属域名，如 abcxyz.re.qweatherapi.com（控制台-设置中查看）"},
            {"key": "private_key", "label": "Ed25519 私钥", "type": "textarea", "required": False,
             "placeholder": "JWT 认证用，PEM 格式（-----BEGIN PRIVATE KEY----- 开头）"},
            {"key": "kid", "label": "凭据 ID (kid)", "type": "input", "required": False,
             "placeholder": "JWT 认证用，控制台-项目管理-凭据 ID"},
            {"key": "project_id", "label": "项目 ID (sub)", "type": "input", "required": False,
             "placeholder": "JWT 认证用，控制台-项目管理-项目 ID"},
            {"key": "api_key", "label": "API KEY", "type": "input", "required": False,
             "placeholder": "API KEY 认证用（auth_type=key 时必填）"},
        ]

    # ------------------------------------------------------------------
    # 认证
    # ------------------------------------------------------------------
    def _build_headers(self) -> Dict[str, str]:
        """按认证方式构造请求头（JWT 每次调用重新签发短时效令牌）"""
        config = self.config or {}
        auth_type = str(config.get("auth_type") or "jwt").strip().lower()

        if auth_type == "key":
            api_key = str(config.get("api_key") or "").strip()
            if not api_key:
                raise ValueError("API KEY 认证方式下未配置 api_key")
            return {"X-QW-Api-Key": api_key}

        # JWT 认证: EdDSA(Ed25519) 签名, header 带 kid, payload 带 sub/iat/exp
        private_key = str(config.get("private_key") or "").strip()
        kid = str(config.get("kid") or "").strip()
        project_id = str(config.get("project_id") or "").strip()
        if not (private_key and kid and project_id):
            raise ValueError("JWT 认证需完整配置 private_key/kid/project_id")

        now = int(time.time())
        token = pyjwt.encode(
            # iat 提前 30 秒容忍服务器时钟偏差
            {"sub": project_id, "iat": now - 30, "exp": now + JWT_TTL},
            private_key,
            algorithm="EdDSA",
            headers={"kid": kid},
        )
        return {"Authorization": f"Bearer {token}"}

    def _api_host(self) -> str:
        """解析 API Host（免费订阅为账号专属域名，必填）"""
        host = str((self.config or {}).get("api_host") or "").strip()
        # 容错去掉用户误填的协议前缀与尾部斜杠
        host = host.removeprefix("https://").removeprefix("http://").rstrip("/")
        if not host:
            raise ValueError("未配置 API Host（控制台-设置中的账号专属域名）")
        return host

    async def _api_get(self, client: httpx.AsyncClient, path: str,
                       location: str) -> Optional[Dict[str, Any]]:
        """
        调用和风 v7 接口并校验业务码

        返回: 响应 JSON；业务码非 200 时返回 None（预警等可选接口容忍失败）
        """
        url = f"https://{self._api_host()}{path}"
        resp = await client.get(url, params={"location": location, "lang": "zh"},
                                headers=self._build_headers())
        data = resp.json()
        if str(data.get("code")) != "200":
            logger.warning(f"[{PLUGIN_NAME}] {path} 返回业务码 {data.get('code')}")
            return None
        return data

    async def _fetch_alerts(self, client: httpx.AsyncClient,
                           latitude: float, longitude: float) -> Optional[Dict[str, Any]]:
        """调用和风新版灾害预警接口 weatheralert/v1/current/{lat}/{lon}

        旧 /v7/warning/now 已对多数订阅返回 403 FORBIDDEN；新接口路径以
        纬度/经度分段传参，返回 alerts 数组（id/eventType/color/headline/
        description/时间）。预警为可选数据，失败仅记警告返回 None。
        """
        lat = round(float(latitude), 2)
        lon = round(float(longitude), 2)
        url = f"https://{self._api_host()}/weatheralert/v1/current/{lat}/{lon}"
        try:
            resp = await client.get(
                url, params={"localTime": "false", "lang": "zh"},
                headers=self._build_headers())
        except httpx.HTTPError as e:
            logger.warning(f"[{PLUGIN_NAME}] 预警接口请求异常: {e}")
            return None
        if resp.status_code != 200:
            logger.warning(
                f"[{PLUGIN_NAME}] /weatheralert/v1/current 返回 {resp.status_code}")
            return None
        try:
            return resp.json()
        except ValueError:
            logger.warning(f"[{PLUGIN_NAME}] 预警接口响应非JSON")
            return None

    @staticmethod
    def _to_float(value, default=None):
        """安全转 float（和风字段均为字符串）"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _fmt_obs_time(value: str) -> str:
        """
        观测时间归一化为全站统一时间格式（%Y-%m-%d %H:%M:%S）

        和风返回 ISO8601 带时区字符串（如 2026-07-26T17:52+08:00），
        直接透传前端会出现 'T' 分隔符，与系统时间格式约定不符；
        解析失败时原样返回，不影响快照主体更新。
        """
        if not value:
            return ""
        try:
            return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value

    # ------------------------------------------------------------------
    # 数据获取（归一化为统一 DTO）
    # ------------------------------------------------------------------
    async def fetch_weather(self, loc: Dict[str, Any]) -> Dict[str, Any]:
        """
        拉取实况 + 24 小时逐时 + 3 天预报 + 官方灾害预警并归一化

        实况为必需数据（失败即整体失败）；逐时/预报/预警任一失败仅记警告，
        对应字段返回空，不影响快照主体更新。
        """
        longitude = loc.get("longitude") or 0
        latitude = loc.get("latitude") or 0
        if not longitude or not latitude:
            return {"status": "error", "source": PLUGIN_NAME,
                    "msg": "产区未设置经纬度，无法定位（和风仅支持经纬度）"}
        # 和风要求经纬度小数不超过 2 位
        location = f"{round(float(longitude), 2)},{round(float(latitude), 2)}"

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                now_data = await self._api_get(client, "/v7/weather/now", location)
                if not now_data:
                    return {"status": "error", "source": PLUGIN_NAME,
                            "msg": "实况查询失败（业务码非200，请检查凭据与Host）"}
                hourly_data = await self._api_get(client, "/v7/weather/24h", location)
                daily_data = await self._api_get(client, "/v7/weather/3d", location)
                # 预警接口：和风已迁移到 weatheralert/v1/current/{lat}/{lon}
                # 旧 /v7/warning/now 对多数订阅返回 403 FORBIDDEN，改用新接口
                warning_data = await self._fetch_alerts(client, latitude, longitude)
        except httpx.HTTPError as e:
            logger.warning(f"[{PLUGIN_NAME}] 请求和风接口异常: {e}")
            return {"status": "error", "source": PLUGIN_NAME, "msg": f"网络请求失败: {e}"}
        except ValueError as e:
            return {"status": "error", "source": PLUGIN_NAME, "msg": str(e)}

        now = now_data.get("now", {}) or {}
        return {
            "status": "success",
            "msg": "",
            "source": PLUGIN_NAME,
            "granularity": "point",
            "realtime": {
                "temp": self._to_float(now.get("temp")),
                "humidity": self._to_float(now.get("humidity")),
                "wind_dir": now.get("windDir", ""),
                "wind_scale": now.get("windScale", ""),
                "text": now.get("text", ""),
                "icon": now.get("icon", ""),
                "obs_time": self._fmt_obs_time(now.get("obsTime", "")),
            },
            "hourly": [
                {
                    "time": h.get("fxTime", ""),
                    "temp": self._to_float(h.get("temp")),
                    "text": h.get("text", ""),
                    "icon": h.get("icon", ""),
                    "precip_prob": self._to_float(h.get("pop")),
                }
                for h in ((hourly_data or {}).get("hourly") or [])
            ],
            "forecast": [
                {
                    "date": d.get("fxDate", ""),
                    "text_day": d.get("textDay", ""),
                    "text_night": d.get("textNight", ""),
                    "temp_max": self._to_float(d.get("tempMax")),
                    "temp_min": self._to_float(d.get("tempMin")),
                    "icon_day": d.get("iconDay", ""),
                    "wind_dir": d.get("windDirDay", ""),
                    "wind_scale": d.get("windScaleDay", ""),
                    "humidity": self._to_float(d.get("humidity")),
                    "precip": self._to_float(d.get("precip")),
                }
                for d in ((daily_data or {}).get("daily") or [])
            ],
            "alerts": [
                {
                    "alert_id": w.get("id", ""),
                    "type": (w.get("eventType") or {}).get("name", ""),
                    "level": (w.get("color") or {}).get("code", ""),
                    "title": w.get("headline", ""),
                    "text": w.get("description", ""),
                    "start_time": w.get("effectiveTime", ""),
                    "end_time": w.get("expireTime", ""),
                }
                for w in ((warning_data or {}).get("alerts") or [])
            ],
        }

    # ------------------------------------------------------------------
    # 连通性测试
    # ------------------------------------------------------------------
    async def test_connection(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        """用北京坐标真实调用实况接口探活（校验凭据+Host 有效性）"""
        if config:
            self.config = {**(self.config or {}), **config}
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                data = await self._api_get(client, "/v7/weather/now", "116.41,39.92")
            if data:
                return {"success": True, "message": "和风天气接口连通正常"}
            return {"success": False, "message": "接口返回业务码非200，请检查凭据/项目ID/Host"}
        except httpx.HTTPError as e:
            return {"success": False, "message": f"网络请求失败: {e}"}
        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            # Ed25519 私钥格式错误等签名异常
            return {"success": False, "message": f"凭据异常: {e}"}
