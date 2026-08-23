"""
对象存储统一调度层

从 hy_configuration 读取 oss_method 配置，路由到对应存储插件实例。
内置进程内实例缓存（依赖单 worker 部署约束，main.py:4-6），
避免每次请求重新实例化的性能开销。

关键回落逻辑：oss_method 未配置或插件加载失败时，
upload() 直接返回 /upload/common/{save_name}（与原有行为一致，保证零回归）。
"""
import importlib
import logging

from core.db.base import async_session_factory

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
        # 读 oss_method 配置
        async with async_session_factory() as db:
            from core.config_manager import ConfigManager
            cm = ConfigManager()
            oss_method = await cm.get("oss_method", db) or _DEFAULT_OSS_METHOD

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
                plugin = plugin_cls(None, {})
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
                    return result
                logger.warning(
                    "[OssService] 存储插件返回失败: %s",
                    result.get("msg", "未知错误"),
                )
            except Exception as e:
                logger.warning("[OssService] 存储插件上传异常，回落直写: %s", e)

        # 回落：直接返回直链 URL（与原有行为一致）
        return {
            "status": "success",
            "data": {"url": f"/upload/common/{save_name}"},
        }

    def invalidate(self):
        """失效缓存（切换 oss_method 时调用）"""
        self._active_plugin = None
        self._active_method = None
        logger.info("[OssService] 插件实例缓存已失效")


# 全局单例
oss_service = OssService()
