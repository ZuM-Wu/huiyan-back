"""慧眼本地控制中心 FastAPI 应用。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from control_center.auth import ALLOWED_ORIGINS, LocalSessionManager
from control_center.log_store import ControlLog
from control_center.service import ControlCenterService


BACKEND_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = BACKEND_ROOT / "runtime" / "control-center"
STATIC_DIR = Path(__file__).resolve().parent / "static"
SHARED_STATIC_DIR = BACKEND_ROOT / "templates" / "shared" / "static"
PLUGIN_PAGE_ORIGINS = {"http://127.0.0.1:8000", "http://localhost:8000"}
logger = logging.getLogger(__name__)


class BootstrapBody(BaseModel):
    token: str


class BatchApplyBody(BaseModel):
    """按 operation_id 精确选择控制中心要应用的计划。"""

    operation_ids: list[str] = Field(..., min_length=1)


def _runtime_error(status: int, exc: RuntimeError) -> HTTPException:
    """把控制中心已知运行态冲突转换为稳定业务错误码。"""
    message = str(exc)
    if "8000 端口" in message or "127.0.0.1:8000 正在监听" in message:
        detail = {"code": "backend_running", "message": message}
    else:
        detail = message
    return HTTPException(status_code=status, detail=detail)


def create_app(shutdown_callback: Callable[[], None] | None = None) -> FastAPI:  # noqa: C901, PLR0915
    """创建与业务后端相互独立的本机控制应用。"""
    log = ControlLog(RUNTIME_DIR)
    auth = LocalSessionManager(RUNTIME_DIR)
    service = ControlCenterService(BACKEND_ROOT, log)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        log.append("control", "慧眼本地控制中心已启动")
        auto_start = __import__("asyncio").create_task(service.auto_start())
        try:
            yield
        finally:
            auto_start.cancel()
            await service.close()
            log.append("control", "慧眼本地控制中心已退出")
            log.close()

    app = FastAPI(
        title="慧眼本地控制中心", version="1.0.0", lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.state.control_log = log
    app.state.local_auth = auth
    app.state.control_service = service
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "testserver"],
    )

    @app.middleware("http")
    async def ping_cors(request: Request, call_next):
        response = await call_next(request)
        if response.status_code >= 400:
            logger.warning(
                "控制中心请求失败: host=%s path=%s status=%d",
                request.headers.get("host", ""), request.url.path, response.status_code,
            )
        origin = request.headers.get("origin", "")
        if request.url.path == "/api/ping" and origin in PLUGIN_PAGE_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/api/ping")
    async def ping():
        return {"status": "ok", "protocol_version": 1}

    @app.post("/api/session/bootstrap")
    async def bootstrap(body: BootstrapBody, request: Request, response: Response):
        if request.headers.get("origin", "") not in ALLOWED_ORIGINS:
            raise HTTPException(status_code=403, detail="控制中心请求来源校验失败")
        return auth.bootstrap(body.token, response)

    @app.get("/api/session")
    async def session(request: Request):
        current = auth.require(request)
        return {"authenticated": True, "csrf_token": current.csrf_token}

    @app.get("/api/state")
    async def state(request: Request):
        auth.require(request)
        return await service.state()

    @app.post("/api/backend/{action}", status_code=202)
    async def backend_action(action: str, request: Request):
        auth.require(request, write=True)
        try:
            job = service.submit_backend_action(action)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise _runtime_error(409, exc) from exc
        return job.public()

    @app.post("/api/plugin-updates/apply-all", status_code=202)
    async def apply_all_updates(request: Request):
        auth.require(request, write=True)
        try:
            return service.submit_update(apply_all=True).public()
        except (ValueError, RuntimeError) as exc:
            if isinstance(exc, RuntimeError):
                raise _runtime_error(409, exc) from exc
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/plugin-updates/apply-batch", status_code=202)
    async def apply_batch_updates(body: BatchApplyBody, request: Request):
        auth.require(request, write=True)
        try:
            return service.submit_update_many(body.operation_ids).public()
        except (ValueError, RuntimeError) as exc:
            if isinstance(exc, RuntimeError):
                raise _runtime_error(409, exc) from exc
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/plugin-updates/{operation_id}/apply", status_code=202)
    async def apply_update(operation_id: str, request: Request):
        auth.require(request, write=True)
        try:
            return service.submit_update(operation_id=operation_id).public()
        except (ValueError, RuntimeError) as exc:
            if isinstance(exc, RuntimeError):
                raise _runtime_error(409, exc) from exc
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/operations/{job_id}")
    async def operation(job_id: str, request: Request):
        auth.require(request)
        job = service.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="控制中心操作不存在")
        return job.public()

    @app.get("/api/logs")
    async def logs(request: Request, cursor: int = 0, limit: int = 500):
        auth.require(request)
        return log.read_after(cursor, limit)

    @app.post("/api/control-center/exit", status_code=202)
    async def exit_control_center(request: Request):
        auth.require(request, write=True)
        if shutdown_callback is None:
            raise HTTPException(status_code=503, detail="当前启动方式不支持退出控制中心")
        try:
            return service.submit_exit(shutdown_callback).public()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="control-assets")
    app.mount("/shared", StaticFiles(directory=SHARED_STATIC_DIR), name="shared-assets")

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app
