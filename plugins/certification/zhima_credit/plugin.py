# -*- coding: utf-8 -*-
"""
芝麻信用实名认证插件

基于支付宝芝麻信用官方 API 实现实名认证，支持三种认证方式：
- FACE       人脸识别
- SMART_FACE 人脸+身份证
- MANUAL     快捷认证（无需识别）

认证流程：
1. certify_initialize  提交姓名+身份证号，获取 biz_no
2. certify_get_url     获取认证链接，用户在端内完成认证
3. certify_query       查询认证结果（passed=T/F）

插件发现后，在"实名认证 > 接口管理"页面直接配置参数，
填写 AppID / 支付宝公钥 / 商户私钥即可启用。
"""
import asyncio
import logging
import uuid

from core.config_manager import ConfigManager
from core.plugin_manager import BasePlugin

from plugins.certification.zhima_credit.alipay_client import AlipayClient

logger = logging.getLogger(__name__)

PLUGIN_NAME = "zhima_credit"

# 认证方式映射
CERT_METHODS = {
    "FACE": "人脸识别",
    "SMART_FACE": "人脸+身份证",
    "MANUAL": "快捷认证(无需识别)",
}


class Plugin(BasePlugin):
    """芝麻信用实名认证插件"""

    def __init__(self, db_session=None, config: dict = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "芝麻信用实名认证"
        self.version = "1.0.1"
        self.description = "基于支付宝芝麻信用官方API的实名认证插件"
        self.module = "certification"
        self._config_manager = ConfigManager()

    # ------------------------------------------------------------------
    # 安装 / 卸载
    # ------------------------------------------------------------------
    async def install(self) -> bool:
        """安装插件：写入默认配置"""
        if not self.db:
            return False
        for key, value in (self.config or {}).items():
            existing = await self._config_manager.get(key, self.db)
            if existing is None:
                await self._config_manager.set(
                    key, str(value), self.db,
                    description="芝麻信用实名认证配置",
                )
        logger.info("[zhima_credit] 插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """卸载插件；配置、导航和权限由 PluginManager 统一清理。"""
        if not self.db:
            return False
        logger.info("[zhima_credit] 插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def get_pages(self):
        """无独立管理页面（通过实名认证页面的接口管理 Tab 配置）"""
        return []

    def get_config_schema(self) -> list:
        """声明插件配置项表单定义

        前端"接口管理"Tab 编辑渠道时，根据此 schema 动态渲染表单。
        """
        return [
            {
                "key": "app_id",
                "label": "AppID",
                "type": "input",
                "placeholder": "支付宝开放平台应用ID",
                "required": True,
            },
            {
                "key": "alipay_public_key",
                "label": "支付宝公钥",
                "type": "textarea",
                "placeholder": "支付宝公钥（Base64编码字符串）",
                "required": True,
            },
            {
                "key": "merchant_private_key",
                "label": "商户私钥",
                "type": "textarea",
                "placeholder": "商户应用私钥（Base64编码字符串）",
                "required": True,
            },
            {
                "key": "cert_method",
                "label": "认证方式",
                "type": "select",
                "default": "FACE",
                "options": [
                    {"value": "FACE", "label": "人脸识别"},
                    {"value": "SMART_FACE", "label": "人脸+身份证"},
                    {"value": "MANUAL", "label": "快捷认证(无需识别)"},
                ],
            },
        ]

    # ------------------------------------------------------------------
    # 认证接口（供实名认证 API 层调用）
    # ------------------------------------------------------------------
    def _create_client(self, channel_config: dict) -> AlipayClient:
        """从渠道配置创建支付宝客户端

        Args:
            channel_config: 渠道配置字典（来自 hy_certification_channel.config JSON）
        """
        cfg = channel_config or {}
        return AlipayClient(
            app_id=cfg.get("app_id", ""),
            merchant_private_key=cfg.get("merchant_private_key", ""),
            alipay_public_key=cfg.get("alipay_public_key", ""),
            gateway_url=cfg.get(
                "gateway_url", "https://openapi.alipay.com/gateway.do"
            ),
        )

    async def certify_initialize(
        self, real_name: str, id_card: str,
        channel_config: dict = None, phone: str = "",
    ) -> dict:
        """初始化实名认证

        Args:
            real_name: 真实姓名
            id_card: 身份证号
            channel_config: 渠道配置
            phone: 手机号（备用）
        Returns:
            {"biz_no": "芝麻认证业务号", "certify_url": "认证链接"}
        """
        client = self._create_client(channel_config)
        cert_method = (channel_config or {}).get("cert_method", "FACE")
        # 生成唯一交易流水号
        transaction_id = f"hy_{uuid.uuid4().hex[:24]}"
        biz_no = await client.certify_initialize(
            transaction_id, real_name, id_card, cert_method
        )
        # 第二步获取认证链接：失败后短暂退避重试一次，
        # 仍失败则抛异常交由 API 层统一降级人工审核
        #（避免返回空 URL 的半成品状态）
        try:
            certify_url = await client.certify_get_url(biz_no)
        except Exception as first_err:
            logger.warning(
                f"[zhima_credit] 获取认证链接失败，即将重试: "
                f"biz_no={biz_no}, err={first_err}"
            )
            await asyncio.sleep(0.5)
            try:
                certify_url = await client.certify_get_url(biz_no)
            except Exception as retry_err:
                raise RuntimeError(
                    f"获取认证链接失败(biz_no={biz_no}): {retry_err}"
                ) from retry_err
        return {"biz_no": biz_no, "certify_url": certify_url}

    async def certify_query(
        self, biz_no: str, channel_config: dict = None
    ) -> dict:
        """查询认证结果

        Args:
            biz_no: 芝麻认证业务号
            channel_config: 渠道配置
        Returns:
            {"passed": True/False, "failed_reason": "..."}
        """
        client = self._create_client(channel_config)
        return await client.certify_query(biz_no)

    async def test_connection(self, channel_config: dict = None) -> dict:
        """测试渠道连接（密钥加载 + 真实网关连通性验证）

        先加载密钥验证格式，再用假 biz_no 调用查询接口做轻量联网测试：
        - 收到支付宝业务错误响应（code!=10000 且已通过验签）
          说明网关连通、密钥配置有效，属预期结果
        - 网关错误/验签失败/网络异常则判定为配置或网络问题

        Args:
            channel_config: 渠道配置
        Returns:
            {"success": True/False, "message": "..."}
        """
        try:
            client = self._create_client(channel_config)
        except Exception as e:
            return {"success": False, "message": f"密钥加载失败: {e!s}"}
        try:
            # 假 biz_no 不会命中真实认证单，仅用于验证连通性
            await client.certify_query("hy_test_connection_000")
            return {"success": True, "message": "支付宝网关连通，配置有效"}
        except RuntimeError as e:
            msg = str(e)
            if msg.startswith("支付宝接口错误"):
                # 业务错误码响应已通过验签，说明网关连通且密钥正确
                return {"success": True, "message": "支付宝网关连通，配置有效"}
            return {"success": False, "message": f"连接测试失败: {msg}"}
        except Exception as e:
            return {"success": False, "message": f"连接测试失败: {e!s}"}
