import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from avanza_mcp.web.app import create_web_app
from avanza_mcp.web.runtime import WebRuntime


class FakeAvanza:
    def __init__(self, credentials=None):
        self.credentials = credentials or {}

    def get_overview(self):
        return {
            "accounts": [
                {"id": "acc-1", "name": "Main", "accountType": "ISK", "totalValue": {"value": 1000.0, "unit": "SEK"},
                 "buyingPower": {"value": 500.0, "unit": "SEK"}, "status": "Active"},
                {"id": "acc-2", "name": "Pension", "accountType": "KF", "totalValue": {"value": 50.0, "unit": "SEK"},
                 "buyingPower": {"value": 10.0, "unit": "SEK"}, "status": "Active"},
            ]
        }

    def get_accounts_positions(self):
        return {
            "withOrderbook": [
                {
                    "account": {"id": "acc-1"},
                    "instrument": {"name": "TestStock", "orderbook": {"id": "1234"}},
                    "volume": {"value": 10},
                    "value": {"value": 100.0, "unit": "SEK"},
                    "averageAcquiredPrice": {"value": 9.0, "unit": "SEK"},
                    "lastTradingDayPerformance": {"absolute": {"value": 5.0, "unit": "SEK"}, "relative": {"value": 1.5}},
                }
            ],
            "withoutOrderbook": [],
        }

    def get_all_stop_losses(self):
        return []

    def get_orders(self):
        return []


