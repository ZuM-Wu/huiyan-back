# -*- coding: utf-8 -*-
"""
支付宝开放平台 API 客户端

封装 RSA2 签名、验签、网关请求，支持芝麻信用实名认证三步接口：
1. zhima.customer.certification.material.initialize  初始化认证
2. zhima.customer.certification.certify              获取认证链接
3. zhima.customer.certification.query                查询认证结果

依赖：cryptography（已由 python-jose[cryptography] 间接安装）
"""
import asyncio
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

# 支付宝网关地址
PROD_GATEWAY = "https://openapi.alipay.com/gateway.do"

# 芝麻信用实名认证产品码（支付宝开放平台官方固定值，w + 19 位数字）
# 参见「App 端人脸核身」文档 zhima.customer.certification.initialize 接口说明
PRODUCT_CODE = "w1010100000000000001"


def _format_pem(key_str: str, key_type: str) -> str:
    """将支付宝原始密钥字符串转换为 PEM 格式

    Args:
        key_str: Base64 编码的密钥字符串（不含 PEM 头尾）
        key_type: "public" 或 "private"
    """
    key_str = key_str.strip()
    # 如果已经是 PEM 格式则直接返回
    if "BEGIN" in key_str:
        return key_str
    # 每 64 字符换行
    lines = [key_str[i:i + 64] for i in range(0, len(key_str), 64)]
    body = "\n".join(lines)
    if key_type == "public":
        return f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----"
    # 私钥优先尝试 PKCS#8 格式（更通用）
    return f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----"


def _load_private_key(key_str: str):
    """加载商户私钥（兼容 PKCS#1 和 PKCS#8）"""
    pem = _format_pem(key_str, "private")
    try:
        return serialization.load_pem_private_key(
            pem.encode(), password=None, backend=default_backend()
        )
    except Exception:
        # 回退到 PKCS#1 格式
        pem = pem.replace("BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY")
        pem = pem.replace("END PRIVATE KEY", "END RSA PRIVATE KEY")
        return serialization.load_pem_private_key(
            pem.encode(), password=None, backend=default_backend()
        )


def _load_public_key(key_str: str):
    """加载支付宝公钥"""
    pem = _format_pem(key_str, "public")
    return serialization.load_pem_public_key(
        pem.encode(), backend=default_backend()
    )


