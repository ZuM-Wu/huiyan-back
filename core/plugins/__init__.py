"""
服务型插件抽象基类包
提供短信插件 (SmsPluginBase)、邮件插件 (MailPluginBase) 与对象存储插件 (OssPluginBase) 的统一接口规范
"""
from core.plugins.base import SmsPluginBase, MailPluginBase, OssPluginBase

__all__ = ["MailPluginBase", "OssPluginBase", "SmsPluginBase"]
