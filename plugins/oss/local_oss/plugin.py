"""
本地对象存储插件（LocalOss）

对标 ZJMF LocalOss.php，内置不可卸载的本地存储实现。
文件存储于服务器本地磁盘 upload/common/ 目录，通过 StaticFiles 直接访问。
"""
import os
import logging

from core.plugins.base import OssPluginBase
from core.config import BASE_DIR
from core.file_log_service import count_by_oss_method, ensure_file_log, find_file_url

logger = logging.getLogger(__name__)


class Plugin(OssPluginBase):
    """本地对象存储插件"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = "local_oss"
        self.title = "本地存储"
        self.version = "1.0.0"
        self.description = "内置本地对象存储，文件存储于服务器本地磁盘"
        self.module = "oss"

    async def install(self) -> bool:
        """安装：调用父类写配置 + 写入 oss_method 默认值"""
        await super().install()
        if self.db:
            from core.config_manager import ConfigManager
            cm = ConfigManager()
            existing = await cm.get("oss_method", self.db)
            if existing is None:
                await cm.set(
                    "oss_method", "local_oss", self.db,
                    description="对象存储方式（默认本地存储）",
                )
        logger.info("[local_oss] 本地存储插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """内置不可卸载（对标 ZJMF LocalOss.php:39）"""
        logger.warning("[local_oss] 内置存储插件不可卸载")
        return False

    async def oss_link(self) -> dict:
        """检测对象存储是否联通（本地存储检测目录可写）"""
        upload_dir = BASE_DIR / "upload" / "common"
        if upload_dir.exists() and os.access(str(upload_dir), os.W_OK):
            return {"status": "success", "msg": "本地存储目录可写"}
        return {"status": "error", "msg": "本地存储目录不可写"}

    async def oss_has_data(self) -> dict:
        """检测对象存储是否有数据（用于切换存储方式前的迁移评估）"""
        count = await count_by_oss_method("local_oss")
        return {
            "status": "success",
            "data": {"has_data": count > 0, "file_count": count},
        }

    async def oss_upload(self, params: dict) -> dict:
        """
        上传/搬移文件

        本地存储场景：文件已在 upload/common/ 落盘，无需搬移（no-op）。
        写入 file_log 记录存储归属，返回访问 URL。
        """
        save_name = params.get("save_name", "")
        original_name = params.get("original_name", "")
        ext = params.get("ext", "")
        file_size = params.get("file_size", 0)
        admin_id = params.get("admin_id")
        source = params.get("source", "admin")

        # 本地存储：文件已在 upload/common/ 落盘，无需搬移
        url = f"/upload/common/{save_name}"

        # 写入 file_log 记录（save_name 唯一索引，已存在则跳过）
        await ensure_file_log(
            save_name, original_name, ext, url, file_size, admin_id, source,
        )
        logger.info("[local_oss] 文件记录已写入 file_log: %s", save_name)

        return {"status": "success", "data": {"url": url}}

    async def oss_download(self, params: dict) -> dict:
        """
        获取文件下载地址

        MVP 阶段返回直链 URL（签名临时 URL 为增量能力，见步骤9）。
        """
        file_id = params.get("file_id", "")

        url = await find_file_url(file_id)
        if not url:
            return {"status": "error", "msg": "文件记录不存在"}

        # MVP 阶段返回直链 URL
        return {"status": "success", "data": {"url": url}}
