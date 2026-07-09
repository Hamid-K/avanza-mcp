"""Headless Avanza login flow shared by the TUI and Web front-ends.

The staged call sequence (connect, overview, portfolio, stop-losses, orders)
is identical for every host; hosts differ only in how they surface progress.
A host passes ``run_stage(message, stage_index, fn, *args)`` — the TUI wires
this to its pulse-threaded progress display, the web streams it over a
WebSocket channel — or omits it for silent execution.
"""

from dataclasses import dataclass
from typing import Any, Callable

from avanza import Avanza

StageRunner = Callable[..., Any]


@dataclass
class LoginResult:
    avanza: Avanza
    overview: dict[str, Any]
    portfolio: dict[str, Any]
    stoplosses: Any
    orders: Any


def _run_directly(message: str, stage_index: int, fn: Callable[..., Any], *args: Any) -> Any:
    return fn(*args)


def perform_login_headless(
    credentials: dict[str, str],
    connect_stage_index: int = 0,
    run_stage: StageRunner | None = None,
) -> LoginResult:
    runner = run_stage or _run_directly
    avanza = runner("Connecting to Avanza...", connect_stage_index, Avanza, credentials)

    overview = runner(
        "Loading account overview...",
        connect_stage_index + 1,
        avanza.get_overview,
    )
    if not isinstance(overview, dict):
        raise RuntimeError(f"Unexpected account overview response type: {type(overview).__name__}")

    portfolio = runner(
        "Loading portfolio...",
        connect_stage_index + 2,
        avanza.get_accounts_positions,
    )
    if not isinstance(portfolio, dict):
        raise RuntimeError(f"Unexpected portfolio response type: {type(portfolio).__name__}")

    stoplosses = runner(
        "Loading stop-losses and open orders...",
        connect_stage_index + 3,
        avanza.get_all_stop_losses,
    )
    try:
        orders = avanza.get_orders()
    except Exception:
        orders = []

    return LoginResult(avanza=avanza, overview=overview, portfolio=portfolio, stoplosses=stoplosses, orders=orders)
