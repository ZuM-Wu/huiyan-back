"""七牛云对象存储插件主类。"""

import logging
from pathlib import Path

from core.file_log_service import ensure_file_log
from core.plugins.base import OssPluginBase

from .auth import permission_tree
from .service import QiniuService, build_object_key, normalize_object_key

logger = logging.getLogger(__name__)


class Plugin(OssPluginBase):
    """七牛云存储实现，配置由平台按 qiniu_oss.* 读取。"""

    name = "qiniu_oss"
    title = "七牛云对象存储"
    version = "1.0.1"
    module = "oss"

    def __init__(self, db_session=None, config: dict | None = None):
        super().__init__(db_session, config)
        self.description = "七牛云对象存储及文件管理"

    def _service(self) -> QiniuService:
        return QiniuService(self.config)

    async def install(self) -> bool:
        """幂等写入默认配置，不创建插件私有表。"""
        if not self.db:
            return True
        from core.config_manager import ConfigManager

        manager = ConfigManager()
        for key, value in (self.config or {}).items():
            full_key = f"{self.name}.{key}"
            if await manager.get(full_key, self.db) is None:
                await manager.set(full_key, str(value), self.db, description="七牛云对象存储配置")
        logger.info("[qiniu_oss] 对象存储插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """卸载时由平台清理 qiniu_oss.* 配置和权限。"""
        return await super().uninstall()

    def get_permissions(self) -> list[dict]:
        return permission_tree()

    def get_routers(self) -> list:
        from .router_admin import router

        return [router]

    def get_config_schema(self) -> list[dict]:
        return [
            {"key": "access_key", "label": "AccessKey", "type": "input", "default": "", "sensitive": True},
            {"key": "secret_key", "label": "SecretKey", "type": "password", "default": "", "sensitive": True},
            {"key": "bucket", "label": "存储空间", "type": "input", "default": ""},
            {"key": "domain", "label": "加速域名", "type": "input", "default": "", "placeholder": "https://cdn.example.com"},
            {
                "key": "region", "label": "区域", "type": "select", "default": "z0",
                "options": [
                    {"label": "华东-浙江", "value": "z0"},
                    {"label": "华北-河北", "value": "z1"},
                    {"label": "华南-广东", "value": "z2"},
                    {"label": "北美", "value": "na0"},
                    {"label": "东南亚", "value": "as0"},
                ],
            },
            {
                "key": "private", "label": "私有空间", "type": "switch", "default": "0",
                "options": [
                    {"label": "公开", "value": "0"},
                    {"label": "私有", "value": "1"},
                ],
            },
            {"key": "save_path", "label": "保存路径", "type": "input", "default": "", "placeholder": "例如 uploads/images"},
        ]

    async def oss_link(self) -> dict:
        """测试七牛存储空间连通性。"""
        try:
            await self._service().test_connection()
            return {"status": "success", "msg": "七牛云连接正常"}
        except Exception as exc:
            logger.warning("[qiniu_oss] 连通检测失败: %s", exc)
            return {"status": "error", "msg": str(exc)}

    async def oss_has_data(self) -> dict:
        """统计保存路径下是否已有对象。"""
        try:
            result = await self._service().list_files(limit=1)
            count = len(result.get("items", []))
            return {"status": "success", "data": {"has_data": count > 0, "file_count": count}}
        except Exception as exc:
            return {"status": "error", "msg": str(exc), "data": {"has_data": False, "file_count": 0}}

    async def oss_upload(self, params: dict) -> dict:
        """上传已由统一上传服务落盘的文件，并写入七牛归属日志。"""
        save_name = str(params.get("save_name") or "")
        supplied_key = str(params.get("object_key") or "").strip()
        service = self._service()
        if supplied_key and params.get("_trusted_object_key"):
            from .service import normalize_object_key
            key = normalize_object_key(supplied_key)
        elif supplied_key:
            key = service.object_key(supplied_key)
        else:
            key = build_object_key(self.config.get("save_path", ""), save_name)
        local_file = Path(str(params.get("save_path") or "")).resolve()
        upload_root = (Path(__file__).resolve().parents[3] / "upload").resolve()
        local_path = ""
        if upload_root in local_file.parents:
            local_path = local_file.relative_to(upload_root).as_posix()
        await self._service().upload_file(str(params.get("save_path") or ""), key)
        url = await self._service().access_url(key)
        log_url = url
        if local_path:
            from core.oss_service import oss_service
            log_url = oss_service.stable_url(local_path)
        await ensure_file_log(
            save_name, str(params.get("original_name") or ""), str(params.get("ext") or ""),
            log_url, int(params.get("file_size") or 0), params.get("admin_id"),
            str(params.get("source") or "admin"), oss_method=self.name,
            local_path=local_path, object_key=key,
        )
        return {"status": "success", "data": {"url": url, "key": key}}

    async def oss_check_file(self, params: dict) -> dict:
        """按七牛 ETag 判断迁移文件是否需要上传，禁止冲突覆盖。"""
        import asyncio
        import qiniu.utils

        supplied_key = str(params.get("object_key") or "").strip()
        service = self._service()
        key = service.object_key(supplied_key) if not params.get("_trusted_object_key") else normalize_object_key(supplied_key)
        local_path = str(params.get("save_path") or "")
        remote = await service.stat_file(key)
        if not remote:
            return {"status": "missing"}
        remote_hash = str(remote.get("hash") or "").strip()
        if not remote_hash:
            return {"status": "unsupported", "msg": "七牛对象缺少 ETag，无法判断文件是否一致"}
        local_hash = await asyncio.to_thread(qiniu.utils.etag, local_path)
        if remote_hash == local_hash:
            return {"status": "same"}
        return {"status": "conflict", "msg": "远端已存在同名对象且内容不同"}

    async def oss_download(self, params: dict) -> dict:
        """根据文件标识返回公开或私有访问地址。"""
        file_id = str(params.get("file_id") or "")
        service = self._service()
        if params.get("_trusted_object_key"):
            # 仅由 core.oss_service 根据已登记日志调用；管理员接口不传此标记。
            from .service import normalize_object_key
            key = normalize_object_key(file_id)
        else:
            key = service.object_key(file_id) if "/" in file_id else build_object_key(self.config.get("save_path", ""), file_id)
        action = str(params.get("action") or "preview")
        url = await service.access_url(key, int(params.get("timeout") or 3600), action)
        return {"status": "success", "data": {"url": url, "key": key}}

    async def test_connection(self, config: dict | None = None) -> dict:
        """兼容平台插件配置测试门面。"""
        return {"success": (await self.oss_link()).get("status") == "success"}
