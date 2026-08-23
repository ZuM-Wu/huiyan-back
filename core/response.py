# -*- coding: utf-8 -*-
"""
统一响应信封 helper

信封格式: {"status": <业务码>, "msg": <提示>, "data": <载荷>}
与前端 request.js 拦截器约定一致（status==200 视为成功）。

收敛约定（Docs/03）: 所有新增端点必须使用本模块构造响应；
存量端点响应格式保持现状不动（测试基线与前端拦截器已容错）。
"""


def ok(data=None, msg: str = "操作成功") -> dict:
    """成功信封 — status 固定 200"""
    return {"status": 200, "msg": msg, "data": data}


def fail(code: int = 400, msg: str = "操作失败") -> dict:
    """失败信封 — code 为业务错误码（通常与 HTTP 状态码对齐）"""
    return {"status": code, "msg": msg, "data": None}
