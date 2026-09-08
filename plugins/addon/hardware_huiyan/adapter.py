"""慧眼内置平台适配器，直接读取本插件数据，不访问自身 HTTP 服务。"""

from core.hardware_types import HardwareDeviceData, HardwareError, HardwareMetric
from . import service


class HuiyanAdapter:
    def __init__(self, config: dict):
        self.config = config

    async def list_devices(self) -> list[HardwareDeviceData]:
        rows = await service.list_registered_devices()
        return [HardwareDeviceData(
            provider_device_id=item["device_id"], device_name=item["device_name"],
            device_type=item["device_type"], device_type_label=item["device_type_label"],
            nickname=item["nickname"] or item["device_name"], capabilities=["realtime"], external_id=item["id"],
            lat=item["latitude"], lng=item["longitude"], address=item["address"],
        ) for item in rows]

    async def list_identifiers(self, device_id: str) -> list[dict]:
        item = await service.read_registered_device(device_id)
        rows = item.get("metadata", {}).get("identifiers", [])
        return [HardwareMetric.model_validate(row).model_dump(by_alias=True) for row in rows]

    async def read_realtime(self, device_id: str) -> list[dict]:
        return await self.list_identifiers(device_id)

    async def get_history(self, device_id: str, **kwargs):
        raise HardwareError("慧眼平台尚未启用历史存储", 400)

    async def take_photo(self, device_id: str):
        raise HardwareError("慧眼平台尚未启用设备控制", 400)
