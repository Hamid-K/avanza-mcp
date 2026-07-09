"""MCP management endpoints: bridge lifecycle, R/W, live-trading authorization.

Semantics mirror the TUI toggles exactly: the bridge is opt-in (never
auto-started), disabling R/W revokes live-trading authorization, and the
session file is rewritten on every mode change. Arming live trading from the
web requires the typed "LIVE" confirmation.
"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from avanza_mcp.web.serializers import mcp_status_web_payload

router = APIRouter()


def _kernel(request: Request):
    return request.app.state.runtime.kernel


async def _run(kernel, fn, *args, **kwargs):
    def work():
        with kernel.state_lock:
            return fn(*args, **kwargs)

    return await asyncio.to_thread(work)


@router.get("/api/mcp/status")
async def mcp_status(request: Request):
    return mcp_status_web_payload(_kernel(request))


@router.get("/api/mcp/log")
async def mcp_log(request: Request):
    kernel = _kernel(request)
    return {"entries": list(getattr(kernel, "web_mcp_log", []))}


@router.post("/api/mcp/bridge")
async def mcp_bridge(request: Request):
    kernel = _kernel(request)
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    try:
        if enabled and kernel.mcp_server is None:
            await _run(kernel, kernel.start_mcp_bridge)
        elif not enabled and kernel.mcp_server is not None:
            await _run(kernel, kernel.stop_mcp_bridge)
    except RuntimeError as exc:
        return JSONResponse({"error": "bridge_failed", "detail": str(exc)}, status_code=409)
    kernel.update_mode_toggles()
    return mcp_status_web_payload(kernel)


@router.post("/api/mcp/read-write")
async def mcp_read_write(request: Request):
    kernel = _kernel(request)
    body = await request.json()
    enabled = bool(body.get("enabled", False))

    def work() -> None:
        kernel.mcp_write_enabled = enabled
        if not kernel.mcp_write_enabled:
            kernel.live_trading_allowed_for_session = False
        kernel.update_mcp_session_file()
        mode = "read/write" if kernel.mcp_write_enabled else "read-only"
        kernel.write_mcp_log(f"MCP mode: {mode}.")

    await _run(kernel, work)
    kernel.update_mode_toggles()
    return mcp_status_web_payload(kernel)


@router.post("/api/mcp/live-trading")
async def mcp_live_trading(request: Request):
    kernel = _kernel(request)
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    if enabled:
        if not kernel.mcp_write_enabled:
            return JSONResponse(
                {"error": "read_write_required", "detail": "Enable MCP R/W mode before authorizing live trading."},
                status_code=409,
            )
        if not bool(body.get("acknowledge", False)):
            return JSONResponse(
                {"error": "acknowledge_required", "detail": "Confirm live trading authorization."},
                status_code=403,
            )

    def work() -> None:
        kernel.live_trading_allowed_for_session = enabled
        state = "AUTHORIZED" if enabled else "revoked"
        kernel.write_mcp_log(f"Live trading for this session: {state} (web).")

    await _run(kernel, work)
    kernel.update_mode_toggles()
    return mcp_status_web_payload(kernel)
