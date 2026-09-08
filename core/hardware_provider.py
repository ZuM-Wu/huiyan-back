"""硬件来源公开门面：声明、资源校验、配置装配和 owner 运行代次。"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import ValidationError

from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from core.hardware_types import HardwareDeviceData, HardwareError, HardwareHistory, HardwareMetric, HardwareProvider
from core.platform.resource import resource_registry

logger = logging.getLogger(__name__)


class HardwareProviderRegistry:
    """单 worker 下按 owner 隔离，停用先关闭门禁再等待正在提交的短事务。"""

    def __init__(self):
        self._providers: dict[str, HardwareProvider] = {}
        self._generations: dict[str, int] = {}
        self._blocked: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, owner: str, declarations: list, root: Path, metadata: dict) -> None:
        if not isinstance(declarations, list) or len(declarations) > 1:
            raise ValueError("每个插件最多声明一个硬件来源")
        if not declarations:
            return
        declaration = declarations[0]
        if not isinstance(declaration, HardwareProvider) or declaration.provider_id != owner:
            raise ValueError("硬件来源标识必须为所属插件名")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", owner) or not declaration.title.strip():
            raise ValueError("硬件来源标识或名称无效")
        if metadata.get("module") != "addon" or not callable(declaration.factory):
            raise ValueError("硬件来源必须由 Addon 声明适配器工厂")
        if owner in self._providers:
            raise ValueError("硬件来源重复注册")
        for item in declaration.device_types:
            if not isinstance(item, dict) or not item.get("value") or not item.get("label"):
                raise ValueError("硬件类型必须包含 value 和 label")
        self._validate_view(owner, declaration, root)
        self._providers[owner] = declaration
        self._blocked.discard(owner)
        self._generations[owner] = self._generations.get(owner, 0) + 1

    def _validate_view(self, owner: str, declaration: HardwareProvider, root: Path) -> None:
        from core.plugin_page_validator import _safe_file, _validate_source

        view = declaration.detail_view
        if not view:
            return
        dependencies = tuple(item["key"] for item in resource_registry.snapshot(owner)
                             if item["path"].endswith(".js") and item["key"] != view.entry)
        for key in (view.entry, *view.styles, *dependencies):
            resource = resource_registry.resolve("admin", key, owner)
            if not resource:
                raise ValueError(f"硬件详情资源未登记: {key}")
            if not resource["path"].startswith("static/"):
                raise ValueError("硬件详情资源必须位于插件 static 目录")
            path = _safe_file(root.resolve(), resource["path"])
            expected = ".css" if key in view.styles else ".js"
            if path.suffix != expected:
                raise ValueError("硬件详情组件必须是本地 JS 与 CSS")
            source = path.read_text(encoding="utf-8")
            _validate_source(path, source)
            if expected == ".js":
                self._validate_imports(owner, root, path, source)
            if expected == ".css":
                selectors = re.findall(r"(?:^|\})\s*([^{}]+)\{", source)
                if any(not selector.strip().startswith(f'.hardware-provider-{owner}') for group in selectors for selector in group.split(',')):
                    raise ValueError("硬件详情样式必须限定在自身来源容器内")

    @staticmethod
    def _validate_imports(owner: str, root: Path, path: Path, source: str) -> None:
        """组件依赖也必须在当前插件资源清单中，禁止旁路载入外部模块。"""
        from core.plugin_page_validator import _safe_file

        asset_root = (root / "static").resolve()
        registered = {(root / item["path"]).resolve() for item in resource_registry.snapshot(owner)}
        imports = re.findall(r"(?:from\s*|import\s*\()\s*['\"]([^'\"]+)['\"]", source)
        for target in imports:
            if not target.startswith("./"):
                raise ValueError("硬件详情依赖必须是当前插件本地相对模块")
            relative = (path.parent / target).relative_to(asset_root).as_posix()
            dependency = _safe_file(asset_root, relative)
            if dependency not in registered:
                raise ValueError("硬件详情依赖未登记")

    def unregister_owner(self, owner: str) -> None:
        self._providers.pop(owner, None)
        self._blocked.add(owner)
        self._generations[owner] = self._generations.get(owner, 0) + 1

    def active(self, owner: str) -> bool:
        return owner in self._providers and owner not in self._blocked

    def owners(self) -> list[str]:
        return [owner for owner in self._providers if self.active(owner)]

    def check(self, owner: str, generation: int) -> None:
        if not self.active(owner) or generation != self._generations.get(owner):
            raise HardwareError("硬件来源已停用或发生变更，请重新打开设备", 503)

    async def block_owner(self, owner: str) -> None:
        self._blocked.add(owner)
        self._generations[owner] = self._generations.get(owner, 0) + 1
        async with self._locks.setdefault(owner, asyncio.Lock()):
            pass

    def unblock_owner(self, owner: str) -> None:
        self._blocked.discard(owner)

    @asynccontextmanager
    async def commit_guard(self, owner: str, generation: int):
        async with self._locks.setdefault(owner, asyncio.Lock()):
            self.check(owner, generation)
            yield

    def describe(self, owner: str) -> dict:
        declaration = self._providers.get(owner)
        active = self.active(owner)
        result = {"provider_id": owner, "title": declaration.title if declaration else owner,
                  "available": active, "device_types": list(declaration.device_types) if declaration else [],
                  "detail_view": None}
        if active and declaration and declaration.detail_view:
            view = declaration.detail_view
            result["detail_view"] = {"entry": self._resource_url(owner, view.entry),
                                     "styles": [self._resource_url(owner, key) for key in view.styles]}
        return result

    @staticmethod
    def _resource_url(owner: str, key: str) -> str:
        item = resource_registry.resolve("admin", key, owner)
        if not item:
            raise HardwareError("硬件详情资源已失效", 503)
        relative = item["path"].removeprefix("static/")
        return f"/plugin-assets/{owner}/{relative}?v={item['version']}.{item['resource_version']}"

    async def connect(self, owner: str):
        generation = self._generations.get(owner, 0)
        self.check(owner, generation)
        declaration = self._providers[owner]
        async with async_session_factory() as db:
            config = await ConfigManager().get_plugin_config(owner, db)
        self.check(owner, generation)
        try:
            adapter = declaration.factory(config)
        except HardwareError:
            raise
        except Exception as exc:
            raise HardwareError("硬件来源配置无效，无法创建连接", 503) from exc
        return HardwareConnection(owner, generation, adapter, self)


class HardwareConnection:
    """每次外部调用前后检查门禁，并在协议边界拒绝非法数据。"""

    def __init__(self, owner, generation, adapter, registry):
        self.owner, self.generation, self.adapter, self.registry = owner, generation, adapter, registry

    def check(self):
        self.registry.check(self.owner, self.generation)

    async def call(self, method: str, *args, **kwargs):
        self.check()
        try:
            async with asyncio.timeout(25):
                result = await getattr(self.adapter, method)(*args, **kwargs)
        except HardwareError:
            raise
        except TimeoutError as exc:
            raise HardwareError("硬件来源请求超时", 504) from exc
        except Exception as exc:
            logger.warning("硬件来源调用失败: owner=%s operation=%s type=%s", self.owner, method, type(exc).__name__)
            raise HardwareError("硬件来源调用失败") from exc
        self.check()
        return result

    async def list_devices(self):
        raw = await self.call("list_devices")
        try:
            if not isinstance(raw, list):
                raise ValueError("列表格式错误")
            devices = [HardwareDeviceData.model_validate(item) for item in raw]
            if len({item.provider_device_id for item in devices}) != len(devices):
                raise ValueError("重复来源设备ID")
            return devices
        except (ValidationError, ValueError) as exc:
            raise HardwareError("硬件来源返回的设备列表无效，本次同步未写入") from exc

    async def metrics(self, method: str, device_id: str):
        raw = await self.call(method, device_id)
        try:
            if not isinstance(raw, list):
                raise ValueError("指标格式错误")
            return [HardwareMetric.model_validate(item).model_dump(by_alias=True) for item in raw]
        except (ValidationError, ValueError) as exc:
            raise HardwareError("硬件来源返回的指标格式无效") from exc

    async def list_identifiers(self, device_id: str):
        return await self.metrics("list_identifiers", device_id)

    async def read_realtime(self, device_id: str):
        return await self.metrics("read_realtime", device_id)

    async def get_history(self, device_id: str, **kwargs):
        raw = await self.call("get_history", device_id, **kwargs)
        try:
            return HardwareHistory.model_validate(raw).model_dump(by_alias=True)
        except ValidationError as exc:
            raise HardwareError("硬件来源返回的历史数据格式无效") from exc

    async def take_photo(self, device_id: str):
        return await self.call("take_photo", device_id)

    def commit_guard(self):
        return self.registry.commit_guard(self.owner, self.generation)


hardware_provider_registry = HardwareProviderRegistry()


async def get_device_connection(device_id: int, capability: str = ""):
    """按本地主键解析来源，调用方无法利用同名编号跨来源访问。"""
    from core.hardware_device_service import get_hardware_device_info

    device = await get_hardware_device_info(device_id)
    if not device:
        raise HardwareError("设备不存在", 404)
    if capability and capability not in device["capabilities"]:
        raise HardwareError("设备不支持此操作", 400)
    connection = await hardware_provider_registry.connect(device["provider_id"])
    return connection, device
