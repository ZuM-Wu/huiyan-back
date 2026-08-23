"""
MCP 工具权限过滤中间件

过滤规则（依据工具 meta 中的 audience / permission_code，见 registry.py 协议）:
- farmer Key: 仅可见/可调 audience in (farmer, both) 的工具
- admin  Key: 仅可见/可调 audience in (admin, both) 的工具，且满足其一:
    is_super（超管 id=1）/ permission_code 为空 / permission_code in scopes
- 双保险: tools/list 只做展示过滤，tools/call 强制校验（越权直接拒绝，不依赖列表隐藏）
"""
import logging
from typing import Optional

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger(__name__)


def _tool_allowed(meta: Optional[dict], token) -> bool:
    """
    判断当前 access token 是否允许使用某工具

    :param meta: registry 登记的工具元信息 {"audience", "permission_code", "owner"}；
                 None 表示未登记工具，默认拒绝
    :param token: fastmcp AccessToken（可能为 None，理论上鉴权层已拦截）
    """
    if meta is None:
        # 所有可调用工具必须由 registry 登记，未知工具默认拒绝。
        return False
    if token is None:
        return False

    audience = meta.get("audience", "admin")
    claims = token.claims or {}
    user_type = claims.get("user_type", "")

    if user_type == "farmer":
        return audience in ("farmer", "both")

    if user_type == "admin":
        if audience not in ("admin", "both"):
            return False
        # 超管放行 / 无权限码要求放行 / 命中 RBAC 权限码放行
        if claims.get("is_super"):
            return True
        code = meta.get("permission_code")
        return not code or code in (token.scopes or [])

    return False


class PermissionFilterMiddleware(Middleware):
    """按 Key 身份过滤工具列表、强制校验工具调用的 FastMCP 中间件"""

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        """tools/list: 按权限过滤返回列表"""
        from services.mcp.registry import get_tool_meta

        tools = await call_next(context)
        token = get_access_token()
        return [t for t in tools if _tool_allowed(get_tool_meta(t.name), token)]

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """tools/call: 越权直接拒绝（不依赖列表隐藏的兜底校验）"""
        from services.mcp.registry import get_tool_meta

        tool_name = context.message.name
        token = get_access_token()
        if not _tool_allowed(get_tool_meta(tool_name), token):
            client = token.client_id if token else "anonymous"
            logger.warning("[MCP] 越权调用被拒绝: client=%s tool=%s", client, tool_name)
            raise ToolError(f"无权调用该工具: {tool_name}")
        return await call_next(context)
