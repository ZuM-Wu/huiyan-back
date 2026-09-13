# -*- coding: utf-8 -*-
"""
SMTP 邮件插件
基于 Python 标准库 smtplib 实现，通过线程池避免阻塞事件循环

支持:
- SSL(465) / TLS(587) / 明文(25) 三种加密方式
- HTML 正文
- 变量语法 {code} — 由核心 NoticeSender 完成替换后传入
"""
import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from typing import Dict, Any

from core.plugins.base import MailPluginBase

logger = logging.getLogger(__name__)

PLUGIN_NAME = "mail_smtp"


class Plugin(MailPluginBase):
    """SMTP 协议邮件插件"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "SMTP 邮件"
        self.version = "1.0.2"
        self.description = "标准 SMTP 协议邮件发送插件"
        self.module = "mail"

    # ------------------------------------------------------------------
    # 配置声明
    # ------------------------------------------------------------------
    def get_config_schema(self) -> list:
        """接口管理页面动态渲染的配置表单定义"""
        return [
            {"key": "host", "label": "SMTP 服务器", "type": "input", "required": True,
             "placeholder": "如 smtp.qq.com"},
            {"key": "port", "label": "端口", "type": "input", "default": "465",
             "placeholder": "SSL=465 / TLS=587 / 明文=25"},
            {"key": "username", "label": "账号", "type": "input", "required": True,
             "placeholder": "SMTP 登录账号（一般为邮箱地址）"},
            {"key": "password", "label": "密码/授权码", "type": "input", "required": True,
             "placeholder": "SMTP 登录密码或授权码"},
            {"key": "encryption", "label": "加密方式", "type": "select", "default": "ssl",
             "options": [
                 {"value": "ssl", "label": "SSL"},
                 {"value": "tls", "label": "TLS (STARTTLS)"},
                 {"value": "none", "label": "无加密"},
             ]},
            {"key": "from_email", "label": "发件邮箱", "type": "input", "required": True,
             "placeholder": "显示的发件人邮箱地址"},
            {"key": "from_name", "label": "发件人名称", "type": "input", "default": "慧眼护农",
             "placeholder": "显示的发件人名称"},
        ]

    # ------------------------------------------------------------------
    # 同步发送（在线程池中执行，避免阻塞事件循环）
    # ------------------------------------------------------------------
    @staticmethod
    def _send_sync(config: Dict[str, Any], to: str,
                   subject: str, content: str) -> Dict[str, Any]:
        """
        同步 SMTP 发送逻辑

        Args:
            config: host/port/username/password/encryption/from_email/from_name
            to: 收件邮箱
            subject: 邮件主题
            content: HTML 正文
        """
        host = config.get("host", "")
        port = int(config.get("port", 465) or 465)
        username = config.get("username", "")
        password = config.get("password", "")
        encryption = (config.get("encryption") or "ssl").lower()
        from_email = config.get("from_email") or username
        from_name = config.get("from_name") or "慧眼护农"

        if not host or not username or not password:
            return {"status": "error", "msg": "SMTP 服务器/账号/密码未配置"}

        # 验证 from_email 为纯 ASCII（邮箱地址不允许非 ASCII）
        try:
            from_email.encode("ascii")
        except (UnicodeEncodeError, AttributeError):
            return {"status": "error", "msg": "发件邮箱包含非法字符，请在接口管理中配置正确的邮箱地址"}

        # 构造 MIME 邮件（HTML 正文）
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((from_name, from_email), charset="utf-8")
        msg["To"] = to
        msg.attach(MIMEText(content, "html", "utf-8"))

        smtp = None
        try:
            if encryption == "ssl":
                smtp = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                smtp = smtplib.SMTP(host, port, timeout=30)
                if encryption == "tls":
                    smtp.starttls()

            smtp.login(username, password)
            smtp.sendmail(from_email, [to], msg.as_string())
            return {"status": "success", "msg": "发送成功", "msg_id": msg.get("Message-ID", "")}
        except smtplib.SMTPException as e:
            logger.error(f"[{PLUGIN_NAME}] SMTP 发送失败: {e}")
            return {"status": "error", "msg": f"SMTP 发送失败：{e}"}
        except OSError as e:
            logger.error(f"[{PLUGIN_NAME}] SMTP 连接失败: {e}")
            return {"status": "error", "msg": f"SMTP 连接失败：{e}"}
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 发送接口
    # ------------------------------------------------------------------
    async def send_email(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送邮件

        params: to/subject/content/attachments/config
        """
        config = params.get("config", {})
        to = params.get("to", "")
        subject = params.get("subject", "")
        content = params.get("content", "")

        if not to:
            return {"status": "error", "msg": "收件邮箱不能为空"}

        # 在线程池中执行阻塞的 SMTP 操作
        return await asyncio.to_thread(self._send_sync, config, to, subject, content)

    async def test_connection(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        """测试连接：尝试登录 SMTP 服务器"""
        cfg = config or {}
        if not cfg.get("host") or not cfg.get("username") or not cfg.get("password"):
            return {"success": False, "message": "SMTP 服务器/账号/密码未配置"}

        def _try_login() -> Dict[str, Any]:
            """尝试建立连接并登录"""
            encryption = (cfg.get("encryption") or "ssl").lower()
            port = int(cfg.get("port", 465) or 465)
            smtp = None
            try:
                if encryption == "ssl":
                    smtp = smtplib.SMTP_SSL(cfg["host"], port, timeout=15)
                else:
                    smtp = smtplib.SMTP(cfg["host"], port, timeout=15)
                    if encryption == "tls":
                        smtp.starttls()
                smtp.login(cfg["username"], cfg["password"])
                return {"success": True, "message": "SMTP 登录成功，配置有效"}
            except Exception as e:
                return {"success": False, "message": f"SMTP 连接/登录失败：{e}"}
            finally:
                if smtp is not None:
                    try:
                        smtp.quit()
                    except Exception:
                        pass

        return await asyncio.to_thread(_try_login)
