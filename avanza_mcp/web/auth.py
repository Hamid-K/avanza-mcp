"""Web UI authentication: startup token, cookie session, CSRF, origin checks.

Model: at startup a random token is generated, written to
``.avanza_web_session.json`` (chmod 600), and printed to the terminal. The
user pastes it into the login form once; the server then sets an HttpOnly
SameSite=Strict session cookie. Mutating requests must additionally echo the
session value in the ``X-Avanza-Web-Token`` header (double-submit CSRF), and
Origin/Host are validated against the local bind. The server only ever binds
127.0.0.1.
"""

import hmac
import json
import os
import secrets
import time
from datetime import datetime
from typing import Any

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse

from avanza_mcp import config

COOKIE_NAME = "avanza_web_auth"
CSRF_HEADER = "x-avanza-web-token"
_FAILURE_DELAY_SECONDS = 0.4


class WebAuth:
    def __init__(self, port: int) -> None:
        self.port = int(port)
        self.login_token = secrets.token_urlsafe(24)
        self.session_value: str | None = None
        self.failed_attempts = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def write_session_file(self) -> None:
        payload = {
            "url": self.url,
            "token": self.login_token,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        config.WEB_SESSION_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(config.WEB_SESSION_FILE, 0o600)
        except OSError:
            pass

    def remove_session_file(self) -> None:
        try:
            config.WEB_SESSION_FILE.unlink()
        except (FileNotFoundError, OSError):
            pass

    def attempt_login(self, token: str) -> str | None:
        """Return the session value on success; None (after a delay) on failure."""
        supplied = str(token or "")
        if not hmac.compare_digest(supplied, self.login_token):
            self.failed_attempts += 1
            time.sleep(min(_FAILURE_DELAY_SECONDS * self.failed_attempts, 3.0))
            return None
        self.failed_attempts = 0
        self.session_value = secrets.token_urlsafe(24)
        return self.session_value

    def logout(self) -> None:
        self.session_value = None

    # ------------------------------------------------------------------

    def _origin_ok(self, origin: str | None, host: str | None) -> bool:
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if host is not None and host not in allowed:
            return False
        if origin:
            origin = origin.rstrip("/")
            if origin not in {f"http://{a}" for a in allowed}:
                return False
        return True

    def cookie_ok(self, cookie_value: str | None) -> bool:
        return bool(
            self.session_value
            and cookie_value
            and hmac.compare_digest(str(cookie_value), self.session_value)
        )

    def check_request(self, request: Request, *, mutating: bool) -> JSONResponse | None:
        """Return an error response if the request is not authorized, else None."""
        if not self._origin_ok(request.headers.get("origin"), request.headers.get("host")):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        if not self.cookie_ok(request.cookies.get(COOKIE_NAME)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if mutating:
            header = request.headers.get(CSRF_HEADER)
            if not (header and hmac.compare_digest(str(header), self.session_value or "")):
                return JSONResponse({"error": "csrf_required"}, status_code=403)
        return None

    def check_websocket(self, websocket: WebSocket) -> bool:
        if not self._origin_ok(websocket.headers.get("origin"), websocket.headers.get("host")):
            return False
        return self.cookie_ok(websocket.cookies.get(COOKIE_NAME))


def auth_state(request: Request, auth: "WebAuth") -> dict[str, Any]:
    return {"authenticated": auth.cookie_ok(request.cookies.get(COOKIE_NAME))}
