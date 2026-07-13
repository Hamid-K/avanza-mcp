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


def test_static_responses_not_cached(client):
    response = client.get("/")
    assert response.headers["cache-control"] == "no-store"
    response = client.get("/static/app.js")
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
    assert "REFRESH_MS = 10000" in history
    assert "PUSH_DEBOUNCE_MS" in history
    assert "scheduleInterval()" in history
    assert "watch(() => store.historyRevision" in history
    assert "fromDate.value = range.from" in history
    assert "toDate.value = range.to" in history
    assert '"Trade Date"' in history
    assert '"Account"' in history
    assert '"Type"' in history
    assert '"Description"' in history
    assert '"Stock"' in history
    assert '"Amount"' in history
    assert '"P/L SEK"' in history
    assert "pl_sek" in history
    assert "signClass" in history
    assert "ALL_TRANSACTION_TYPES" in history
    assert 'params.set("types", ALL_TRANSACTION_TYPES)' in history


def test_tradingview_overlay_displays_fallback_notice_and_rows():
    tv_overlay = (STATIC_DIR / "components" / "TvListsOverlay.js").read_text()
    css = (STATIC_DIR / "styles" / "components.css").read_text()

    assert "notice" in tv_overlay
    assert "payload.warning" in tv_overlay
    assert "payload.warnings" in tv_overlay
    assert "selected_list" in tv_overlay
    assert "knownSelected" in tv_overlay
    assert 'key: "zacks_rank", label: "Zacks"' in tv_overlay
    assert "#cell-zacks_rank" in tv_overlay
    assert "row.zacks_note" in tv_overlay
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
    assert "#cell-zacks_rank" in overlay
    assert "row.zacks_error" in overlay
    assert "#cell-reason" in overlay
    assert "CORE_SOURCE_FILTERS" in overlay
    assert '"TradingView heatmap"' in overlay
    assert '"TradingView technicals"' in overlay
    assert '"Zacks"' in overlay
    assert "const filteredRows = computed" in overlay
    assert "rowSources(row).some" in overlay
    assert '@click="toggleSource(filter.label)"' in overlay
    assert ':aria-pressed="isSourceEnabled(filter.label)"' in overlay
    assert ':rows="filteredRows"' in overlay
    assert ".source-filter.active" in css
    assert ".source-filter-count" in css
    assert ".research-summary" in css
    assert ".score-pill" in css


def test_performance_chart_period_switch_uses_explicit_reload_and_cache_bust():
    chart = (STATIC_DIR / "components" / "PerformanceChart.js").read_text()

    assert "function setPeriod" in chart
    assert '@click="setPeriod(value)"' in chart
    assert "loadSequence" in chart
    assert "selectedPeriod = period.value" in chart
    assert 'params.set("period", selectedPeriod)' in chart
    assert 'params.set("account_id", store.portfolio.account_id)' in chart
    assert 'params.set("_", String(Date.now()))' in chart
    assert "await nextTick()" in chart
    assert "host.value.replaceChildren()" in chart


def test_stoplosses_overlay_is_wired_to_toolbar_and_actions():
    topbar = (STATIC_DIR / "components" / "TopBar.js").read_text()
    app_shell = (STATIC_DIR / "components" / "AppShell.js").read_text()
    overlay = (STATIC_DIR / "components" / "StopLossesOverlay.js").read_text()

    assert "Stop-Losses" in topbar
    assert "open-overlay', 'stoplosses'" in topbar
    assert "StopLossesOverlay" in app_shell
    assert "overlay === 'stoplosses'" in app_shell
    assert '@cancel="onCancel"' in app_shell
    assert '@edit="onEditStopLoss"' in app_shell
    assert "Configured Stop-Losses" in overlay
    assert "hydrateStoplosses" in overlay
    assert "store.stoplosses" in overlay
    assert "store.paperStoplosses" in overlay
    assert "emit('edit', row)" in overlay
    assert "kind: row.mode === 'Paper' ? 'paper' : 'stoploss'" in overlay


def test_session_login_remembers_onepassword_profiles_without_passwords():
    modal = (STATIC_DIR / "components" / "SessionLoginModal.js").read_text()
    css = (STATIC_DIR / "styles" / "components.css").read_text()

    assert "PROFILE_STORAGE_KEY" in modal
    assert "avanza.web.onePasswordProfiles.v1" in modal
    assert "function normalizeProfiles" in modal
    assert "seenIds.has(id)" in modal
    assert "savedProfiles.value = normalizeProfiles(savedProfiles.value)" in modal
    assert "localStorage.setItem(PROFILE_STORAGE_KEY" in modal
    assert "Saved 1Password profile" in modal
    assert "Sign in saved" in modal
    assert "forgetSelectedProfile" in modal
    assert "rememberCurrentProfile" in modal
    assert "selectedMatchesCurrentProfile() ? selectedProfile().id : newProfileId()" in modal
    assert "const profile = selectedProfile();" in modal
    assert "body.op_item = profile ? profile.op_item : opItem.value" in modal
    assert '@input="detachSelectedProfile"' in modal
    assert "Only the 1Password item name, vault, and display label are stored locally." in modal
    save_start = modal.index("function saveProfiles")
    save_end = modal.index("function newProfileId")
    save_block = modal[save_start:save_end]
    assert "op_item" in save_block
    assert "op_vault" in save_block
    assert "password" not in save_block.lower()
    assert "totp" not in save_block.lower()
    assert ".profile-picker" in css
    assert ".form-hint" in css


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
    assert "bumpContextRevision()" in body


def test_active_overlay_reloads_after_account_or_session_context_change():
    store = (STATIC_DIR / "store.js").read_text()
    actions = (STATIC_DIR / "actions.js").read_text()
    history = (STATIC_DIR / "components" / "HistoryOverlay.js").read_text()
    stoplosses = (STATIC_DIR / "components" / "StopLossesOverlay.js").read_text()
    tv = (STATIC_DIR / "components" / "TvListsOverlay.js").read_text()
    recommendations = (STATIC_DIR / "components" / "RecommendationsOverlay.js").read_text()

    assert "contextRevision: 0" in store
    assert "export function bumpContextRevision()" in store
    assert "bumpContextRevision" in actions
    assert "watch(() => store.contextRevision" in history
    assert "if (props.open) load()" in history
    assert "watch(() => store.contextRevision" in stoplosses
    assert "if (props.open) hydrateStoplosses()" in stoplosses
    assert "watch(() => store.contextRevision" in tv
    assert "watch(() => store.contextRevision" in recommendations


def test_websocket_order_frames_trigger_history_overlay_refresh_revision():
    store = (STATIC_DIR / "store.js").read_text()

    assert "historyRevision: 0" in store
    assert "export function bumpHistoryRevision()" in store
    for frame_type in ('case "portfolio":', 'case "orders":', 'case "stoplosses":'):
        start = store.index(frame_type)
        end = store.index("break;", start)
        block = store[start:end]
        assert "bumpHistoryRevision()" in block
