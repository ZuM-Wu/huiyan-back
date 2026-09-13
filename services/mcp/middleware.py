"""
MCP 能力权限过滤与账户级频控中间件

过滤规则（依据工具 meta 中的 audience / permission_code，见 registry.py 协议）:
- farmer Key: 仅可见/可调 audience in (farmer, both) 的工具
- admin  Key: 仅可见/可调 audience in (admin, both) 的工具，且满足其一:
    is_super（超管 id=1）/ permission_code 为空 / permission_code in scopes
- Tools、Resources、Prompts 均在发现阶段过滤，并在执行阶段再次强制校验
- 三类能力共用个人账户滑动窗口频控，错误不记录参数、正文或密钥
"""
import logging
from time import perf_counter
from typing import Optional

from fastmcp.exceptions import McpError, PromptError, ResourceError, ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.types import ErrorData

from core.config import settings
from core.rate_limiter import check_rate_detail

logger = logging.getLogger(__name__)


def _normalized_admin(claims: dict) -> bool:
    """兼容 API Key claims 中的布尔字符串，统一识别超级管理员。"""
    value = claims.get("is_super", False)
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _permission_granted(code: str | None, scopes: list[str]) -> bool:
    """按精确码或领域通配码判断 RBAC 权限。"""
    if not code:
        return True
    granted = {str(item).strip() for item in scopes if item}
    if code in granted:
        return True
    domain = code.split(":", 1)[0]
    return f"{domain}:*" in granted or "*" in granted


def _capability_decision(meta: Optional[dict], token) -> tuple[bool, str]:
    """返回能力授权结果及稳定的中文诊断原因。"""
    if meta is None:
        return False, "能力未登记"
    if token is None:
        return False, "未提供有效 MCP API Key"
    claims = token.claims or {}
    user_type = str(claims.get("user_type", "")).strip().lower()
    audience = meta.get("audience", "admin")
    if user_type == "farmer":
        return (True, "") if audience in ("farmer", "both") else (False, "该工具仅限管理员")
    if user_type != "admin":
        return False, "API Key 身份不是管理员或农户"
    if audience not in ("admin", "both"):
        return False, "该工具不向管理员开放"
    if _normalized_admin(claims):
        return True, ""
    codes = [meta.get("permission_code"), *(meta.get("required_permissions") or [])]
    missing = next((code for code in codes if not _permission_granted(code, token.scopes or [])), None)
    return (False, f"管理员缺少权限：{missing}") if missing else (True, "")


def _capability_allowed(meta: Optional[dict], token) -> bool:
    """
    判断当前 access token 是否允许使用某工具

    :param meta: registry 登记的工具元信息 {"audience", "permission_code", "owner"}；
                 None 表示未登记工具，默认拒绝
    :param token: fastmcp AccessToken（可能为 None，理论上鉴权层已拦截）
    """
    return _capability_decision(meta, token)[0]


def _tool_allowed(meta: Optional[dict], token) -> bool:
    """保留既有测试和内部调用入口，统一委托三类能力权限判断。"""
    return _capability_allowed(meta, token)


def _check_rate(token, capability_type: str, capability_name: str) -> None:
    """按账户限制 MCP 能力请求，匿名请求继续交由鉴权层处理。"""
    if token is None:
        return
    allowed, retry_after = check_rate_detail(
        f"mcp:{token.client_id}",
        settings.MCP_RATE_LIMIT_REQUESTS,
        settings.MCP_RATE_LIMIT_WINDOW_SECONDS,
    )
    if allowed:
        return
    logger.warning(
        "[MCP] 调用频率超限: client=%s type=%s name=%s retry_after=%s",
        token.client_id,
        capability_type,
        capability_name,
        retry_after,
    )
    raise McpError(ErrorData(
        code=-32001,
        message="MCP 调用频率超限，请稍后重试",
        data={"retry_after_seconds": retry_after},
    ))


async def _invoke(context, call_next, token, capability_type: str, name: str):
    """执行能力调用并记录脱敏结果与耗时。"""
    started = perf_counter()
    try:
        result = await call_next(context)
    except Exception as exc:
        logger.warning(
            "[MCP] 能力调用失败: client=%s type=%s name=%s error=%s duration_ms=%s",
            token.client_id if token else "anonymous",
            capability_type,
            name,
            type(exc).__name__,
            int((perf_counter() - started) * 1000),
        )
        raise
    logger.info(
        "[MCP] 能力调用完成: client=%s type=%s name=%s duration_ms=%s",
        token.client_id if token else "anonymous",
        capability_type,
        name,
        int((perf_counter() - started) * 1000),
    )
    return result


