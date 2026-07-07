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
