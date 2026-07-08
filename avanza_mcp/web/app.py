"""FastAPI application factory for the Avanza-MCP Web UI."""

import asyncio
import base64
import hashlib
import re
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from avanza_mcp.config import APP_VERSION
from avanza_mcp.web.api.data import router as data_router
from avanza_mcp.web.api.sessions import router as sessions_router
from avanza_mcp.web.api.mcp import router as mcp_router
from avanza_mcp.web.api.paper import router as paper_router
from avanza_mcp.web.api.trading import router as trading_router
from avanza_mcp.web.auth import COOKIE_NAME, WebAuth
from avanza_mcp.web.runtime import WebRuntime

STATIC_DIR = Path(__file__).parent / "static"

_PUBLIC_PATHS = {"/", "/favicon.ico", "/api/auth/login", "/api/auth/me"}
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _import_map_hashes() -> str:
    """CSP sha256 sources for the inline import map(s) in index.html.

    The strict CSP blocks inline scripts; the import map is the one inline
    script we need, so it is allow-listed by content hash. Computed from the
    file at startup so edits to the map can never silently break the page.
    """
    html = (STATIC_DIR / "index.html").read_text()
    sources = []
    for match in re.finditer(r'<script type="importmap">(.*?)</script>', html, re.S):
        digest = hashlib.sha256(match.group(1).encode()).digest()
        sources.append("'sha256-" + base64.b64encode(digest).decode() + "'")
    return " ".join(sources)


def _csp_value(port: int) -> str:
    # 'unsafe-eval' is required by Vue's runtime template compiler (the
    # no-build setup compiles template strings with new Function()). Inline
    # SCRIPT injection remains blocked: only self, the two pinned CDN files,
    # and the hash-pinned import map may execute.
    return (
        "default-src 'none'; "
        f"script-src 'self' https://cdn.jsdelivr.net 'unsafe-eval' {_import_map_hashes()}; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port} https://cdn.jsdelivr.net; "
        "font-src 'self'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )


def create_web_app(runtime: WebRuntime) -> FastAPI:
    auth: WebAuth = runtime.auth
    app = FastAPI(title="Avanza-MCP Web", version=APP_VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    csp = _csp_value(runtime.port)

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        path = request.url.path
        is_api = path.startswith("/api/")
        if is_api and path not in _PUBLIC_PATHS:
            denial = auth.check_request(request, mutating=request.method in _MUTATING_METHODS)
            if denial is not None:
                return denial
        elif is_api and request.method in _MUTATING_METHODS:
            # public API paths still get origin validation
            if not auth._origin_ok(request.headers.get("origin"), request.headers.get("host")):
                return JSONResponse({"error": "origin_rejected"}, status_code=403)

        response: Response = await call_next(request)
        if is_api:
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = csp
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.on_event("startup")
    async def _startup() -> None:
        runtime.event_bus.attach_loop(asyncio.get_running_loop())
        runtime.start_background_loops()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        runtime.stop()

    # ------------------------------------------------------------------ auth

    @app.post("/api/auth/login")
    async def auth_login(request: Request):
        if not auth._origin_ok(request.headers.get("origin"), request.headers.get("host")):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = str((body or {}).get("token", ""))
        session_value = await asyncio.to_thread(auth.attempt_login, token)
        if session_value is None:
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        response = JSONResponse({"ok": True, "csrf_token": session_value})
        response.set_cookie(
            COOKIE_NAME,
            session_value,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request):
        denial = auth.check_request(request, mutating=True)
        if denial is not None:
            return denial
        auth.logout()
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/api/auth/me")
    async def auth_me(request: Request):
        authenticated = auth.cookie_ok(request.cookies.get(COOKIE_NAME))
        payload = {"authenticated": authenticated}
        if authenticated:
            payload["csrf_token"] = auth.session_value
        return payload

    # ------------------------------------------------------------------ meta

    @app.get("/api/meta")
    async def meta(request: Request):
        kernel = runtime.kernel
        return {
            "app_version": APP_VERSION,
            "paper_mode": kernel.paper_mode_enabled,
            "debug": kernel.debug_mode,
            "update": {
                "text": kernel.update_status_text,
                "latest": kernel.update_status_latest,
                "outdated": kernel.update_status_outdated,
                "error": kernel.update_status_error,
            },
            "has_session": bool(kernel.tenant_sessions),
        }

    # ------------------------------------------------------------------ ws

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        if not auth.check_websocket(websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        client_id, queue = runtime.event_bus.subscribe()
        try:
            await websocket.send_json({"type": "hello", "seq": 0, "payload": {"app_version": APP_VERSION}})
            while True:
                sender = asyncio.create_task(queue.get())
                receiver = asyncio.create_task(websocket.receive_text())
                done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                if receiver in done:
                    try:
                        receiver.result()
                    except (WebSocketDisconnect, RuntimeError):
                        break
                if sender in done:
                    await websocket.send_json(sender.result())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            runtime.event_bus.unsubscribe(client_id)

    app.include_router(sessions_router)
    app.include_router(data_router)
    app.include_router(trading_router)
    app.include_router(mcp_router)
    app.include_router(paper_router)

    # ------------------------------------------------------------------ static

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