class PermissionFilterMiddleware(Middleware):
    """对三类 MCP 能力统一执行发现过滤、调用校验、频控和脱敏日志。"""

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        """tools/list: 按权限过滤返回列表"""
        from services.mcp.registry import get_tool_meta

        token = get_access_token()
        _check_rate(token, "tool", "list")
        tools = await _invoke(context, call_next, token, "tool", "list")
        from services.mcp.compact import compact_tool_declaration, compact_tool_list
        visible = [compact_tool_declaration(t) for t in tools if _capability_allowed(get_tool_meta(t.name), token)]
        core_names = {t.name for t in visible if (get_tool_meta(t.name) or {}).get("owner") == "core"}
        selected = compact_tool_list(visible, core_names=core_names)
        if len(selected) != len(visible):
            logger.info("[MCP] 工具清单裁剪: client=%s available=%s omitted=%s", token.client_id if token else "anonymous", len(selected), len(visible) - len(selected))
        return selected

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """tools/call: 越权直接拒绝（不依赖列表隐藏的兜底校验）"""
        from services.mcp.registry import get_tool_declaration, get_tool_meta, resolve_tool_name

        tool_name = context.message.name
        canonical_name = resolve_tool_name(tool_name)
        if canonical_name != tool_name:
            # 兼容旧客户端调用名，转发到规范名称对应的 FastMCP 工具。
            context = context.copy(message=context.message.model_copy(update={"name": canonical_name}))
        token = get_access_token()
        _check_rate(token, "tool", tool_name)
        meta = get_tool_meta(tool_name)
        allowed, reason = _capability_decision(meta, token)
        if not allowed:
            client = token.client_id if token else "anonymous"
            logger.warning("[MCP] 越权调用被拒绝: client=%s tool=%s permission=%s reason=%s", client, tool_name, (meta or {}).get("permission_code"), reason)
            from services.mcp.errors import tool_error_result
            return tool_error_result("permission_denied", f"{tool_name}：{reason}")
        from pydantic import ValidationError
        from services.mcp.errors import McpToolError, tool_error_result
        try:
            result = await _invoke(context, call_next, token, "tool", tool_name)
        except McpToolError as exc:
            return tool_error_result(exc.code, str(exc))
        except (ValidationError, TypeError):
            return tool_error_result("invalid_arguments")
        except ToolError as exc:
            from services.mcp.errors import legacy_tool_error_result
            return legacy_tool_error_result(str(exc))
        except Exception:
            return tool_error_result("internal_error")
        from services.mcp.compact import compact_tool_result
        declaration = get_tool_declaration(canonical_name) or {}
        return compact_tool_result(result, declaration.get("response_max_bytes", 1024))

    async def on_list_resources(self, context: MiddlewareContext, call_next):
        """resources/list: 仅返回当前身份可见的固定资源。"""
        from services.mcp.registry import get_resource_meta

        token = get_access_token()
        _check_rate(token, "resource", "list")
        resources = await _invoke(context, call_next, token, "resource", "list")
        return [
            resource for resource in resources
            if _capability_allowed(get_resource_meta(str(resource.uri)), token)
        ]

    async def on_list_resource_templates(self, context: MiddlewareContext, call_next):
        """resources/templates/list: 按模板 URI 元数据过滤。"""
        from services.mcp.registry import get_resource_meta

        token = get_access_token()
        _check_rate(token, "resource_template", "list")
        templates = await _invoke(
            context, call_next, token, "resource_template", "list"
        )
        return [
            template for template in templates
            if _capability_allowed(
                get_resource_meta(str(template.uri_template)), token
            )
        ]

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        """resources/read: 实际 URI 在读取前再次执行权限校验。"""
        from services.mcp.registry import get_resource_meta

        uri = str(context.message.uri)
        token = get_access_token()
        _check_rate(token, "resource", uri)
        if not _capability_allowed(get_resource_meta(uri), token):
            client = token.client_id if token else "anonymous"
            logger.warning("[MCP] 越权读取被拒绝: client=%s uri=%s", client, uri)
            raise ResourceError(f"无权读取该资源: {uri}")
        return await _invoke(context, call_next, token, "resource", uri)

    async def on_list_prompts(self, context: MiddlewareContext, call_next):
        """prompts/list: 仅返回当前身份可见的提示模板。"""
        from services.mcp.registry import get_prompt_meta

        token = get_access_token()
        _check_rate(token, "prompt", "list")
        prompts = await _invoke(context, call_next, token, "prompt", "list")
        return [
            prompt for prompt in prompts
            if _capability_allowed(get_prompt_meta(prompt.name), token)
        ]

    async def on_get_prompt(self, context: MiddlewareContext, call_next):
        """prompts/get: 获取模板前再次执行权限校验。"""
        from services.mcp.registry import get_prompt_meta

        prompt_name = context.message.name
        token = get_access_token()
        _check_rate(token, "prompt", prompt_name)
        if not _capability_allowed(get_prompt_meta(prompt_name), token):
            client = token.client_id if token else "anonymous"
            logger.warning(
                "[MCP] 越权获取 Prompt 被拒绝: client=%s prompt=%s",
                client,
                prompt_name,
            )
            raise PromptError(f"无权获取该提示模板: {prompt_name}")
        return await _invoke(context, call_next, token, "prompt", prompt_name)
