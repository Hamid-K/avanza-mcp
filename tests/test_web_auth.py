import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from avanza_mcp.web.app import create_web_app
from avanza_mcp.web.runtime import WebRuntime


@pytest.fixture(autouse=True)
def isolate_runtime_files(monkeypatch, tmp_path):
    monkeypatch.setattr("avanza_mcp.config.LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("avanza_mcp.config.PAPER_SESSION_FILE", tmp_path / "paper-session.json")
    monkeypatch.setattr("avanza_mcp.config.WEB_SESSION_FILE", tmp_path / "web-session.json")
    monkeypatch.setenv("AVANZA_UPDATE_CHECK_ENABLED", "0")


@pytest.fixture
def runtime(monkeypatch):
    rt = WebRuntime(port=8787)
    # keep background loops out of unit tests
    monkeypatch.setattr(rt, "start_background_loops", lambda: None)
    yield rt
    rt.kernel.shutdown_event.set()


@pytest.fixture
def client(runtime):
    app = create_web_app(runtime)
    with TestClient(app, base_url="http://127.0.0.1:8787") as test_client:
        yield test_client


def login(client, runtime):
    response = client.post("/api/auth/login", json={"token": runtime.auth.login_token})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_login_with_bad_token_rejected(client, runtime, monkeypatch):
    monkeypatch.setattr("avanza_mcp.web.auth._FAILURE_DELAY_SECONDS", 0.0)
    response = client.post("/api/auth/login", json={"token": "wrong"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_login_sets_cookie_and_returns_csrf(client, runtime):
    csrf = login(client, runtime)
    assert csrf
    me = client.get("/api/auth/me")
    assert me.json()["authenticated"] is True


def test_api_requires_cookie(client, runtime):
    response = client.get("/api/meta")
    assert response.status_code == 401


def test_mutation_requires_csrf_header(client, runtime):
    login(client, runtime)
    response = client.post("/api/auth/logout")
    assert response.status_code == 403
    assert response.json()["error"] == "csrf_required"


def test_mutation_with_csrf_header_succeeds(client, runtime):
    csrf = login(client, runtime)
    response = client.post("/api/auth/logout", headers={"X-Avanza-Web-Token": csrf})
    assert response.status_code == 200
    me = client.get("/api/auth/me")
    assert me.json()["authenticated"] is False


def test_foreign_origin_rejected(client, runtime):
    login(client, runtime)
    response = client.get("/api/meta", headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"] == "origin_rejected"


def test_meta_shape_after_login(client, runtime):
    login(client, runtime)
    payload = client.get("/api/meta").json()
    assert payload["paper_mode"] is True
    assert payload["has_session"] is False
    assert "update" in payload


def test_websocket_rejected_without_cookie(client, runtime):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass


def test_websocket_hello_after_login(client, runtime):
    # TestClient does not forward the cookie jar or real Host on WS handshakes;
    # pass them explicitly the way a browser would.
    csrf = login(client, runtime)
    headers = {"host": "127.0.0.1:8787", "cookie": f"avanza_web_auth={csrf}"}
    with client.websocket_connect("/ws", headers=headers) as ws:
        frame = ws.receive_json()
        assert frame["type"] == "hello"
