"""
MCP 能力注册表

工具声明 dict 协议（核心工具与插件 get_mcp_tools 返回值统一使用）:
{
    "name":            str,   工具短名（注册后实际名为 f"{owner}_{name}"）
    "description":     str,   工具描述（AI 消费，建议写清参数含义与返回结构）
    "handler":         async callable，工具实现（内部自建 DB 会话，
                       用 fastmcp.server.dependencies.get_access_token 读身份）
    "audience":        "admin" | "farmer" | "both"，可见的用户体系
    "permission_code": str | None，管理员侧所需 RBAC 权限码（None 表示不额外要求）
}

设计要点:
- MCP_ENABLED=False 时所有函数空操作返回，插件体系无感知
- 懒 import server 模块，开关关闭时不创建 FastMCP 实例
- owner -> [tool_name] 映射支撑插件禁用/卸载时的即时注销
- _tool_meta 自维护元信息映射，供权限中间件查询（不依赖 fastmcp 内部 meta 读取 API）
- 系统 Resources/Prompts 静态注册；插件生命周期仍只管理 Tools
"""
import logging
from typing import Any, Optional

from core.config import settings

logger = logging.getLogger(__name__)

# owner（core 或插件名）-> 已注册的完整工具名列表
_owner_tools: dict[str, list[str]] = {}
# 完整工具名 -> {"owner", "audience", "permission_code"}
_tool_meta: dict[str, dict] = {}
# 完整工具名 -> 已验证的声明。AgentScope 等运行时只通过下面的公开查询门面读取，
# 不直接依赖 FastMCP 内部 Tool 对象或插件实例。
_tool_declarations: dict[str, dict] = {}
# Resource URI 或 URI Template -> 权限元信息。
_resource_meta: dict[str, dict] = {}
# URI Template -> FastMCP ResourceTemplate，用于实际 URI 的安全匹配。
_resource_templates: dict[str, Any] = {}
# 完整 Prompt 名 -> 权限元信息。
_prompt_meta: dict[str, dict] = {}


def get_tool_meta(tool_name: str) -> Optional[dict]:
    """查询工具元信息（权限中间件使用）；非本注册表管理的工具返回 None"""
    return _tool_meta.get(tool_name)


def get_resource_meta(uri: str) -> Optional[dict]:
    """按固定 URI 或 URI Template 查询资源权限元信息。"""
    normalized = str(uri)
    direct = _resource_meta.get(normalized)
    if direct is not None:
        return direct
    for template_uri, template in _resource_templates.items():
        if template.matches(normalized) is not None:
            return _resource_meta.get(template_uri)
    return None


def get_prompt_meta(prompt_name: str) -> Optional[dict]:
    """查询提示模板权限元信息；未登记模板默认拒绝。"""
    return _prompt_meta.get(prompt_name)


def list_registered_tool_declarations() -> list[dict]:
    """返回当前已注册 MCP 工具的声明快照。

    返回副本，调用方不得通过结果修改注册表。声明中的 handler 仍由插件
    或核心模块拥有，执行前会由各自的身份上下文重新校验权限。
    """
    return [dict(declaration) for declaration in _tool_declarations.values()]


def register_tools(owner: str, tools: list[dict]):
    """
    注册一组工具到 FastMCP 实例（幂等: 重名工具跳过并告警）

    :param owner: 归属方标识，"core" 或插件名（用于命名前缀与批量注销）
    :param tools: 工具声明 dict 列表（协议见模块 docstring）
    """
    if not settings.MCP_ENABLED or not tools:
        return

    from fastmcp.tools import Tool
    from services.mcp.server import mcp

    registered = _owner_tools.setdefault(owner, [])
    for decl in tools:
        try:
            full_name = f"{owner}_{decl['name']}"
            if full_name in _tool_meta:
                logger.warning("[MCP] 工具重名跳过注册: %s", full_name)
                continue
            meta = {
                "owner": owner,
                "audience": decl.get("audience", "admin"),
                "permission_code": decl.get("permission_code"),
            }
            tool = Tool.from_function(
                decl["handler"],
                name=full_name,
                description=decl.get("description", ""),
                meta=meta,
            )
            mcp.add_tool(tool)
            registered.append(full_name)
            _tool_meta[full_name] = meta
            _tool_declarations[full_name] = {
                **decl,
                "name": full_name,
                "owner": owner,
            }
            logger.info("[MCP] 工具已注册: %s (audience=%s)", full_name, meta["audience"])
        except Exception:
            logger.exception("[MCP] 工具注册失败: owner=%s decl=%s", owner, decl.get("name"))


