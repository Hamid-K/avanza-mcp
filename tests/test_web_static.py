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
    # the inline import map must be allow-listed by content hash, or the page is blank
    assert "'sha256-" in csp
    import base64
    import hashlib

    html = (STATIC_DIR / "index.html").read_text()
    import_map = re.search(r'<script type="importmap">(.*?)</script>', html, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(import_map.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in csp
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


def test_dashboard_actions_are_in_toolbar_not_floating():
    app_shell = (STATIC_DIR / "components" / "AppShell.js").read_text()
    topbar = (STATIC_DIR / "components" / "TopBar.js").read_text()
    css = (STATIC_DIR / "styles" / "components.css").read_text()

    assert "fab-row" not in app_shell
    assert ".fab-row" not in css
    assert "open-overlay" in topbar
    assert "TradingView lists" in topbar
    assert "+ Stop-Loss" in topbar


def test_history_overlay_normalizes_api_transaction_rows():
    history = (STATIC_DIR / "components" / "HistoryOverlay.js").read_text()

    assert "function normalizeHistoryRow" in history
    assert "function defaultDateRange" in history
    assert "fromDate.value = range.from" in history
    assert "toDate.value = range.to" in history
    assert '"Trade Date"' in history
    assert '"Account"' in history
    assert '"Type"' in history
    assert '"Description"' in history
    assert '"Stock"' in history
    assert '"Amount"' in history
    assert "ALL_TRANSACTION_TYPES" in history
    assert 'params.set("types", ALL_TRANSACTION_TYPES)' in history


def test_tradingview_overlay_displays_fallback_notice_and_rows():
    tv_overlay = (STATIC_DIR / "components" / "TvListsOverlay.js").read_text()
    css = (STATIC_DIR / "styles" / "components.css").read_text()

    assert "notice" in tv_overlay
    assert "payload.warning" in tv_overlay
    assert "selected_list" in tv_overlay
    assert "knownSelected" in tv_overlay
    assert "No TradingView symbols available" in tv_overlay
    assert "TradingView session may be missing" not in tv_overlay
    assert ".notice" in css


def test_research_candidates_overlay_is_wired_to_toolbar_and_api():
    topbar = (STATIC_DIR / "components" / "TopBar.js").read_text()
    app_shell = (STATIC_DIR / "components" / "AppShell.js").read_text()
    overlay = (STATIC_DIR / "components" / "RecommendationsOverlay.js").read_text()
    css = (STATIC_DIR / "styles" / "components.css").read_text()

    assert "Research candidates" in topbar
    assert "open-overlay', 'recommendations'" in topbar
    assert "RecommendationsOverlay" in app_shell
    assert "overlay === 'recommendations'" in app_shell
    assert "/api/recommendations/stocks" in overlay
    assert "source-ranked" in overlay
    assert "Research input only" in overlay
    assert ".research-summary" in css
    assert ".score-pill" in css


def test_activity_log_lives_under_ongoing_orders_and_scrolls_independently():
    app_shell = (STATIC_DIR / "components" / "AppShell.js").read_text()
    activity = (STATIC_DIR / "components" / "ActivityLog.js").read_text()
    css = (STATIC_DIR / "styles" / "components.css").read_text()

    assert app_shell.index("<OpenOrdersPanel") < app_shell.index("<ActivityLog")
    assert "appHost" in activity and "mcpHost" in activity
    assert "shouldFollow" in activity
    assert "startConsoleResize" in activity
    assert ".log-scroll" in css and "overflow: auto" in css
    assert "--activity-log-width" in css


def test_paper_toggle_has_no_browser_confirm_and_live_auth_is_single_action():
    topbar = (STATIC_DIR / "components" / "TopBar.js").read_text()
    mcp_panel = (STATIC_DIR / "components" / "McpPanel.js").read_text()
    css = (STATIC_DIR / "styles" / "components.css").read_text()

    assert 'confirm("Disable paper mode?' not in topbar
    assert "armAcknowledged" not in mcp_panel
    assert "arm-live-check" not in mcp_panel
    assert "live-auth-strip" in mcp_panel
    assert 'api.post("/api/paper/mode", { enabled: false, acknowledge: true })' in mcp_panel
    assert ".live-auth-strip" in css


def test_dashboard_splitters_are_persisted():
    app_shell = (STATIC_DIR / "components" / "AppShell.js").read_text()
    css = (STATIC_DIR / "styles" / "components.css").read_text()

    for key in ("sideWidth", "portfolioHeight", "ongoingHeight"):
        assert key in app_shell
    assert "avanza.web.layout.${key}" in app_shell
    assert "startResize('side'" in app_shell
    assert "startResize('portfolio'" in app_shell
    assert "startResize('ongoing'" in app_shell
    assert ".resize-bar.vertical" in css
    assert ".resize-bar.horizontal" in css


def test_account_selection_hydrates_orders_and_stoplosses():
    actions = (STATIC_DIR / "actions.js").read_text()
    select_account = re.search(r"export async function selectAccount\(accountId\) \{(?P<body>.*?)\n\}", actions, re.S)
    assert select_account, "selectAccount action not found"
    body = select_account.group("body")
    assert "hydratePortfolio()" in body
    assert "hydrateOrders()" in body
    assert "hydrateStoplosses()" in body
