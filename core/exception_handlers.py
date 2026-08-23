"""
全局异常处理器
从 main.py 抽离，保持 main.py 最小入口原则

浏览器请求（Accept: text/html）返回友好 HTML 页面；
API 请求统一返回 JSON。
"""
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


def _is_browser_request(request: Request) -> bool:
    """判断请求是否来自浏览器（Accept 头包含 text/html）"""
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def _back_url_for(request: Request) -> str:
    """根据请求路径智能判断错误页“返回首页”链接"""
    path = request.url.path
    if path.startswith("/farmer"):
        return "/farmer/home"
    if path.startswith("/admin"):
        return "/admin/dashboard"
    return "/"


# 常见请求字段名 → 中文标签（用于校验失败时生成友好提示）
_FIELD_LABELS = {
    "username": "用户名",
    "password": "密码",
    "old_password": "当前密码",
    "new_password": "新密码",
    "nickname": "昵称",
    "name": "名称",
    "title": "标题",
    "code": "标识",
    "content": "内容",
    "email": "邮箱",
    "phone": "手机号",
    "role_id": "角色",
    "status": "状态",
    "url": "链接地址",
    "sort": "排序",
}


def _friendly_field_error(error: dict) -> str:
    """将单条 Pydantic 校验错误转为中文友好提示（避免直接回显英文原始信息）"""
    loc = [str(x) for x in error.get("loc", ()) if x != "body"]
    field_key = loc[-1] if loc else ""
    label = _FIELD_LABELS.get(field_key, field_key)
    etype = error.get("type", "")
    ctx = error.get("ctx") or {}
    # 字段缺失、或提交了空值 —— 统一提示“请输入”
    if etype == "missing" or (etype == "string_too_short" and error.get("input") in ("", None)):
        return f"请输入{label}"
    if etype == "string_too_short":
        return f"{label}长度至少为 {ctx.get('min_length')} 个字符"
    if etype == "string_too_long":
        return f"{label}长度不能超过 {ctx.get('max_length')} 个字符"
    if etype.endswith("_type"):
        return f"{label}格式不正确"
    # 其余类型保留原始描述，避免吞掉有用细节
    return f"{label}: {error.get('msg', '参数不合法')}"


def register_exception_handlers(app: FastAPI):
    """向 FastAPI app 注册全局异常处理器"""

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Pydantic 校验失败 — 返回 422（中文友好提示）"""
        messages = [_friendly_field_error(e) for e in exc.errors()]
        logging.warning(f"[ValidationError] {request.method} {request.url.path}: {'; '.join(messages)}")
        return JSONResponse(
            status_code=422,
            content={"status": 422, "msg": "；".join(messages)}
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """HTTP 异常 — 浏览器请求返回友好 HTML，API 返回 JSON"""
        if _is_browser_request(request) and exc.status_code in (400, 403, 404, 500, 503):
            from core.view_controller import _error_html, _ERROR_META
            meta = _ERROR_META.get(exc.status_code)
            title = meta[0] if meta else f"错误 {exc.status_code}"
            detail = str(exc.detail) if exc.detail else ""
            return HTMLResponse(
                content=_error_html(title, detail, exc.status_code, _back_url_for(request)),
                status_code=exc.status_code
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": exc.status_code, "msg": exc.detail}
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """全局兜底"""
        logging.error(f"[UnhandledError] {request.method} {request.url.path}: {exc}\n{traceback.format_exc()}")
        if _is_browser_request(request):
            from core.view_controller import _error_html
            return HTMLResponse(
                content=_error_html("服务器错误", "服务器内部异常，请稍后重试", 500, _back_url_for(request)),
                status_code=500
            )
        return JSONResponse(
            status_code=500,
            content={"status": 500, "msg": "服务器内部错误，请稍后重试"}
        )
