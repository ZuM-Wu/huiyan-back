"""系统 MCP 的线协议压缩；不改变业务权限和真实参数校验。"""

import json
from copy import deepcopy
from typing import Any

from fastmcp.tools import ToolResult
from mcp.types import CallToolResult, TextContent

TOOL_DESCRIPTION_MAX_CHARS = 120
TOOL_PARAMETER_MAX_COUNT = 16
TOOL_RESULT_MAX_BYTES = 1024
TOOL_LIST_MAX_ESTIMATED_TOKENS = 8000
TOOL_LIST_MAX_COUNT = 64


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def compact_tool_schema(schema: Any) -> dict:
    """只删除文档性重复字段，保留可选值、联合类型、嵌套结构和校验约束。"""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    def clean(value):
        if isinstance(value, list):
            return [clean(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key in {"title", "description", "examples", "$comment"}:
                continue
            # properties 中的用户参数名可以是 title/description，不能误删。
            result[key] = {name: clean(child) for name, child in item.items()} if key in {"properties", "$defs"} else clean(item)
        return result

    return clean(schema)


def compact_tool_declaration(tool: Any) -> Any:
    """复制 FastMCP Tool 后压缩发现信息，原函数校验器不受影响。"""
    if isinstance(tool, dict):
        return {**tool, "description": str(tool.get("description") or "")[:TOOL_DESCRIPTION_MAX_CHARS],
                "parameters": compact_tool_schema(tool.get("parameters"))}
    schema_key = "parameters" if hasattr(tool, "parameters") else "inputSchema"
    updates = {
        "description": str(getattr(tool, "description", "") or "")[:TOOL_DESCRIPTION_MAX_CHARS],
        schema_key: compact_tool_schema(getattr(tool, schema_key, {})),
    }
    return tool.model_copy(update=updates)


def compact_tool_list(tools: list[Any], *, core_names: set[str] | None = None) -> list[Any]:
    """按核心、插件、外部排序；按实际 MCP 声明的 UTF-8 字节估算预算。"""
    core_names = core_names or set()

    def priority(tool):
        name = tool["name"] if isinstance(tool, dict) else tool.name
        if name in core_names:
            return 0
        return 2 if name.startswith(("ext__", "fext__")) else 1

    result: list[Any] = []
    wire: list[dict] = []
    for item in sorted(tools, key=priority):
        item = compact_tool_declaration(item)
        declaration = item.to_mcp_tool() if hasattr(item, "to_mcp_tool") else item
        payload = declaration if isinstance(declaration, dict) else declaration.model_dump(by_alias=True, exclude_none=True)
        if len(result) >= TOOL_LIST_MAX_COUNT:
            break
        if (len(_json({"tools": [*wire, payload]}).encode("utf-8")) + 3) // 4 > TOOL_LIST_MAX_ESTIMATED_TOKENS:
            continue
        result.append(item)
        wire.append(payload)
    return result


def _priority(key: str, value: Any) -> int:
    if key in {"status", "error", "code", "reason", "message", "requires_confirmation", "next_action", "found"}:
        return 0
    if key == "id" or key.endswith(("_id", "_name", "_at", "_time")) or key in {"name", "time"}:
        return 1
    if isinstance(value, (int, float, bool)) or value is None:
        return 2
    return 3


def _compact_value(value: Any, string_limit: int, list_limit: int) -> tuple[Any, int]:
    """递归收敛字段与列表，统计被省略的值或列表项。"""
    if isinstance(value, str):
        return value[:string_limit], int(len(value) > string_limit)
    if isinstance(value, list):
        pairs = [_compact_value(item, string_limit, list_limit) for item in value[:list_limit]]
        return [item for item, _ in pairs], len(value) - len(pairs) + sum(count for _, count in pairs)
    if isinstance(value, dict):
        fields = {key: _compact_value(item, string_limit, list_limit) for key, item in value.items()}
        return {key: item for key, (item, _) in fields.items()}, sum(count for _, count in fields.values())
    return value, 0


def _result(payload: Any, is_error: bool) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=_json(payload))], is_error=is_error)


def _wire_size(payload: Any, is_error: bool) -> int:
    # 包括 MCP content 和 isError 的开销，避免正文达标而协议包仍超限。
    return len(_json({"content": [{"type": "text", "text": _json(payload)}], "isError": is_error}).encode("utf-8"))


def _trim_detail(value: dict) -> bool:
    """逐项移除最低优先级字段，保留嵌套设备和模型 ID，避免整组候选被清空。"""
    choices: list[tuple[int, int, Any, Any]] = []

    def collect(node):
        if isinstance(node, list):
            if len(node) > 1:
                choices.append((4, len(_json(node[-1])), node, len(node) - 1))
            for child in node:
                collect(child)
        elif isinstance(node, dict):
            for key, child in node.items():
                if node is value and key in {"truncated", "next_action", "omitted_count"}:
                    continue
                if isinstance(child, (dict, list)) and child:
                    collect(child)
                else:
                    choices.append((_priority(key, child), len(_json(child)), node, key))

    collect(value)
    if not choices:
        return False
    _, _, parent, key = max(choices, key=lambda choice: choice[:2])
    parent.pop(key)
    value["omitted_count"] += 1
    return True


def compact_tool_result(value: Any, max_bytes: int = TOOL_RESULT_MAX_BYTES) -> Any:
    """压缩真实 ToolResult，始终保留 MCP 返回类型与 isError 语义。"""
    if isinstance(value, CallToolResult):
        result = compact_tool_result(ToolResult(content=value.content,
            structured_content=value.structuredContent, is_error=value.isError), max_bytes)
        return result.to_mcp_result()
    is_tool_result = isinstance(value, ToolResult)
    is_error = value.is_error if is_tool_result else False
    payload = value
    if is_tool_result:
        texts = [item.text for item in value.content if isinstance(item, TextContent)]
        payload = value.structured_content
        if payload is None:
            payload = {"message": "请在后台查看媒体详情"}
            if texts:
                try:
                    payload = json.loads(texts[0]) if len(texts) == 1 else {"messages": texts}
                except ValueError:
                    payload = {"message": texts[0]}
    if _wire_size(payload, is_error) <= max_bytes:
        return _result(payload, is_error) if is_tool_result else payload
    source = deepcopy(payload) if isinstance(payload, dict) else {"data": deepcopy(payload)}
    compact: dict[str, Any] = {}
    for list_limit in (3, 2, 1):
        compact, omitted = _compact_value(source, 80, list_limit)
        compact.update({"truncated": True, "omitted_count": omitted + int(source.get("omitted_count") or 0),
                        "next_action": str(source.get("next_action") or "请按具体ID或日期缩小查询范围")[:80]})
        if _wire_size(compact, is_error) <= max_bytes:
            break
    # 极小的工具自定义预算先缩短引导，保留状态和核心标识的空间。
    if max_bytes < 512:
        compact["next_action"] = "请缩小查询范围"
    while _wire_size(compact, is_error) > max_bytes and _trim_detail(compact):
        pass
    return _result(compact, is_error) if is_tool_result else compact
