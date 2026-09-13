"""MCP 工具的稳定业务错误码，不向客户端传播内部异常。"""

from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult

from services.mcp.compact import compact_tool_result

ERROR_MESSAGES = {
    "invalid_arguments": "请求参数错误",
    "permission_denied": "无权限",
    "plot_not_found": "地块不存在或已停用",
    "area_not_found": "产区不存在或已停用",
    "device_not_found": "设备不存在",
    "no_devices": "没有绑定设备",
    "no_recognition_devices": "没有可识别设备",
    "no_valid_model": "没有有效模型",
    "binding_changed": "设备或模型绑定已变化，请重新预览",
    "device_unavailable": "设备暂不可用",
    "no_data": "暂无可用数据",
    "queue_disabled": "识别任务队列已关闭",
    "internal_error": "工具暂不可用，请稍后重试",
    "resource_not_found": "资源不存在",
    "operation_failed": "操作失败，请稍后重试",
    "rate_limited": "请求过于频繁，请稍后重试",
}


class McpToolError(ToolError):
    """只允许服务端定义的代码和脱敏中文说明。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or ERROR_MESSAGES.get(code, "操作失败"))


def tool_error_result(code: str, message: str = "") -> ToolResult:
    """错误与成功结果共用报文预算，并保留 isError=true。"""
    return compact_tool_result(ToolResult(
        content={"status": "error", "code": code, "message": message or ERROR_MESSAGES.get(code, "操作失败")},
        is_error=True,
    ))


def legacy_tool_error_result(message: str) -> ToolResult:
    """兼容旧工具的中文业务异常，只返回固定说明，避免透传参数及底层错误。"""
    for words, code in (
        (("无权", "未绑定", "身份"), "permission_denied"),
        (("不存在",), "resource_not_found"),
        (("参数", "不能为空", "正整数", "日期格式", "待更新字段"), "invalid_arguments"),
    ):
        if any(word in message for word in words):
            return tool_error_result(code)
    return tool_error_result("operation_failed")
