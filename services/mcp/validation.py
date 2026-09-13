"""MCP 声明校验；权限目录来自核心种子和当前插件权限树。"""

import inspect
import re

from core.seed_crud import core_permission_codes
from services.mcp.compact import TOOL_PARAMETER_MAX_COUNT, TOOL_RESULT_MAX_BYTES


def permission_codes(tree: list[dict] | None) -> set[str]:
    """展开插件原有权限树，不创建权限或向角色追加授权。"""
    codes: set[str] = set()
    pending = list(tree or [])
    while pending:
        node = pending.pop()
        if node.get("code"):
            codes.add(str(node["code"]))
        pending.extend(node.get("children") or [])
    return codes


def validate_tool(owner: str, declaration: dict, plugin_permissions: list[dict] | None) -> None:
    """错误声明在加入 FastMCP 和权限元数据之前拒绝。"""
    name = declaration.get("name")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", owner):
        raise ValueError("工具归属方须为小写下划线名称")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name) or len(f"{owner}_{name}") > 64:
        raise ValueError("工具名称须为小写下划线名称且完整名称不超过64字符")
    handler = declaration.get("handler")
    if not callable(handler):
        raise ValueError("handler 必须可调用")
    signature = inspect.signature(handler)
    if len(signature.parameters) > TOOL_PARAMETER_MAX_COUNT:
        raise ValueError("工具参数过多，请拆分领域操作")
    if any(item.kind in {item.VAR_KEYWORD, item.VAR_POSITIONAL} for item in signature.parameters.values()):
        raise ValueError("工具不允许不定参数")
    if not isinstance(declaration.get("description"), str) or not declaration["description"].strip():
        raise ValueError("工具必须提供简短中文说明")
    if len(declaration["description"]) > 512:
        raise ValueError("工具描述超过512字符，请精简")
    if declaration.get("audience", "admin") not in {"admin", "farmer", "both"}:
        raise ValueError("工具受众无效")
    allowed = core_permission_codes() | permission_codes(plugin_permissions)
    required = declaration.get("required_permissions", [])
    if not isinstance(required, list):
        raise ValueError("required_permissions 必须为权限码列表")
    codes = [declaration.get("permission_code"), *required]
    for code in codes:
        if code is not None and (not isinstance(code, str) or code not in allowed):
            raise ValueError(f"权限码未在核心或插件权限树登记：{code}")
    budget = declaration.get("response_max_bytes", TOOL_RESULT_MAX_BYTES)
    if type(budget) is not int or not 256 <= budget <= TOOL_RESULT_MAX_BYTES:
        raise ValueError("响应字节预算必须在256至1024之间")
    if declaration.get("requires_confirmation") and "confirmed" not in signature.parameters:
        raise ValueError("需要确认的工具必须声明confirmed参数")
