"""主题和插件操作审计门面。"""

from fastapi import Request

from core.log.active_log import active_log
from core.platform.context import current_context


async def audit_log(
    description: str,
    log_type: str,
    *,
    owner: str = "",
    version: str = "",
    current_version: str = "",
    operation_id: str = "",
    phase: str = "",
    result: str = "success",
    error_reason: str = "",
    rel_id: int = 0,
    request: Request | None = None,
    db=None,
) -> bool:
    """写入带平台上下文的操作日志。"""
    context = current_context()
    request_id = context.request_id or str(getattr(getattr(request, "state", None), "request_id", ""))
    return await active_log(
        description,
        log_type=log_type,
        rel_id=rel_id,
        request=request,
        db=db,
        request_id=request_id,
        owner=owner or context.owner,
        version=version or context.version,
        current_version=current_version,
        operation_id=operation_id or context.operation_id,
        phase=phase or context.phase,
        result=result,
        error_reason=error_reason,
    )
