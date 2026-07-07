"""Tenant-session endpoints: Avanza login (primary/extra/re-auth), activate, logout."""

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from avanza_mcp import auth as avanza_auth
from avanza_mcp.core.login import LoginResult, perform_login_headless
from avanza_mcp.rendering import account_rows_from_overview, default_account, open_order_items
from avanza_mcp.web.serializers import sessions_payload

router = APIRouter()


def _apply_login_result(kernel: Any, result: LoginResult, *, refresh_session_id: str | None, label: str | None) -> str:
    """Register a new tenant session (or refresh one in place) from a login result.

    Mirrors the state core of the TUI's complete_login. Returns the session id.
    """
    with kernel.state_lock:
        refresh_token = str(refresh_session_id or "").strip()
        if refresh_token and refresh_token in kernel.tenant_sessions:
            context = kernel.tenant_sessions[refresh_token]
            accounts = account_rows_from_overview(result.overview)
            context.avanza = result.avanza
            if label:
                context.label = str(label)
            context.accounts = accounts
            selected = str(context.selected_account_id or "").strip()
            if not selected or not any(str(item.get("id", "")) == selected for item in accounts):
                default = default_account(accounts)
                context.selected_account_id = str(default.get("id", "")) if default else None
            context.latest_portfolio_data = result.portfolio if isinstance(result.portfolio, dict) else None
            context.latest_stoploss_items = (
                [item for item in result.stoplosses if isinstance(item, dict)]
                if isinstance(result.stoplosses, list)
                else []
            )
            context.latest_open_order_items = [item for item in open_order_items(result.orders) if isinstance(item, dict)]
            context.auth_valid = True
            context.auth_error = ""
            kernel.live_refresh_auth_blocked_sessions.discard(context.session_id)
            kernel.live_refresh_auth_last_notice_at.pop(context.session_id, None)
        else:
            context = kernel.register_tenant_session(
                result.avanza,
                result.overview,
                result.portfolio,
                result.stoplosses,
                result.orders,
                label=label,
            )
        kernel.update_tenant_session_data_cache(
            context.session_id,
            result.overview,
            result.portfolio if isinstance(result.portfolio, dict) else None,
            result.stoplosses,
            result.orders,
        )
        kernel.load_active_state_from_tenant(context)
        kernel.position_row_cache = {}
    kernel.on_state_changed("sessions")
    kernel.on_state_changed("portfolio")
    kernel.write_log(f"Web login completed for session {context.label} ({context.session_id}).")
    return context.session_id


def _login_worker(kernel: Any, body: dict[str, Any]) -> str:
    mode = str(body.get("mode", "credentials")).strip().lower()
    if mode == "1password":
        item = str(body.get("op_item", "")).strip()
        if not item:
            raise ValueError("1Password item is required.")
        vault = str(body.get("op_vault", "")).strip() or None
        credentials = avanza_auth.onepassword_credentials(item, vault)
    else:
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        totp = str(body.get("totp", "")).strip()
        if not username or not password:
            raise ValueError("Username and password are required.")
        credentials = {"username": username, "password": password}
        if totp:
            credentials["totpCode"] = totp

    def on_stage(message: str, index: int, fn, *args):
        kernel.event_bus.publish("login_progress", {"message": message, "index": index})
        return fn(*args)

    result = perform_login_headless(credentials, run_stage=on_stage)
    return _apply_login_result(
        kernel,
        result,
        refresh_session_id=body.get("refresh_session_id"),
        label=str(body.get("label", "")).strip() or None,
    )


@router.post("/api/sessions")
async def create_session(request: Request):
    kernel = request.app.state.runtime.kernel
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        session_id = await asyncio.to_thread(_login_worker, kernel, body or {})
    except ValueError as exc:
        kernel.event_bus.publish("login_progress", None)
        return JSONResponse({"error": "invalid_request", "detail": str(exc)}, status_code=400)
    except Exception as exc:
        kernel.event_bus.publish("login_progress", None)
        return JSONResponse({"error": "login_failed", "detail": str(exc)}, status_code=502)
    kernel.event_bus.publish("login_progress", None)
    return {"ok": True, "session_id": session_id, **sessions_payload(kernel)}


@router.get("/api/sessions")
async def list_sessions(request: Request):
    return sessions_payload(request.app.state.runtime.kernel)


@router.post("/api/sessions/{session_id}/activate")
async def activate_session(session_id: str, request: Request):
    kernel = request.app.state.runtime.kernel

    def work() -> None:
        with kernel.state_lock:
            kernel.activate_tenant_session(session_id)

    try:
        await asyncio.to_thread(work)
    except ValueError as exc:
        return JSONResponse({"error": "unknown_session", "detail": str(exc)}, status_code=404)
    kernel.on_state_changed("portfolio")
    return {"ok": True, **sessions_payload(kernel)}


@router.delete("/api/sessions/{session_id}")
async def logout_session(session_id: str, request: Request):
    kernel = request.app.state.runtime.kernel
    if session_id not in kernel.tenant_sessions:
        return JSONResponse({"error": "unknown_session"}, status_code=404)

    def work() -> None:
        with kernel.state_lock:
            kernel.logout_session_state(session_id)

    await asyncio.to_thread(work)
    kernel.on_state_changed("sessions")
    kernel.on_state_changed("portfolio")
    return {"ok": True, **sessions_payload(kernel)}


@router.delete("/api/sessions")
async def logout_all(request: Request):
    kernel = request.app.state.runtime.kernel

    def work() -> None:
        with kernel.state_lock:
            kernel.logout_all_sessions_state()

    await asyncio.to_thread(work)
    kernel.on_state_changed("sessions")
    kernel.on_state_changed("portfolio")
    return {"ok": True, **sessions_payload(kernel)}
