"""Paper-trading state and TradingView list endpoints."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from avanza_mcp.paper import paper_orders, paper_positions, paper_session_summary, paper_trades

router = APIRouter()


def _kernel(request: Request):
    return request.app.state.runtime.kernel


@router.get("/api/paper/state")
async def paper_state(request: Request):
    kernel = _kernel(request)

    def work():
        with kernel.state_lock:
            session = kernel.paper_session
            account_id = kernel.selected_account_id
            payload = {
                "paper_mode": kernel.paper_mode_enabled,
                "positions": paper_positions(session, account_id=None),
                "open_positions": paper_positions(session, account_id=None, active_only=True),
                "orders": paper_orders(session, account_id=None),
                "active_orders": paper_orders(session, account_id=None, active_only=True),
                "trades": paper_trades(session, account_id=None),
                "summary": paper_session_summary(session),
                "risk": None,
            }
            if account_id and kernel.avanza is not None:
                try:
                    payload["risk"] = kernel.execute_mcp_tool("avanza_paper_risk_state", {"account_id": account_id})
                except Exception:
                    payload["risk"] = None
            return payload

    return await asyncio.to_thread(work)


@router.get("/api/tv/lists")
async def tv_lists(request: Request, list_id: str = "", limit: int = 200):
    kernel = _kernel(request)

    def work():
        with kernel.state_lock:
            arguments = {"limit": int(limit)}
            if list_id:
                arguments["list_id"] = list_id
            return kernel.execute_mcp_tool("tv_auth_custom_lists", arguments)

    try:
        return await asyncio.to_thread(work)
    except PermissionError as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    except Exception as exc:
        return JSONResponse({"error": "tv_failed", "detail": str(exc)}, status_code=502)
