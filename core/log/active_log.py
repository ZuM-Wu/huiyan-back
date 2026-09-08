"""
全局操作日志函数
慧眼护农 3.4.0 日志子系统

调用规范: 所有关键业务链路必须调用 active_log()
- 登录/登出、管理员 CRUD、农户注册/变更
- 插件安装/卸载/启用/禁用、配置修改、权限变更
"""

import logging
import re
from core.time_utils import china_now
from fastapi import Request

from core.db.base import async_session_factory
from core.db.system_log import SystemLog

logger = logging.getLogger(__name__)

_WINDOWS_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s\]\),;]+")
_UNIX_PATH_RE = re.compile(r"(?<!https:)(?<!http:)(?<![\w.])/(?:[^\s\]\),;]+/)+[^\s\]\),;]+")
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|credential)\s*[=:]\s*[^\s,;]+"
)


def _sanitize_context_value(value: object) -> str:
    """审计字段只保留稳定标识，去除凭据、绝对路径和换行。"""
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _SECRET_VALUE_RE.sub(r"\1=[REDACTED]", text)
    text = _WINDOWS_PATH_RE.sub("[path]", text)
    return _UNIX_PATH_RE.sub("[path]", text)


async def active_log(  # noqa: PLR0917
    description: str,
    log_type: str = "",
    rel_id: int = 0,
    request: Request | None = None,
    db=None,
    request_id: str = "",
    owner: str = "",
    version: str = "",
    current_version: str = "",
    operation_id: str = "",
    phase: str = "",
    result: str = "",
    error_reason: str = "",
) -> bool:
    """
    全局操作日志函数

    参数:
        description: 操作描述
        log_type:    操作类型，如 'login', 'create_farm'
        rel_id:      关联实体ID
        request:     FastAPI Request 对象（自动提取 IP、用户信息）
        db:          数据库会话（可选，不提供时自动创建）

    自动判定:
    - user_type: 从 request.state 提取（admin/farmer/system/cron）
    - user_id:   从 request.state 提取
    - user_name: 从 request.state 提取
    - ip:        从 request.client.host 提取

    安全处理:
    - description 中出现 'password' 时自动替换为 '--REDACTED--'
    - 写入失败静默返回 True

    返回: True（写入成功或静默失败）
    """
    # 平台更新字段暂时复用现有 description 列，避免在未上线阶段扩张日志表。
    # 字段值只允许稳定标识和错误原因，不写入凭据、绝对路径或完整包内容。
    context_fields = {
        "request_id": request_id,
        "owner": owner,
        "version": version,
        "current_version": current_version,
        "operation_id": operation_id,
        "phase": phase,
        "result": result,
        "error_reason": error_reason,
    }
    context_suffix = " ".join(
        f"{key}={_sanitize_context_value(value).replace(' ', '_')[:128]}"
        for key, value in context_fields.items()
        if value
    )
    if context_suffix:
        description = f"{description} [{context_suffix}]"

    # 密码脱敏
    if "password" in description.lower():
        description = "[密码操作已脱敏]"

    # 自动提取请求上下文
    user_type = "system"
    user_id = 0
    user_name = ""
    ip_addr = ""

    if request:
        ip_addr = request.client.host if request.client else ""
        if hasattr(request.state, "user_type"):
            user_type = request.state.user_type
        if hasattr(request.state, "user_id"):
            user_id = request.state.user_id
        if hasattr(request.state, "user_name"):
            user_name = request.state.user_name

    try:
        if db:
            _db = db
            _close = False
        else:
            _db_context = async_session_factory()
            _db = await _db_context.__aenter__()
            _close = True

        log_entry = SystemLog(
            type=log_type,
            rel_id=rel_id,
            description=description,
            user_type=user_type,
            user_id=user_id,
            user_name=user_name,
            ip=ip_addr,
            create_time=china_now(),
        )
        _db.add(log_entry)
        await _db.commit()

        if _close:
            await _db_context.__aexit__(None, None, None)

        logger.debug(f"[active_log] {log_type}: {description} (user={user_name})")
        return True

    except Exception as e:
        logger.error(f"[active_log] 写入失败: {e}")
        return True  # 写入失败静默返回 True
