"""将 JJR owner-open 协议转换成公共硬件 DTO。"""

from core.hardware_types import HardwareDeviceData, HardwareError
from .client import FarmbotClient
from .properties import _merge_realtime_properties

FALLBACK_UNITS = {"envTemp": "℃", "envHum": "%RH", "envLux": "lux", "vari": "无量纲",
                  "chlorophy": "SPAD", "solarEnergyVol": "V", "batVol": "V", "signal4G": "dBm"}


class JjrAdapter(FarmbotClient):
    def __init__(self, config: dict):
        super().__init__(config=config)

    async def list_devices(self) -> list[HardwareDeviceData]:
        raw = await super().list_devices()
        result = []
        for item in raw:
            name = str(item.get("device_name") or "").strip()
            capabilities = ["realtime", "history"]
            if item.get("device_type") == "growth":
                capabilities.append("take_photo")
            result.append(HardwareDeviceData(
                provider_device_id=name, device_name=name, capabilities=capabilities,
                external_id=int(item.get("id") or 0), bot_id=int(item.get("bot_id") or 0),
                **{key: str(item.get(key) or "") for key in (
                    "device_type", "device_type_label", "nickname", "lat", "lng", "address",
                )},
            ))
        return result

    async def list_identifiers(self, device_id: str) -> list[dict]:
        rows = await super().list_identifiers(device_id)
        return self._units(rows)

    @staticmethod
    def _units(rows: list[dict]) -> list[dict]:
        return [{**row, "unit": row.get("unit") or FALLBACK_UNITS.get(row.get("identifier"), "")}
                for row in rows]

    async def read_realtime(self, device_id: str) -> list[dict]:
        identifiers = await self.list_identifiers(device_id)
        try:
            properties = await self.get_properties(device_id)
        except HardwareError:
            return identifiers
        return self._units(_merge_realtime_properties(identifiers, properties))