@pytest.fixture(autouse=True)
def isolate_runtime_files(monkeypatch, tmp_path):
    monkeypatch.setattr("avanza_mcp.config.LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("avanza_mcp.config.PAPER_SESSION_FILE", tmp_path / "paper-session.json")
    monkeypatch.setattr("avanza_mcp.config.WEB_SESSION_FILE", tmp_path / "web-session.json")
    monkeypatch.setenv("AVANZA_UPDATE_CHECK_ENABLED", "0")
    monkeypatch.setenv("AVANZA_MCP_SESSION_BACKEND", "file")


@pytest.fixture
def runtime(monkeypatch):
    rt = WebRuntime(port=8787)
    monkeypatch.setattr(rt, "start_background_loops", lambda: None)
    yield rt
    rt.kernel.shutdown_event.set()


@pytest.fixture
def client(runtime):
    app = create_web_app(runtime)
    with TestClient(app, base_url="http://127.0.0.1:8787") as test_client:
        yield test_client


@pytest.fixture
def authed(client, runtime):
    response = client.post("/api/auth/login", json={"token": runtime.auth.login_token})
    csrf = response.json()["csrf_token"]
    client.headers["X-Avanza-Web-Token"] = csrf
    return client


@pytest.fixture
def with_session(authed, runtime, monkeypatch):
    monkeypatch.setattr("avanza_mcp.core.login.Avanza", FakeAvanza)
    response = authed.post("/api/sessions", json={"mode": "credentials", "username": "u", "password": "p", "totp": "123456"})
    assert response.status_code == 200, response.text
    return authed


def test_session_login_registers_tenant(with_session, runtime):
    payload = with_session.get("/api/sessions").json()
    assert len(payload["sessions"]) == 1
    session = payload["sessions"][0]
    assert session["auth_valid"] is True
    assert session["color"].startswith("#")
    assert payload["active_session_id"] == session["session_id"]
    assert runtime.kernel.selected_account_id == "acc-1"  # largest account auto-selected


def test_session_login_requires_credentials(authed, monkeypatch):
    monkeypatch.setattr("avanza_mcp.core.login.Avanza", FakeAvanza)
    response = authed.post("/api/sessions", json={"mode": "credentials", "username": "", "password": ""})
    assert response.status_code == 400


def test_portfolio_shape(with_session):
    payload = with_session.get("/api/portfolio").json()
    assert payload["account"]["id"] == "acc-1"
    assert payload["rows"][0]["Stock"] == "TestStock"
    assert payload["rows"][0]["Order Book ID"] == "1234"
    assert set(payload["metrics"].keys()) == {"day", "week", "month", "year", "since_start", "total"}
    assert "clock" in payload


def test_accounts_and_select(with_session, runtime):
    accounts = with_session.get("/api/accounts").json()
    assert [a["id"] for a in accounts["accounts"]] == ["acc-1", "acc-2"]
    response = with_session.post("/api/accounts/acc-2/select")
    assert response.status_code == 200
    assert runtime.kernel.selected_account_id == "acc-2"
    response = with_session.post("/api/accounts/nope/select")
    assert response.status_code == 404


def test_orders_and_stoplosses_empty(with_session):
    assert with_session.get("/api/orders/open").json() == {"items": []}
    payload = with_session.get("/api/stoplosses").json()
    assert payload["items"] == []
    assert payload["paper_items"] == []


def test_second_session_and_activate(with_session, runtime, monkeypatch):
    monkeypatch.setattr("avanza_mcp.core.login.Avanza", FakeAvanza)
    response = with_session.post("/api/sessions", json={"mode": "credentials", "username": "x", "password": "y", "label": "Second"})
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 2
    first_id = sessions[0]["session_id"]
    response = with_session.post(f"/api/sessions/{first_id}/activate")
    assert response.status_code == 200
    assert runtime.kernel.active_session_id == first_id


def test_logout_session_switches_or_clears(with_session, runtime):
    session_id = runtime.kernel.active_session_id
    response = with_session.request("DELETE", f"/api/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json()["sessions"] == []
    assert runtime.kernel.avanza is None


def test_search_requires_two_chars(with_session):
    assert with_session.get("/api/search?q=a").status_code == 400


def test_search_returns_portfolio_holdings(with_session):
    payload = with_session.get("/api/search?q=test").json()
    assert any(r["order_book_id"] == "1234" for r in payload["results"])


def test_ws_pushes_portfolio_on_state_change(with_session, runtime):
    csrf = with_session.headers["X-Avanza-Web-Token"]
    headers = {"host": "127.0.0.1:8787", "cookie": f"avanza_web_auth={csrf}"}
    with with_session.websocket_connect("/ws", headers=headers) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        runtime.kernel.on_state_changed("portfolio")
        frame = ws.receive_json()
        assert frame["type"] == "portfolio"
        assert frame["payload"]["rows"][0]["Stock"] == "TestStock"


def test_market_status(with_session):
    assert "clock" in with_session.get("/api/market/status").json()


# ------------------------------------------------------------------ trading


def _order_body():
    from datetime import date

    return {
        "order_book_id": "1234",
        "order_type": "buy",
        "price": 10.0,
        "volume": 5,
        "condition": "normal",
        "valid_until": date.today().isoformat(),
    }


def test_order_dry_run_returns_review(with_session):
    response = with_session.post("/api/orders/dry-run", json=_order_body())
    assert response.status_code == 200
    payload = response.json()
    assert payload["review_id"]
    assert payload["paper_mode"] is True
    assert payload["confirm_required"] is None
    assert payload["preview"]["order_type"] == "BUY"


def test_order_place_paper_flow(with_session, runtime):
    review = with_session.post("/api/orders/dry-run", json=_order_body()).json()
    response = with_session.post("/api/orders/place", json={"review_id": review["review_id"]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "paper"
    assert payload["order"]["id"]
    assert runtime.kernel.paper_session["orders"][-1]["id"] == payload["order"]["id"]


def test_order_place_rejects_stale_nonce(with_session):
    review = with_session.post("/api/orders/dry-run", json=_order_body()).json()
    first = with_session.post("/api/orders/place", json={"review_id": review["review_id"]})
    assert first.status_code == 200
    second = with_session.post("/api/orders/place", json={"review_id": review["review_id"]})
    assert second.status_code == 409  # single-use


def test_order_place_without_review_rejected(with_session):
    response = with_session.post("/api/orders/place", json={"review_id": "bogus"})
    assert response.status_code == 409


def test_live_order_requires_typed_confirm(with_session, runtime):
    runtime.kernel.paper_mode_enabled = False
    review = with_session.post("/api/orders/dry-run", json=_order_body()).json()
    assert review["confirm_required"] == "PLACE"
    response = with_session.post("/api/orders/place", json={"review_id": review["review_id"], "confirm_text": "place"})
    assert response.status_code == 403
    runtime.kernel.paper_mode_enabled = True


def test_stoploss_dry_run_and_paper_place(with_session, runtime):
    from datetime import date, timedelta

    body = {
        "order_book_id": "1234",
        "volume": 5,
        "trigger_type": "follow_upwards",
        "trigger_value": 5.0,
        "trigger_value_type": "percentage",
        "valid_until": (date.today() + timedelta(days=10)).isoformat(),
        "order_type": "sell",
        "order_price": 2.0,
        "order_price_type": "percentage",
        "order_valid_days": 1,
    }
    review = with_session.post("/api/stoplosses/dry-run", json=body)
    assert review.status_code == 200, review.text
    payload = review.json()
    assert payload["preview"]["stop_loss_order_event"]["valid_days"] == 1
    placed = with_session.post("/api/stoplosses/place", json={"review_id": payload["review_id"]})
    assert placed.status_code == 200
    assert placed.json()["mode"] == "paper"
    stoplosses = with_session.get("/api/stoplosses").json()
    assert len(stoplosses["paper_items"]) == 1


def test_paper_cancel_flow(with_session):
    review = with_session.post("/api/orders/dry-run", json=_order_body()).json()
    order = with_session.post("/api/orders/place", json={"review_id": review["review_id"]}).json()["order"]
    response = with_session.post("/api/orders/cancel", json={"kind": "paper", "id": order["id"]})
    assert response.status_code == 200
    assert response.json()["mode"] == "paper"


def test_live_cancel_requires_typed_confirm(with_session):
    response = with_session.post(
        "/api/orders/cancel", json={"kind": "order", "id": "42", "confirm_text": "nope"}
    )
    assert response.status_code == 403


def test_paper_mode_toggle(with_session, runtime):
    response = with_session.post("/api/paper/mode", json={"enabled": False})
    assert response.status_code == 200
    assert runtime.kernel.paper_mode_enabled is False
    with_session.post("/api/paper/mode", json={"enabled": True})
    assert runtime.kernel.paper_mode_enabled is True


# ---------------------------------------------------------------------- mcp


def test_mcp_status_initial(with_session):
    payload = with_session.get("/api/mcp/status").json()
    assert payload["running"] is False
    assert payload["read_write"] is False
    assert payload["live_trading"] is False
    assert payload["proxy_command"].endswith("mcp")


def test_mcp_bridge_start_stop_writes_session_file(with_session, runtime, monkeypatch, tmp_path):
    monkeypatch.setattr("avanza_mcp.config.MCP_SESSION_FILE", tmp_path / "mcp-session.json")
    response = with_session.post("/api/mcp/bridge", json={"enabled": True})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["running"] is True
    assert payload["url"].startswith("http://127.0.0.1:")
    assert payload["token"]
    assert (tmp_path / "mcp-session.json").exists()
    import json

    session_file = json.loads((tmp_path / "mcp-session.json").read_text())
    assert session_file["token"] == payload["token"]
    assert session_file["read_write"] is False

    response = with_session.post("/api/mcp/bridge", json={"enabled": False})
    assert response.json()["running"] is False
    assert not (tmp_path / "mcp-session.json").exists()


def test_mcp_read_write_toggle_revokes_live(with_session, runtime, monkeypatch, tmp_path):
    monkeypatch.setattr("avanza_mcp.config.MCP_SESSION_FILE", tmp_path / "mcp-session.json")
    with_session.post("/api/mcp/read-write", json={"enabled": True})
    response = with_session.post("/api/mcp/live-trading", json={"enabled": True, "confirm_text": "LIVE"})
    assert response.json()["live_trading"] is True
    assert runtime.kernel.live_mutations_allowed() is False  # paper mode still on
    runtime.kernel.paper_mode_enabled = False
    assert runtime.kernel.live_mutations_allowed() is True
    runtime.kernel.paper_mode_enabled = True

    response = with_session.post("/api/mcp/read-write", json={"enabled": False})
    payload = response.json()
    assert payload["read_write"] is False
    assert payload["live_trading"] is False  # auto-revoked


def test_mcp_live_trading_requires_rw_and_confirm(with_session):
    response = with_session.post("/api/mcp/live-trading", json={"enabled": True, "confirm_text": "LIVE"})
    assert response.status_code == 409  # R/W off
    with_session.post("/api/mcp/read-write", json={"enabled": True})
    response = with_session.post("/api/mcp/live-trading", json={"enabled": True, "confirm_text": "live"})
    assert response.status_code == 403  # wrong confirm
    with_session.post("/api/mcp/read-write", json={"enabled": False})


def test_mcp_bridge_requires_session(authed):
    response = authed.post("/api/mcp/bridge", json={"enabled": True})
    assert response.status_code == 409


def test_mcp_log_endpoint(with_session):
    with_session.post("/api/mcp/read-write", json={"enabled": True})
    entries = with_session.get("/api/mcp/log").json()["entries"]
    assert any("read/write" in e["message"] for e in entries)
    with_session.post("/api/mcp/read-write", json={"enabled": False})


def test_mcp_bridge_end_to_end_tool_call(with_session, runtime, monkeypatch, tmp_path):
    """A real MCP client hit: web-managed bridge dispatches through the kernel."""
    import json
    from urllib.request import Request, urlopen

    monkeypatch.setattr("avanza_mcp.config.MCP_SESSION_FILE", tmp_path / "mcp-session.json")
    payload = with_session.post("/api/mcp/bridge", json={"enabled": True}).json()
    request = Request(
        payload["url"] + "/call",
        data=json.dumps({"tool": "avanza_status", "arguments": {}}).encode(),
        headers={"Authorization": f"Bearer {payload['token']}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        body = json.loads(response.read())
    assert body["ok"] is True
    assert body["tool"] == "avanza_status"
    result = body["result"]
    assert result["mcp_enabled"] is True or result.get("sessions"), result
    with_session.post("/api/mcp/bridge", json={"enabled": False})
