"""仅限本机的控制中心会话认证。"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, Response


COOKIE_NAME = "hy_control_session"
ALLOWED_ORIGINS = {"http://localhost:8765", "http://127.0.0.1:8765"}


@dataclass
class LocalSession:
    session_id: str
    csrf_token: str
    expires_at: float


class LocalSessionManager:
    """以启动令牌换取短期 HttpOnly 会话，避免网页直接触发本机运维。"""

    def __init__(self, runtime_dir: Path, lifetime_seconds: int = 12 * 60 * 60) -> None:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self.token_path = runtime_dir / "control-center.token"
        self.launch_token = self._load_or_create_token()
        self.lifetime_seconds = lifetime_seconds
        self._sessions: dict[str, LocalSession] = {}

    def _load_or_create_token(self) -> str:
        if self.token_path.is_file():
            token = self.token_path.read_text(encoding="utf-8").strip()
            if len(token) >= 32:
                return token
        token = secrets.token_urlsafe(32)
        self.token_path.write_text(token, encoding="utf-8")
        try:
            self.token_path.chmod(0o600)
        except OSError:
            pass
        return token

    def bootstrap(self, token: str, response: Response) -> dict:
        if not secrets.compare_digest(str(token or ""), self.launch_token):
            raise HTTPException(status_code=401, detail="控制中心启动令牌无效")
        now = time.time()
        session = LocalSession(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(24),
            expires_at=now + self.lifetime_seconds,
        )
        self._sessions[session.session_id] = session
        self._prune(now)
        response.set_cookie(
            COOKIE_NAME,
            session.session_id,
            max_age=self.lifetime_seconds,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return {"authenticated": True, "csrf_token": session.csrf_token}

    def require(self, request: Request, *, write: bool = False) -> LocalSession:
        now = time.time()
        session = self._sessions.get(request.cookies.get(COOKIE_NAME, ""))
        if not session or session.expires_at <= now:
            raise HTTPException(status_code=401, detail="请通过桌面快捷方式重新打开控制中心")
        if write:
            origin = request.headers.get("origin", "")
            csrf = request.headers.get("x-control-csrf", "")
            if origin not in ALLOWED_ORIGINS or not secrets.compare_digest(csrf, session.csrf_token):
                raise HTTPException(status_code=403, detail="控制中心请求来源校验失败")
        return session

    def _prune(self, now: float) -> None:
        expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)
