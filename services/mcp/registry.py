"""
MCP 工具注册表

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
"""
import logging
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)

# owner（core 或插件名）-> 已注册的完整工具名列表
_owner_tools: dict[str, list[str]] = {}
# 完整工具名 -> {"owner", "audience", "permission_code"}
_tool_meta: dict[str, dict] = {}


def get_tool_meta(tool_name: str) -> Optional[dict]:
    """查询工具元信息（权限中间件使用）；非本注册表管理的工具返回 None"""
    return _tool_meta.get(tool_name)


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
            logger.info("[MCP] 工具已注册: %s (audience=%s)", full_name, meta["audience"])
        except Exception:
            logger.exception("[MCP] 工具注册失败: owner=%s decl=%s", owner, decl.get("name"))


def unregister_tools(owner: str):
    """注销某归属方的全部工具（插件禁用/卸载即时生效；不存在则静默）"""
    if not settings.MCP_ENABLED:
        return

    from services.mcp.server import mcp

    for full_name in _owner_tools.pop(owner, []):
        _tool_meta.pop(full_name, None)
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
