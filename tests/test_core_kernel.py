import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from avanza_mcp.core.kernel import TradingKernel
from avanza_mcp.core.login import perform_login_headless
from avanza_mcp.core.trading import build_regular_order_request_from_fields, build_stop_loss_request_from_fields

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate_runtime_files(monkeypatch, tmp_path):
    monkeypatch.setattr("avanza_mcp.config.LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("avanza_mcp.config.PAPER_SESSION_FILE", tmp_path / "paper-session.json")
    monkeypatch.setenv("AVANZA_MCP_SESSION_BACKEND", "file")
    monkeypatch.setenv("AVANZA_UPDATE_CHECK_ENABLED", "0")


def make_kernel(**kwargs) -> TradingKernel:
    kernel = TradingKernel()
    kernel.init_kernel_state(**kwargs)
    return kernel


def test_core_package_does_not_import_textual():
    code = (
        "import sys\n"
        "sys.path.insert(0, r'%s')\n"
        "import avanza_mcp.core\n"
        "import avanza_mcp.core.kernel\n"
        "assert not any(m == 'textual' or m.startswith('textual.') for m in sys.modules), 'textual leaked into core'\n"
    ) % PROJECT_ROOT
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_kernel_state_matches_tui_state(monkeypatch, tmp_path):
    from avanza_mcp.tui.app import AvanzaTradingTui

    kernel = make_kernel()
    app = AvanzaTradingTui()
    for attr in (
        "avanza", "tenant_sessions", "active_session_id", "session_label_counter",
        "accounts", "selected_account_id", "mcp_scope_depth", "mcp_write_enabled",
        "live_trading_allowed_for_session", "paper_mode_enabled",
        "latest_portfolio_data", "latest_stoploss_items", "latest_open_order_items",
        "update_status_outdated", "live_refresh_inflight",
    ):
        assert getattr(kernel, attr) == getattr(app, attr), attr
    assert kernel.paper_session_path == app.paper_session_path


def test_live_mutations_allowed_truth_table():
    kernel = make_kernel()
    kernel.active_session_id = "s1"
    cases = [
        # (paper_mode, rw, live_auth) -> allowed
        (True, True, True, False),
        (False, False, True, False),
        (False, True, False, False),
        (False, True, True, True),
    ]
    for paper, rw, live_auth, expected in cases:
        kernel.paper_mode_enabled = paper
        kernel.mcp_write_enabled = rw
        kernel.live_trading_allowed_for_session = live_auth
        assert kernel.live_mutations_allowed() is expected, (paper, rw, live_auth)


def test_live_authorization_is_scoped_to_the_armed_session():
    """P0 regression: arming one tenant session must not arm any other."""
    kernel = make_kernel()

    class FakeAvanza:
        pass

    overview = {"accounts": [{"id": "a1", "name": "Main"}]}
    first = kernel.register_tenant_session(FakeAvanza(), overview, None, [], [], label="One")
    second = kernel.register_tenant_session(FakeAvanza(), overview, None, [], [], label="Two")
    kernel.load_active_state_from_tenant(first)
    kernel.paper_mode_enabled = False
    kernel.mcp_write_enabled = True

    kernel.live_trading_allowed_for_session = True  # arms `first` only
    assert kernel.live_mutations_allowed() is True
    with kernel.temporary_tenant_scope(second.session_id):
        assert kernel.live_trading_allowed_for_session is False
        assert kernel.live_mutations_allowed() is False
    assert kernel.live_mutations_allowed() is True

    # arming with no active session is refused
    kernel.live_trading_authorized_session_ids.clear()
    kernel.active_session_id = None
    kernel.live_trading_allowed_for_session = True
    assert kernel.live_trading_authorized_session_ids == set()

    # logout drops authorization
    kernel.load_active_state_from_tenant(first)
    kernel.live_trading_allowed_for_session = True
    assert first.session_id in kernel.live_trading_authorized_session_ids
    kernel.logout_session_state(first.session_id)
    assert first.session_id not in kernel.live_trading_authorized_session_ids


def test_temporary_tenant_scope_restores_state_and_depth():
    kernel = make_kernel()

    class FakeAvanza:
        def get_overview(self):
            return {"accounts": []}

    overview = {"accounts": [{"id": "a1", "name": "Main", "totalValue": {"value": 1.0}}]}
    session = kernel.register_tenant_session(FakeAvanza(), overview, None, [], [], label="One")
    other = kernel.register_tenant_session(FakeAvanza(), overview, None, [], [], label="Two")
    kernel.load_active_state_from_tenant(session)
    assert kernel.active_session_id == session.session_id
    assert kernel.mcp_scope_depth == 0
    with kernel.temporary_tenant_scope(other.session_id):
        assert kernel.active_session_id == other.session_id
        assert kernel.mcp_scope_depth == 1
    assert kernel.active_session_id == session.session_id
    assert kernel.mcp_scope_depth == 0


def test_register_tenant_session_assigns_color_and_label():
    kernel = make_kernel()

    class FakeAvanza:
        pass

    overview = {"accounts": [{"id": "a1", "name": "Main"}]}
    session = kernel.register_tenant_session(FakeAvanza(), overview, None, [], [], label="Primary")
    assert session.session_id in kernel.tenant_sessions
    assert session.label == "Primary"
    assert session.color.startswith("#")


def test_mark_tenant_session_auth_expired_blocks_live_refresh():
    kernel = make_kernel()

    class FakeAvanza:
        pass

    overview = {"accounts": [{"id": "a1", "name": "Main"}]}
    session = kernel.register_tenant_session(FakeAvanza(), overview, None, [], [], label="One")
    kernel.mark_tenant_session_auth_expired(session.session_id, RuntimeError("401"))
    assert session.auth_valid is False
    assert session.session_id in kernel.live_refresh_auth_blocked_sessions
    kernel.mark_tenant_session_auth_ok(session.session_id)
    assert session.auth_valid is True
    assert session.session_id not in kernel.live_refresh_auth_blocked_sessions


def test_perform_login_headless_sequence(monkeypatch):
    calls = []

    class FakeAvanza:
        def __init__(self, credentials):
            calls.append(("connect", credentials))

        def get_overview(self):
            calls.append(("overview",))
            return {"accounts": []}

        def get_accounts_positions(self):
            calls.append(("positions",))
            return {"withOrderbook": []}

        def get_all_stop_losses(self):
            calls.append(("stoplosses",))
            return []

        def get_orders(self):
            calls.append(("orders",))
            return []

    monkeypatch.setattr("avanza_mcp.core.login.Avanza", FakeAvanza)
    result = perform_login_headless({"username": "u", "password": "p", "totpSecret": "s"})
    assert [c[0] for c in calls] == ["connect", "overview", "positions", "stoplosses", "orders"]
    assert isinstance(result.overview, dict)
    stages = []
    monkeypatch.setattr("avanza_mcp.core.login.Avanza", FakeAvanza)
    perform_login_headless({}, run_stage=lambda msg, idx, fn, *a: (stages.append((msg, idx)), fn(*a))[1])
    assert stages[0] == ("Connecting to Avanza...", 0)
    assert len(stages) == 4


def test_build_stop_loss_request_from_fields_happy_path():
    valid_until = date.today() + timedelta(days=5)
    trigger, order_event, preview = build_stop_loss_request_from_fields(
        {
            "account_id": "a1",
            "order_book_id": "123",
            "valid_until": valid_until,
            "trigger_type": "follow_downwards",
            "trigger_value": 5.0,
            "trigger_value_type": "percentage",
            "trigger_on_market_maker_quote": False,
            "order_type": "sell",
            "order_price": 2.0,
            "volume": 10,
            "order_valid_days": 1,
            "order_price_type": "percentage",
            "short_selling_allowed": False,
        }
    )
    assert preview["account_id"] == "a1"
    assert preview["order_book_id"] == "123"
    assert preview["stop_loss_order_event"]["valid_days"] == 1
    assert order_event.volume == 10


def test_build_requests_reject_missing_ids():
    with pytest.raises(ValueError, match="portfolio holding"):
        build_stop_loss_request_from_fields({"account_id": "a1", "order_book_id": ""})
    with pytest.raises(ValueError, match="account"):
        build_regular_order_request_from_fields({"account_id": "", "order_book_id": "123"})


def test_submit_paper_order_appends_ledger(monkeypatch, tmp_path):
    kernel = make_kernel()
    kernel.paper_session_path = tmp_path / "paper.json"
    kernel.selected_account_id = "a1"
    _, condition, preview = build_regular_order_request_from_fields(
        {
            "account_id": "a1",
            "order_book_id": "123",
            "order_type": "buy",
            "price": 10.0,
            "valid_until": date.today(),
            "volume": 5,
            "condition": "normal",
        }
    )
    order = kernel.submit_paper_order(preview, "TestStock", source="test")
    assert order["id"]
    assert kernel.paper_session["orders"][-1]["id"] == order["id"]
    events = [e.get("type") for e in kernel.paper_session.get("events", [])]
    assert "paper_order_set_from_test" in events