def _capability_meta(owner: str, declaration: dict) -> dict:
    """生成三类能力共用的最小权限元信息。"""
    return {
        "owner": owner,
        "audience": declaration.get("audience", "admin"),
        "permission_code": declaration.get("permission_code"),
    }


def register_resources(owner: str, resources: list[dict]) -> None:
    """静态注册系统 Resources；固定 URI 与模板 URI 均要求全局唯一。"""
    if not settings.MCP_ENABLED or not resources:
        return

    from fastmcp.resources import Resource, ResourceTemplate
    from services.mcp.server import mcp

    for declaration in resources:
        uri = str(declaration.get("uri") or "")
        try:
            if not uri or uri in _resource_meta:
                if uri:
                    logger.warning("[MCP] Resource URI 重名跳过注册: %s", uri)
                continue
            meta = _capability_meta(owner, declaration)
            kwargs = {
                "fn": declaration["handler"],
                "name": f"{owner}_{declaration['name']}",
                "description": declaration.get("description", ""),
                "mime_type": declaration.get("mime_type", "application/json"),
                "meta": meta,
            }
            component: Any
            if "{" in uri and "}" in uri:
                component = ResourceTemplate.from_function(
                    uri_template=uri,
                    **kwargs,
                )
            else:
                component = Resource.from_function(uri=uri, **kwargs)
            mcp.add_resource(component)
            _resource_meta[uri] = meta
            if isinstance(component, ResourceTemplate):
                _resource_templates[uri] = component
            logger.info("[MCP] Resource 已注册: %s", uri)
        except Exception:
            logger.exception("[MCP] Resource 注册失败: owner=%s uri=%s", owner, uri)


def register_prompts(owner: str, prompts: list[dict]) -> None:
    """静态注册系统 Prompts，并使用 owner 前缀隔离名称。"""
    if not settings.MCP_ENABLED or not prompts:
        return

    from fastmcp.prompts import Prompt
    from services.mcp.server import mcp

    for declaration in prompts:
        full_name = f"{owner}_{declaration.get('name', '')}"
        try:
            if full_name == f"{owner}_" or full_name in _prompt_meta:
                if full_name != f"{owner}_":
                    logger.warning("[MCP] Prompt 重名跳过注册: %s", full_name)
                continue
            meta = _capability_meta(owner, declaration)
            prompt = Prompt.from_function(
                declaration["handler"],
                name=full_name,
                description=declaration.get("description", ""),
                meta=meta,
            )
            mcp.add_prompt(prompt)
            _prompt_meta[full_name] = meta
            logger.info("[MCP] Prompt 已注册: %s", full_name)
        except Exception:
            logger.exception("[MCP] Prompt 注册失败: owner=%s name=%s", owner, full_name)


def unregister_tools(owner: str):
    """注销某归属方的全部工具（插件禁用/卸载即时生效；不存在则静默）"""
    if not settings.MCP_ENABLED:
        return

    from services.mcp.server import mcp

    for full_name in _owner_tools.pop(owner, []):
        _tool_meta.pop(full_name, None)
        _tool_declarations.pop(full_name, None)
        try:
            mcp.remove_tool(full_name)
            logger.info("[MCP] 工具已注销: %s", full_name)
        except Exception:
            # 工具可能已被移除，容错不中断批量注销
            logger.warning("[MCP] 工具注销失败(忽略): %s", full_name)


def register_core_tools():
    """注册系统核心工具（main.py 启动接线时调用；先注销保证幂等）"""
    if not settings.MCP_ENABLED:
        return

    from services.mcp.tools_core import CORE_TOOLS

    unregister_tools("core")
    register_tools("core", CORE_TOOLS)


def register_core_capabilities() -> None:
    """一次性注册系统 Tools、Resources 与 Prompts。"""
    if not settings.MCP_ENABLED:
        return

    from services.mcp.prompts_agriculture import CORE_PROMPTS
    from services.mcp.resources_agriculture import CORE_RESOURCES

    register_core_tools()
    register_resources("core", CORE_RESOURCES)
    register_prompts("core", CORE_PROMPTS)