def _rsa2_sign(data: str, private_key) -> str:
    """RSA2 (SHA256withRSA) 签名，返回 Base64 字符串"""
    signature = private_key.sign(
        data.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


def _extract_biz_raw(response_body: str, resp_key: str) -> str:
    """从原始响应报文中截取业务数据的 JSON 原文子串

    支付宝的验签范围是 alipay_xxx_response 的 JSON 原文，
    不能用 json.dumps 重新序列化（键序/空白差异会导致验签必然失败）。
    采用单遍扫描的花括号配平算法（O(n)），通过维护
    「花括号深度 / 字符串态 / 转义态」三个状态变量，
    正确处理字符串值内含 {} 与转义引号的边界情况。

    Args:
        response_body: 支付宝网关返回的原始 JSON 字符串
        resp_key: 业务数据键名，如 alipay_xxx_response
    Returns:
        业务数据 JSON 原文子串；定位失败时返回空字符串
    """
    key_pos = response_body.find(f'"{resp_key}"')
    if key_pos < 0:
        return ""
    start = response_body.find("{", key_pos)
    if start < 0:
        return ""
    depth = 0        # 花括号嵌套深度
    in_str = False   # 当前是否处于字符串字面量内
    escape = False   # 前一个字符是否为转义符
    for i in range(start, len(response_body)):
        ch = response_body[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return response_body[start:i + 1]
    return ""


def _rsa2_verify(data: str, sign_b64: str, public_key) -> bool:
    """RSA2 验签"""
    try:
        public_key.verify(
            base64.b64decode(sign_b64),
            data.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


class AlipayClient:
    """支付宝开放平台客户端"""

    def __init__(
        self,
        app_id: str,
        merchant_private_key: str,
        alipay_public_key: str,
        gateway_url: str = PROD_GATEWAY,
    ):
        self.app_id = app_id
        self.gateway_url = gateway_url or PROD_GATEWAY
        self._private_key = _load_private_key(merchant_private_key)
        self._public_key = _load_public_key(alipay_public_key)

    def _build_common_params(self, method: str) -> dict:
        """构建支付宝公共请求参数"""
        return {
            "app_id": self.app_id,
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
        }

    def _sign_params(self, params: dict) -> dict:
        """对请求参数进行 RSA2 签名"""
        # 过滤空值和 sign 字段
        filtered = {
            k: v for k, v in params.items()
            if v is not None and v != "" and k != "sign"
        }
        # 按键名排序拼接
        sorted_items = sorted(filtered.items())
        query_string = "&".join(f"{k}={v}" for k, v in sorted_items)
        # 签名
        sign = _rsa2_sign(query_string, self._private_key)
        filtered["sign"] = sign
        return filtered

    def _verify_response(self, method: str, response_body: str) -> dict:
        """验证支付宝响应签名并返回业务数据

        安全策略（fail-secure）：验签失败、签名缺失或响应结构异常时
        一律抛出 RuntimeError，禁止把不可信数据交给上层业务使用。
        """
        resp_data = json.loads(response_body)
        # 响应 key 格式: alipay_{method}_response
        resp_key = f"alipay_{method.replace('.', '_')}_response"
        if resp_key not in resp_data:
            # 网关级错误（如 AppID 无效、参数缺失），无业务响应体
            err = resp_data.get("error_response", {})
            raise RuntimeError(
                "支付宝网关错误: "
                f"code={err.get('code', '')}, msg={err.get('msg', '')}, "
                f"sub_msg={err.get('sub_msg', '')}"
            )
        sign = resp_data.get("sign", "")
        if not sign:
            # 正常业务响应（含业务错误码响应）必带签名，缺失即视为不可信
            raise RuntimeError("支付宝响应缺少签名，已拒绝处理")
        # 验签范围是业务数据在原始报文中的 JSON 原文（不可重新序列化）
        biz_str = _extract_biz_raw(response_body, resp_key)
        if not biz_str or not _rsa2_verify(biz_str, sign, self._public_key):
            logger.error(f"[支付宝] 响应验签失败, method={method}")
            raise RuntimeError("支付宝响应验签失败，请核对支付宝公钥配置")
        return resp_data.get(resp_key, {})

    async def request(self, method: str, biz_content: dict) -> dict:
        """发送支付宝 API 请求

        Args:
            method: 接口方法名，如 zhima.customer.certification.query
            biz_content: 业务参数字典
        Returns:
            业务响应数据字典
        """
        params = self._build_common_params(method)
        params["biz_content"] = json.dumps(biz_content, ensure_ascii=False)
        signed_params = self._sign_params(params)

        # 构建 POST 数据
        post_data = urllib.parse.urlencode(signed_params).encode("utf-8")

        # 在线程池中执行同步 HTTP 请求
        # 网络层异常统一转为带中文上下文的 RuntimeError，
        # 供上层降级分支（转人工审核）留痕与提示
        def _do_request():
            req = urllib.request.Request(
                self.gateway_url,
                data=post_data,
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"支付宝网关请求失败: HTTP {e.code} {e.reason}") from e
            except urllib.error.URLError as e:
                raise RuntimeError(f"支付宝网关请求失败: 无法连接网关 ({e.reason})") from e
            except TimeoutError as e:
                raise RuntimeError("支付宝网关请求失败: 请求超时(30秒)") from e
            except OSError as e:
                raise RuntimeError(f"支付宝网关请求失败: {e}") from e

        body = await asyncio.to_thread(_do_request)
        result = self._verify_response(method, body)

        # 检查业务错误
        code = result.get("code", "")
        if code != "10000":
            sub_msg = result.get("sub_msg", "")
            msg = result.get("msg", "")
            error = f"支付宝接口错误: code={code}, msg={msg}, sub_msg={sub_msg}"
            logger.error(f"[支付宝] {error}")
            raise RuntimeError(error)

        return result

    async def certify_initialize(
        self, transaction_id: str, cert_name: str, cert_no: str,
        cert_method: str = "FACE",
    ) -> str:
        """初始化实名认证

        Args:
            transaction_id: 商户请求唯一标识（幂等控制）
            cert_name: 真实姓名
            cert_no: 身份证号
            cert_method: 认证方式 FACE|SMART_FACE|MANUAL
        Returns:
            biz_no 芝麻认证业务号
        """
        biz_content = {
            "transaction_id": transaction_id,
            "product_code": PRODUCT_CODE,
            "biz_code": cert_method,
            "identity_param": {
                "identity_type": "CERT_INFO",
                "cert_type": "IDENTITY_CARD",
                "cert_name": cert_name,
                "cert_no": cert_no,
            },
        }
        result = await self.request(
            "zhima.customer.certification.material.initialize", biz_content
        )
        return result.get("biz_no", "")

    async def certify_get_url(self, biz_no: str) -> str:
        """获取认证链接

        Args:
            biz_no: 芝麻认证业务号
        Returns:
            认证 URL
        """
        biz_content = {"biz_no": biz_no}
        result = await self.request(
            "zhima.customer.certification.certify", biz_content
        )
        return result.get("certify_url", "")

    async def certify_query(self, biz_no: str) -> dict:
        """查询认证结果

        Args:
            biz_no: 芝麻认证业务号
        Returns:
            {"passed": True/False, "failed_reason": "..."}
        """
        biz_content = {"biz_no": biz_no}
        result = await self.request(
            "zhima.customer.certification.query", biz_content
        )
        passed = result.get("passed", "F") == "T"
        return {
            "passed": passed,
            "failed_reason": result.get("failed_reason", ""),
        }
