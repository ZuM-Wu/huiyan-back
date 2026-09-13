"""
对象存储统一调度层

从 hy_configuration 读取 oss_method 配置，路由到对应存储插件实例。
内置进程内实例缓存（依赖单 worker 部署约束，main.py:4-6），
避免每次请求重新实例化的性能开销。

关键回落逻辑：oss_method 未配置或插件加载失败时，
upload() 优先返回 upload/ 下文件的站内稳定地址，并保留本地副本。
"""
import importlib
import logging
import re
from urllib.parse import quote

from core.db.base import async_session_factory
from core.config import BASE_DIR

logger = logging.getLogger(__name__)

# 默认存储方式
_DEFAULT_OSS_METHOD = "local_oss"


class OssService:
    """对象存储统一调度层"""

    def __init__(self):
        # 进程内插件实例缓存（依赖单 worker 部署约束）
        self._active_plugin = None
        self._active_method = None

    async def get_active_plugin(self):
        """
        获取激活的存储插件实例（带进程内缓存）

        流程:
        1. 缓存命中且方法未变则直接返回
        2. 否则读 oss_method 配置 → 动态加载插件类 → 实例化并缓存
        3. 加载失败返回 None（调用方负责回落）
        """
        # 读取全局存储方式及其插件私有配置。插件配置统一使用
        # {plugin_name}.* 前缀，避免运行时实例拿到 manifest 默认值而丢失数据库配置。
        async with async_session_factory() as db:
            from core.config_manager import ConfigManager
            cm = ConfigManager()
            oss_method = await cm.get("oss_method", db) or _DEFAULT_OSS_METHOD
            plugin_config = await cm.get_plugin_config(oss_method, db)

        if oss_method != _DEFAULT_OSS_METHOD:
            # 插件被禁用或卸载后，当前配置仍可能暂时保留；此时必须走本地回退，
            # 不能继续向已停止的远端存储发送上传请求。
            from core.plugin_query_service import get_plugin_by_name
            plugin_record = await get_plugin_by_name(oss_method)
            if not plugin_record or plugin_record.get("module") != "oss" or plugin_record.get("status") != 1:
                self.invalidate()
                return None

        # 缓存命中
        if self._active_plugin and self._active_method == oss_method:
            return self._active_plugin

        # 动态加载插件类并实例化
        try:
            module = importlib.import_module(
                f"plugins.oss.{oss_method}.plugin"
            )
            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls:
                plugin = plugin_cls(None, plugin_config)
                self._active_plugin = plugin
                self._active_method = oss_method
                logger.info("[OssService] 存储插件已加载: %s", oss_method)
                return plugin
            logger.warning("[OssService] 存储插件 %s 缺少 Plugin 类", oss_method)
        except Exception as e:
            logger.warning("[OssService] 加载存储插件 %s 失败: %s", oss_method, e)

        return None

    async def upload(
        self, save_path, save_name, original_name, ext,
        file_size, admin_id=None, source="admin",
    ):
        """
        统一上传入口，委托给激活的存储插件

        异常或插件不可用时回落到直链 URL（零回归保护）。
        """
        plugin = await self.get_active_plugin()
        if plugin:
            try:
                result = await plugin.oss_upload({
                    "save_path": save_path,
                    "save_name": save_name,
                    "original_name": original_name,
                    "ext": ext,
                    "file_size": file_size,
                    "admin_id": admin_id,
                    "source": source,
                })
                if result.get("status") == "success":
                    local_path = self._local_path_for_file(save_path)
                    if local_path:
                        # 业务层只持久化站内稳定地址；插件返回的供应商地址
                        # 仍保留在 storage_url，便于运维调用方按需读取。
                        data = result.setdefault("data", {})
                        if isinstance(data, dict):
                            data.setdefault("storage_url", data.get("url", ""))
                            data["url"] = self.stable_url(local_path)
                    return result
                logger.warning(
                    "[OssService] 存储插件返回失败: %s",
                    result.get("msg", "未知错误"),
                )
            except Exception as e:
                logger.warning("[OssService] 存储插件上传异常，回落直写: %s", e)

        # 回落：直接返回直链 URL（与原有行为一致）
        # 对象存储失败时仍登记本地归属，稳定地址才能在后续迁移和访问时准确回退。
        try:
            local_path = self._local_path_for_file(save_path)
            if local_path:
                from core.file_log_service import ensure_file_log
                await ensure_file_log(
                    save_name, original_name, ext, f"/upload/{local_path}", file_size,
                    admin_id, source, oss_method="local_oss", local_path=local_path,
                    object_key=local_path,
                )
        except Exception:
            logger.exception("[OssService] 本地回退文件日志写入失败")
        return {
            "status": "success",
            "data": {"url": self.stable_url(local_path) if local_path else f"/upload/common/{save_name}"},
        }

    @staticmethod
    def _local_path_for_file(save_path) -> str:
        """返回 upload/ 下的 POSIX 路径，越界路径返回空字符串。"""
        from pathlib import Path

        upload_root = BASE_DIR.joinpath("upload").resolve()
        local_file = Path(str(save_path or "")).resolve()
        if upload_root not in local_file.parents or local_file == upload_root:
            return ""
        return local_file.relative_to(upload_root).as_posix()

    def invalidate(self):
        """失效缓存（切换 oss_method 时调用）"""
        self._active_plugin = None
        self._active_method = None
        logger.info("[OssService] 插件实例缓存已失效")

    @staticmethod
    def normalize_local_path(value: str) -> str:
        """规范化 upload/ 下的相对路径，拒绝路径穿越和绝对路径。"""
        raw = str(value or "").strip().replace("\\", "/")
        if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or any(ord(char) < 32 for char in raw):
            raise ValueError("文件路径不合法")
        parts = [part for part in raw.split("/") if part]
        if not parts or any(part in {".", ".."} or ":" in part for part in parts):
            raise ValueError("文件路径不合法")
        return "/".join(parts)

    def stable_url(self, local_path: str) -> str:
        """构造不携带凭据和临时签名的站内稳定文件地址。"""
        normalized = self.normalize_local_path(local_path)
        return f"/api/v1/storage/files/{quote(normalized, safe='/')}"

    async def resolve_file_url(self, local_path: str, *, action: str = "preview") -> str | None:
        """根据文件日志按实际存储插件生成访问地址，失败时回退本地直链。"""
        from core.file_log_service import get_file_log

        normalized = self.normalize_local_path(local_path)
        record = await get_file_log(normalized)
        local_url = f"/upload/{normalized}"
        local_file = (BASE_DIR / "upload" / normalized).resolve()
        upload_root = (BASE_DIR / "upload").resolve()
        local_available = upload_root in local_file.parents and local_file.is_file()
        if not record or not record.oss_method or record.oss_method == "local_oss":
            return local_url if local_available else None
        try:
            from core.plugin_query_service import get_plugin_by_name
            plugin_record = await get_plugin_by_name(record.oss_method)
            if not plugin_record or plugin_record.get("module") != "oss" or plugin_record.get("status") != 1:
                return local_url if local_available else None
            async with async_session_factory() as db:
                from core.config_manager import ConfigManager
                config = await ConfigManager().get_plugin_config(record.oss_method, db)
            module = importlib.import_module(f"plugins.oss.{record.oss_method}.plugin")
            plugin_cls = getattr(module, "Plugin", None)
            if not plugin_cls:
                return local_url
            plugin = plugin_cls(None, config)
            object_key = record.object_key or record.save_name
            result = await plugin.oss_download({
                # object_key 来自文件日志，是历史真实键，不能因插件保存路径后来变更而被拒绝。
                "file_id": object_key, "_trusted_object_key": True,
                "timeout": 3600,
                "action": action,
            })
            if result.get("status") == "success":
                return result.get("data", {}).get("url") or local_url
        except Exception as exc:
            logger.warning("[OssService] 生成文件访问地址失败，回退本地: %s", exc)
        return local_url if local_available else None


# 全局单例
oss_service = OssService()
