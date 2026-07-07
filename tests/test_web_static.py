import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from avanza_mcp.web.app import STATIC_DIR, create_web_app
from avanza_mcp.web.runtime import WebRuntime

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate_runtime_files(monkeypatch, tmp_path):
    monkeypatch.setattr("avanza_mcp.config.LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("avanza_mcp.config.PAPER_SESSION_FILE", tmp_path / "paper-session.json")
    monkeypatch.setenv("AVANZA_UPDATE_CHECK_ENABLED", "0")


@pytest.fixture
def client(monkeypatch):
    runtime = WebRuntime(port=8787)
    monkeypatch.setattr(runtime, "start_background_loops", lambda: None)
    app = create_web_app(runtime)
    with TestClient(app, base_url="http://127.0.0.1:8787") as test_client:
        yield test_client
    runtime.kernel.shutdown_event.set()


def test_index_served_with_csp(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Avanza-MCP" in response.text
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self' https://cdn.jsdelivr.net" in csp
    assert "unsafe-inline" not in csp
    assert response.headers["x-frame-options"] == "DENY"


def test_cdn_pins_have_sri():
    html = (STATIC_DIR / "index.html").read_text()
    for url in re.findall(r'src="(https://[^"]+)"', html):
        assert "cdn.jsdelivr.net" in url
    script_tags = re.findall(r"<script[^>]+src=\"https://[^>]+>", html)
    for tag in script_tags:
        assert 'integrity="sha384-' in tag, tag
        assert 'crossorigin="anonymous"' in tag, tag
    assert '"integrity"' in html  # import map integrity section


def test_referenced_static_files_exist():
    html = (STATIC_DIR / "index.html").read_text()
    for path in re.findall(r'(?:href|src)="/static/([^"]+)"', html):
        assert (STATIC_DIR / path).is_file(), path
    app_js = (STATIC_DIR / "app.js").read_text()
    for mod in re.findall(r'from "\./([^"]+)"', app_js):
        assert (STATIC_DIR / mod).is_file(), mod


def test_component_imports_resolve():
    components = STATIC_DIR / "components"
    for comp in components.glob("*.js"):
        text = comp.read_text()
        for mod in re.findall(r'from "\.\./([^"]+)"', text):
            assert (STATIC_DIR / mod).is_file(), f"{comp.name} -> {mod}"
        for mod in re.findall(r'from "\./([^"]+)"', text):
            assert (components / mod).is_file(), f"{comp.name} -> {mod}"


def test_vendor_fallbacks_committed():
    vendor = STATIC_DIR / "vendor"
    assert (vendor / "vue.esm-browser.prod.js").stat().st_size > 100_000
    assert (vendor / "lightweight-charts.standalone.production.js").stat().st_size > 100_000


def test_api_responses_not_cached(client):
    response = client.get("/api/auth/me")
    assert response.headers["cache-control"] == "no-store"
