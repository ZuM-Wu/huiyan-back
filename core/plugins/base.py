"""
通知插件抽象基类
定义短信/邮件插件的统一接口规范

- SmsPluginBase: 短信插件基类（send_cn_sms/send_global_sms 等方法）
- MailPluginBase: 邮件插件基类（send_email 方法）

插件返回值统一约定:
    {"status": "success", "msg": "...", 其他扩展字段}
    {"status": "error", "msg": "错误原因"}
"""
import logging
from abc import abstractmethod
from typing import Dict, Any, Optional

from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)


class SmsPluginBase(BasePlugin):
    """
    短信插件抽象基类

    子类必须实现 send_cn_sms()，其余方法按需覆写。
    params 通用字段:
        mobile: str          手机号（国际短信需带区号）
        content: str         已完成变量替换的最终内容
        template_id: str     第三方模板 ID（模板短信必需）
        template_param: dict 原始模板变量（供第三方模板发送）
        config: dict         插件配置（appid/secret 等）
    """

    module = "sms"

    async def install(self) -> bool:
        """默认安装：写入 plugin.json 中声明的默认配置"""
        if not self.db:
            return True
        from core.config_manager import ConfigManager
        cm = ConfigManager()
        for key, value in (self.config or {}).items():
            existing = await cm.get(key, self.db)
            if existing is None:
                await cm.set(key, str(value), self.db, description=f"{self.name} 短信插件配置")
        logger.info(f"[{self.name}] 短信插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """默认卸载：清理以插件名为前缀的配置"""
        if not self.db:
            return True
        from sqlalchemy import delete
        from core.db.configuration import ConfigurationModel
        await self.db.execute(
            delete(ConfigurationModel).where(
                ConfigurationModel.key.like(f"{self.name}.%")
            )
        )
        logger.info(f"[{self.name}] 短信插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 发送接口
    # ------------------------------------------------------------------
    @abstractmethod
    async def send_cn_sms(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送国内短信

        Returns:
            {"status": "success/error", "msg": "...", "message_id": "..."}
        """
        ...

    async def send_global_sms(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """发送国际短信（默认不支持，子类按需覆写）"""
        return {"status": "error", "msg": f"插件 {self.name} 不支持国际短信"}

    # ------------------------------------------------------------------
    # 模板管理接口（可选实现）
    # ------------------------------------------------------------------
    async def create_cn_template(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """创建国内模板并提交第三方审核（可选）"""
        return {"status": "error", "msg": f"插件 {self.name} 不支持在线创建模板"}

    async def create_global_template(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """创建国际模板（可选）"""
        return {"status": "error", "msg": f"插件 {self.name} 不支持在线创建模板"}

    async def delete_cn_template(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """删除第三方国内模板（可选）"""
        return {"status": "error", "msg": f"插件 {self.name} 不支持在线删除模板"}

    async def delete_global_template(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """删除第三方国际模板（可选）"""
        return {"status": "error", "msg": f"插件 {self.name} 不支持在线删除国际模板"}

    async def get_template_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """查询第三方模板审核状态（可选）"""
        return {"status": "error", "msg": f"插件 {self.name} 不支持查询模板状态"}

    async def test_connection(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """测试插件连接（配置有效性校验，可选覆写）"""
        return {"success": True, "message": "插件已就绪（未实现真实连接测试）"}


class OssPluginBase(BasePlugin):
    """
    对象存储插件抽象基类

    对标 ZJMF LocalOss 四方法约定（link/data/upload/download），
    子类必须实现四个核心方法，其余方法按需覆写。
    方法名统一使用 oss_ 前缀，避免与框架保留方法冲突。

    返回值统一约定:
        {"status": "success", "msg": "...", "data": {...}}
        {"status": "error", "msg": "错误原因"}
    """

    module = "oss"

    async def install(self) -> bool:
        """默认安装：写入 plugin.json 中声明的默认配置"""
        if not self.db:
            return True
        from core.config_manager import ConfigManager
        cm = ConfigManager()
        for key, value in (self.config or {}).items():
            existing = await cm.get(key, self.db)
            if existing is None:
                await cm.set(key, str(value), self.db, description=f"{self.name} 对象存储插件配置")
        logger.info(f"[{self.name}] 对象存储插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """默认卸载：清理以插件名为前缀的配置"""
        if not self.db:
            return True
        from sqlalchemy import delete
        from core.db.configuration import ConfigurationModel
        await self.db.execute(
            delete(ConfigurationModel).where(
                ConfigurationModel.key.like(f"{self.name}.%")
            )
        )
        logger.info(f"[{self.name}] 对象存储插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 存储契约方法（对标 ZJMF LocalOss 四方法）
    # ------------------------------------------------------------------
    @abstractmethod
    async def oss_link(self) -> Dict[str, Any]:
        """
        检测对象存储是否联通

        Returns:
            {"status": "success"/"error", "msg": "..."}
        """
        ...

    @abstractmethod
    async def oss_has_data(self) -> Dict[str, Any]:
        """
        检测对象存储是否有数据（用于切换存储方式前的迁移评估）

        Returns:
            {"status": "success", "data": {"has_data": bool, "file_count": int}}
        """
        ...

    @abstractmethod
    async def oss_upload(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        上传/搬移文件到对象存储

        params 通用字段:
            save_path: str     文件已落盘的完整路径
            save_name: str     存储文件名（UUID重命名后）
            original_name: str  原始文件名
            ext: str            扩展名（含点号）
            file_size: int      文件字节数
            admin_id: int       上传管理员ID（可空）
            source: str         来源（admin/farmer/system）

        Returns:
            {"status": "success", "data": {"url": "访问地址"}}
        """
        ...

    @abstractmethod
    async def oss_download(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        获取文件下载地址（生成签名临时URL或直链）

        params 通用字段:
            file_id: str    文件标识（file_log.uuid 或 save_name）
            timeout: int    过期时间（秒，默认300）
            action: str     动作（preview预览/download下载）

        Returns:
            {"status": "success", "data": {"url": "下载地址"}}
        """
        ...

class MailPluginBase(BasePlugin):
    """
    邮件插件抽象基类

    子类必须实现 send_email()。
    params 通用字段:
        to: str              收件邮箱
        subject: str         邮件主题（已替换变量）
        content: str         邮件正文 HTML（已替换变量）
        attachments: list    附件 URL 列表（可选）
        config: dict         插件配置（SMTP 服务器/API 密钥等）
    """

    module = "mail"

    async def install(self) -> bool:
        """默认安装：写入 plugin.json 中声明的默认配置"""
        if not self.db:
            return True
        from core.config_manager import ConfigManager
        cm = ConfigManager()
        for key, value in (self.config or {}).items():
            existing = await cm.get(key, self.db)
            if existing is None:
                await cm.set(key, str(value), self.db, description=f"{self.name} 邮件插件配置")
        logger.info(f"[{self.name}] 邮件插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """默认卸载：清理以插件名为前缀的配置"""
        if not self.db:
            return True
        from sqlalchemy import delete
        from core.db.configuration import ConfigurationModel
        await self.db.execute(
            delete(ConfigurationModel).where(
                ConfigurationModel.key.like(f"{self.name}.%")
            )
        )
        logger.info(f"[{self.name}] 邮件插件卸载完成")
        return True

    @abstractmethod
    async def send_email(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送邮件

        Returns:
            {"status": "success/error", "msg": "...", "msg_id": "..."}
        """
        ...

    async def test_connection(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """测试插件连接（配置有效性校验，可选覆写）"""
        return {"success": True, "message": "插件已就绪（未实现真实连接测试）"}
