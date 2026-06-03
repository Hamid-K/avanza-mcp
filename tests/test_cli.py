import argparse
import asyncio
import io
import json
import subprocess
import sys
import time
import tomllib
from datetime import date, timedelta

TEST_VALID_UNTIL = (date.today() + timedelta(days=7)).isoformat()
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError

import pytest
from textual import events
from textual.geometry import Size
from textual.widgets import Button, DataTable, Input, Select, Static

from avanza.constants import OrderType, StopLossPriceType, TimePeriod
from rich.text import Text

from avanza_cli import (
    APP_VERSION,
    STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
    build_parser,
    build_stop_loss_preview,
    call_mcp_bridge,
    connect,
    enum_value,
    load_mcp_session,
    max_valid_until_date,
    onepassword_credentials,
    parse_date,
    parse_price_type,
    prompt_credentials,
    read_mcp_message,
    restore_table_row_selection,
    selected_table_row_key,
    position_state_row_with_quote,
    trade_action_badge,
    write_mcp_message,
)


@pytest.fixture(autouse=True)
def isolate_runtime_files(monkeypatch, tmp_path):
    monkeypatch.setattr("avanza_cli.LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("avanza_cli.PAPER_SESSION_FILE", tmp_path / "paper-session.json")
    monkeypatch.setenv("AVANZA_MCP_SESSION_BACKEND", "file")
    monkeypatch.setenv("AVANZA_TV_SESSION_BACKEND", "file")
    monkeypatch.setenv("AVANZA_UPDATE_CHECK_ENABLED", "0")


def test_parse_date_accepts_iso_date():
    assert parse_date(TEST_VALID_UNTIL).isoformat() == TEST_VALID_UNTIL


def test_parse_date_rejects_non_iso_date():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_date("28-05-2026")


def test_parse_date_rejects_too_far_future_date():
    far_future = (date.today() + timedelta(days=365)).isoformat()
    with pytest.raises(argparse.ArgumentTypeError, match="exceeds Avanza limit"):
        parse_date(far_future)


def test_position_state_row_with_quote_preserves_snapshot_values():
    item = {
        "instrument": {"name": "Example AB", "orderbook": {"id": "ob-1"}},
        "volume": {"value": 10, "unit": "st"},
        "value": {"value": 1000, "unit": "SEK"},
        "averageAcquiredPrice": {"value": 90, "unit": "SEK"},
        "acquiredValue": {"value": 900, "unit": "SEK"},
        "lastTradingDayPerformance": {
            "relative": {"value": 1, "unit": "%"},
            "absolute": {"value": 10, "unit": "SEK"},
        },
        "profit": {
            "absolute": {"value": 100.0, "unit": "SEK"},
            "relative": {"value": 11.11, "unit": "%"},
        },
    }
    quote = {"quote": {"last": 110, "changePercent": 2.5}}
    row = position_state_row_with_quote(item, quote, "Real-time")

    assert row[0] == "Example AB"
    assert row[3] == "1000 SEK"
    assert row[5] == "+1.00%"
    assert "11.11%" in row[7]
    assert isinstance(row[9], Text)
    assert "●" in row[9].plain


def test_enum_value_accepts_hyphenated_names():
    assert enum_value(StopLossPriceType, "percentage") is StopLossPriceType.PERCENTAGE
    assert enum_value(OrderType, "sell") is OrderType.SELL


def test_parse_price_type_accepts_percent_symbol():
    assert parse_price_type("%") == "percentage"


def test_tui_resolves_mcp_stock_marker_from_cached_data():
    from avanza_cli import AvanzaTradingTui

    app = AvanzaTradingTui()
    app.holding_labels_by_order_book["529720"] = "Advanced Micro Devices"
    assert app.mcp_stock_marker_for_call({"order_book_id": "529720"}) == "Advanced Micro Devices"

    app.latest_stoploss_items = [{"id": "sl-1", "orderbook": {"name": "Broadcom"}}]
    assert app.mcp_stock_marker_for_call({"stop_loss_id": "sl-1"}) == "Broadcom"
    assert parse_price_type("SEK") == "monetary"


def test_tui_debug_mode_writes_profile_artifacts():
    from avanza_cli import AvanzaTradingTui

    app = AvanzaTradingTui(debug=True, debug_profile_top=5)

    result = app.run_profiled("unit_test_profile", lambda: sum(range(10)))

    assert result == 45
    assert app.debug_session_log_path is not None
    assert app.debug_session_log_path.exists()

    log_lines = app.debug_session_log_path.read_text(encoding="utf-8").splitlines()
    assert any("unit_test_profile:" in line for line in log_lines)

    prof_files = sorted(app.debug_session_log_path.parent.glob("profile-unit_test_profile-*.prof"))
    assert prof_files


def test_parser_includes_portfolio_commands():
    parser = build_parser()
    args = parser.parse_args(["portfolio", "positions", "--username", "alice"])

    assert args.command == "portfolio"
    assert args.portfolio_command == "positions"
    assert args.username == "alice"

    op_args = parser.parse_args([
        "portfolio",
        "positions",
        "--onepassword-item",
        "Avanza",
        "--onepassword-vault",
        "Private",
    ])
    assert op_args.onepassword_item == "Avanza"
    assert op_args.onepassword_vault == "Private"

    mcp_args = parser.parse_args(["mcp"])
    assert mcp_args.command == "mcp"

    transactions_args = parser.parse_args(["transactions", "list"])
    assert transactions_args.command == "transactions"
    assert transactions_args.transactions_command == "list"

    tui_args = parser.parse_args(["tui", "--debug", "--debug-profile-top", "40"])
    assert tui_args.command == "tui"
    assert tui_args.debug is True
    assert tui_args.debug_profile_top == 40


def test_parser_version_flag_prints_runtime_version(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])
    out = capsys.readouterr().out.strip()
    assert APP_VERSION in out


def test_runtime_version_matches_pyproject():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert APP_VERSION == str(data["project"]["version"])


def test_version_outdated_comparison_helpers():
    from avanza_cli import is_version_outdated, normalize_version_text, version_tuple

    assert normalize_version_text("v1.2.3") == "1.2.3"
    assert version_tuple("v0.1.2") == (0, 1, 2)
    assert is_version_outdated("0.1.2", "0.1.3") is True
    assert is_version_outdated("0.2.0", "0.1.9") is False
    assert is_version_outdated("0.1.2", "v0.1.2") is False


def test_tradingview_watchlist_id_from_url():
    from avanza_cli import tradingview_watchlist_id_from_input

    assert tradingview_watchlist_id_from_input("https://www.tradingview.com/watchlists/57177174/") == "57177174"
    assert tradingview_watchlist_id_from_input("57177174") == "57177174"
    assert tradingview_watchlist_id_from_input("") == ""


def test_github_latest_version_info_uses_release_then_tags(monkeypatch):
    from avanza_cli import github_latest_version_info
    from urllib.error import HTTPError

    def fake_fetch_text_release(url, **kwargs):
        assert "releases/latest" in url
        return json.dumps({"tag_name": "v0.1.9", "html_url": "https://example/release"})

    monkeypatch.setattr("avanza_cli.external_fetch_text", fake_fetch_text_release)
    release_info = github_latest_version_info("hamid-k/avanza-mcp")
    assert release_info["version"] == "0.1.9"
    assert release_info["source"] == "release"

    def fake_fetch_text_tags(url, **kwargs):
        if "releases/latest" in url:
            raise HTTPError(url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
        assert "tags" in url
        return json.dumps([{"name": "v0.2.0", "zipball_url": "https://example/tag"}])

    monkeypatch.setattr("avanza_cli.external_fetch_text", fake_fetch_text_tags)
    tag_info = github_latest_version_info("hamid-k/avanza-mcp")
    assert tag_info["version"] == "0.2.0"
    assert tag_info["source"] == "tag"


def test_cmd_tui_reload_reexecs_current_command(monkeypatch):
    import avanza_cli

    class FakeApp:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run(self):
            return {"reload_tui": True}

    captured: dict[str, Any] = {}

    def fake_execv(executable, argv):
        captured["executable"] = executable
        captured["argv"] = list(argv)

    monkeypatch.setattr(avanza_cli, "AvanzaTradingTui", FakeApp)
    monkeypatch.setattr(avanza_cli.os, "execv", fake_execv)
    monkeypatch.setattr(avanza_cli.sys, "argv", ["avanza_cli.py", "tui", "--debug"])
    monkeypatch.setattr(avanza_cli.sys, "executable", "/usr/bin/python3")

    avanza_cli.cmd_tui(argparse.Namespace(debug=True, debug_profile_top=12))

    assert captured["executable"] == "/usr/bin/python3"
    assert captured["argv"] == ["/usr/bin/python3", "avanza_cli.py", "tui", "--debug"]


def test_cmd_tui_without_reload_does_not_reexec(monkeypatch):
    import avanza_cli

    class FakeApp:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run(self):
            return None

    called = {"execv": False}

    def fake_execv(_executable, _argv):
        called["execv"] = True

    monkeypatch.setattr(avanza_cli, "AvanzaTradingTui", FakeApp)
    monkeypatch.setattr(avanza_cli.os, "execv", fake_execv)

    avanza_cli.cmd_tui(argparse.Namespace(debug=False, debug_profile_top=25))

    assert called["execv"] is False


def test_connect_rejects_conflicting_auth_sources():
    args = argparse.Namespace(username="alice", onepassword_item="Avanza", onepassword_vault=None)
    with pytest.raises(ValueError, match="either --username or --onepassword-item"):
        connect(args)


def test_help_includes_examples_and_safety_notes(capsys):
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    main_help = capsys.readouterr().out

    assert "Common examples:" in main_help
    assert "python avanza_cli.py tui" in main_help
    assert "Mutating commands dry-run unless you pass --confirm." in main_help

    with pytest.raises(SystemExit):
        parser.parse_args(["stoploss", "set", "--help"])
    stoploss_help = capsys.readouterr().out

    assert "Trigger types:" in stoploss_help
    assert "follow-upwards" in stoploss_help
    assert "Gliding sell stop-loss dry-run:" in stoploss_help
    assert "--trigger-value-type {SEK,%}" in stoploss_help


def test_stoploss_defaults_use_max_valid_until_and_order_valid_days():
    parser = build_parser()
    args = parser.parse_args(
        [
            "stoploss",
            "set",
            "--account-id",
            "acc-1",
            "--order-book-id",
            "ob-1",
            "--trigger-type",
            "follow-upwards",
            "--trigger-value",
            "5",
            "--order-price",
            "1",
            "--volume",
            "10",
        ]
    )

    assert STOPLOSS_ORDER_VALID_DAYS_DEFAULT == 1
    assert args.valid_until == max_valid_until_date()
    assert args.order_valid_days == STOPLOSS_ORDER_VALID_DAYS_DEFAULT

    _, _, preview = build_stop_loss_preview(vars(args))
    assert preview["stop_loss_trigger"]["valid_until"] == max_valid_until_date().isoformat()
    assert preview["stop_loss_order_event"]["valid_days"] == STOPLOSS_ORDER_VALID_DAYS_DEFAULT
    assert preview["stop_loss_order_event"]["derived_expiry_if_triggered_today"]


def test_stoploss_edit_parser_uses_order_valid_days_default():
    parser = build_parser()
    args = parser.parse_args(
        [
            "stoploss",
            "edit",
            "--stop-loss-id",
            "sl-1",
            "--account-id",
            "acc-1",
            "--order-book-id",
            "ob-1",
            "--trigger-type",
            "follow-upwards",
            "--trigger-value",
            "5",
            "--order-price",
            "1",
            "--volume",
            "10",
        ]
    )

    assert args.order_valid_days == STOPLOSS_ORDER_VALID_DAYS_DEFAULT


def test_tui_mounts_headless():
    from avanza_cli import AvanzaTradingTui

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#login-screen").display is True
            assert app.query_one("#workspace").display is False
            assert app.title == f"Avanza-MCP v{APP_VERSION}"
            assert str(app.query_one("#login-title").render()) == "Avanza-MCP Trading Console"
            assert app.query_one("#onepassword-item") is not None
            assert app.query_one("#onepassword-vault") is not None
            assert isinstance(app.query_one("#onepassword-login"), Button)
            assert app.query_one("#account-row") is not None
            assert app.query_one("#metric-grid") is not None
            assert app.query_one("#clock-status") is not None
            assert app.query_one("#button-controls") is not None
            assert app.query_one("#reload-tui") is not None
            assert app.query_one("#update-status") is not None
            assert app.query_one("#toggle-controls") is not None
            assert app.query_one("#account-select") is not None
            assert app.query_one("#metric-total") is not None
            assert app.query_one("#metric-buying") is not None
            assert app.query_one("#metric-profit") is not None
            assert app.query_one("#metric-status") is not None
            assert app.query_one("#portfolio-table") is not None
            portfolio_table = app.query_one("#portfolio-table", DataTable)
            assert portfolio_table.cursor_type == "cell"
            portfolio_labels = [
                getattr(column.label, "plain", str(column.label))
                for column in portfolio_table.columns.values()
            ]
            assert portfolio_labels[:4] == ["Stock", "B", "S", "Order Book ID"]
            assert app.query_one("#active-trades-table") is not None
            assert app.query_one("#side-pane-resizer") is not None
            assert app.query_one("#order-modal").display is False
            assert app.query_one("#orders-overlay").display is False
            assert app.query_one("#transactions-overlay").display is False
            assert app.query_one("#tv-lists-overlay").display is False
            assert app.query_one("#open-tv-lists-overlay") is not None
            assert app.query_one("#tv-lists-select") is not None
            assert isinstance(app.query_one("#paper-mode-toggle"), Button)
            assert app.query_one("#paper-mode-label").renderable == "Paper"
            assert isinstance(app.query_one("#mcp-toggle"), Button)
            assert app.query_one("#mcp-label").renderable == "MCP"
            assert isinstance(app.query_one("#mcp-write-toggle"), Button)
            assert app.query_one("#mcp-write-label").renderable == "Live R/W"
            assert app.query_one("#mcp-log") is not None
            assert app.query_one("#order-ticket-resizer") is not None
            assert app.query_one("#stoploss-ticket-resizer") is not None
            assert app.query_one("#activity-resizer") is not None
            assert app.query_one("#order-valid-days", Input).value == str(STOPLOSS_ORDER_VALID_DAYS_DEFAULT)
            resizer = app.query_one("#pane-resizer")
            assert resizer.renderable == "─"
            assert app.query_one("#stoploss-table") is not None
            assert app.query_one("#stoploss-table", DataTable).cursor_type == "cell"
            assert app.query_one("#active-trades-table", DataTable).cursor_type == "cell"
            assert app.query_one("#stoploss-modal").display is False
            expected_valid_until = max_valid_until_date().isoformat()
            assert app.query_one("#valid-until", Input).value == expected_valid_until
            assert app.query_one("#regular-order-valid-until", Input).value == expected_valid_until
            app.apply_pane_weights(3, 2)
            assert app.positions_pane_weight == 3
            assert app.activity_pane_weight == 2

            class FakeMouse:
                def __init__(self, screen_y=0, screen_x=0):
                    self.screen_y = screen_y
                    self.screen_x = screen_x
                    self.y = screen_y
                    self.x = screen_x

                def stop(self):
                    pass

            resizer.on_mouse_down(FakeMouse(10))
            resizer.on_mouse_move(FakeMouse(12))
            assert app.positions_pane_weight == 3.2
            assert app.activity_pane_weight == 1.8
            resizer.on_mouse_up(FakeMouse(12))
            assert app.is_resizing_panes is False

            activity_resizer = app.query_one("#activity-resizer")
            activity_resizer.on_mouse_down(FakeMouse(20))
            activity_resizer.on_mouse_move(FakeMouse(22))
            assert app.activity_table_weight == 3.2
            assert app.activity_logs_weight == 1
            activity_resizer.on_mouse_up(FakeMouse(22))
            assert app.is_resizing_activity is False

            side_resizer = app.query_one("#side-pane-resizer")
            side_resizer.on_mouse_down(FakeMouse(screen_x=100))
            side_resizer.on_mouse_move(FakeMouse(screen_x=95))
            assert app.active_trades_width == 47
            side_resizer.on_mouse_up(FakeMouse(screen_x=95))
            assert app.is_resizing_side_pane is False

            ticket_resizer = app.query_one("#stoploss-ticket-resizer")
            ticket_resizer.on_mouse_down(FakeMouse(screen_x=100))
            ticket_resizer.on_mouse_move(FakeMouse(screen_x=90))
            assert app.ticket_pane_width == 74
            ticket_resizer.on_mouse_up(FakeMouse(screen_x=90))
            assert app.is_resizing_ticket_pane is False

    asyncio.run(run_app())


def test_tui_on_unmount_stops_timers(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeTimer:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    app = AvanzaTradingTui()
    search_timer = FakeTimer()
    live_timer = FakeTimer()
    clock_timer = FakeTimer()
    app.order_search_timer = search_timer
    app.live_refresh_timer = live_timer
    app.clock_timer = clock_timer
    monkeypatch.setattr(app, "stop_mcp_bridge", lambda announce=True: None)

    app.on_unmount()

    assert search_timer.stopped is True
    assert live_timer.stopped is True
    assert clock_timer.stopped is True
    assert app.order_search_timer is None
    assert app.live_refresh_timer is None
    assert app.clock_timer is None
    assert app.shutdown_event.is_set() is True


def test_tui_resize_flags_initialized():
    from avanza_cli import AvanzaTradingTui

    app = AvanzaTradingTui()
    assert app.is_resizing_side_pane is False
    assert app.is_resizing_ticket_pane is False
    assert app.resize_start_active_trades_width == app.active_trades_width
    assert app.resize_start_ticket_pane_width == app.ticket_pane_width


def test_tui_write_logs_tolerate_missing_widgets(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    app = AvanzaTradingTui()
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("missing")))

    app.write_log("hello")
    app.write_mcp_log("hello")


def test_tui_on_unmount_with_mcp_and_missing_log_widgets(monkeypatch, tmp_path):
    from avanza_cli import AvanzaTradingTui

    monkeypatch.setattr("avanza_cli.MCP_SESSION_FILE", tmp_path / "mcp-session.json")

    class FakeServer:
        def __init__(self):
            self.shutdown_called = False
            self.close_called = False

        def shutdown(self):
            self.shutdown_called = True

        def server_close(self):
            self.close_called = True

    class FakeThread:
        def is_alive(self):
            return False

    app = AvanzaTradingTui()
    server = FakeServer()
    app.mcp_server = server
    app.mcp_thread = FakeThread()
    app.mcp_token = "token"
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("missing")))

    app.on_unmount()

    assert server.shutdown_called is True
    assert server.close_called is True
    assert app.mcp_server is None
    assert app.mcp_thread is None
    assert app.mcp_token is None


def test_tui_mcp_health_restores_missing_session_file(monkeypatch, tmp_path):
    from avanza_cli import AvanzaTradingTui

    monkeypatch.setattr("avanza_cli.MCP_SESSION_FILE", tmp_path / "mcp-session.json")
    monkeypatch.setattr("avanza_cli.secrets.token_urlsafe", lambda _n: "token")

    class FakeMcpServer:
        def __init__(self, server_address, _handler, _app, _token):
            self.server_address = ("127.0.0.1", 62001)

        def serve_forever(self):
            return

        def shutdown(self):
            return

        def server_close(self):
            return

    monkeypatch.setattr("avanza_cli.AvanzaMcpHttpServer", FakeMcpServer)

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test():
            app.avanza = object()
            app.start_mcp_bridge()
            session_path = tmp_path / "mcp-session.json"
            assert session_path.exists()
            session_path.unlink()
            app.ensure_mcp_bridge_health()
            assert session_path.exists()
            app.stop_mcp_bridge()

    asyncio.run(run_app())


def test_tui_login_hides_credentials_and_shows_workspace(monkeypatch, tmp_path):
    from avanza_cli import AvanzaTradingTui

    monkeypatch.setattr("avanza_cli.MCP_SESSION_FILE", tmp_path / "mcp-session.json")
    monkeypatch.setattr("avanza_cli.secrets.token_urlsafe", lambda _n: "token")

    class FakeMcpServer:
        def __init__(self, server_address, _handler, _app, _token):
            self.server_address = ("127.0.0.1", 62002)

        def serve_forever(self):
            return

        def shutdown(self):
            return

        def server_close(self):
            return

    monkeypatch.setattr("avanza_cli.AvanzaMcpHttpServer", FakeMcpServer)

    class FakeAvanza:
        def __init__(self, credentials):
            self.credentials = credentials

        def get_overview(self):
            return {
                "accounts": [
                    {
                        "id": "acc-1",
                        "name": {"defaultName": "ISK", "userDefinedName": "Small"},
                        "type": "ISK",
                        "totalValue": {"value": 1000, "unit": "SEK"},
                        "buyingPower": {"value": 250, "unit": "SEK"},
                        "profit": {
                            "absolute": {"value": 0, "unit": "SEK"},
                            "relative": {"value": 0, "unit": "%"},
                        },
                        "performance": {
                            "ONE_DAY": {"absolute": {"value": 0, "unit": "SEK"}, "relative": {"value": 0, "unit": "%"}},
                            "ONE_WEEK": {"absolute": {"value": 0, "unit": "SEK"}, "relative": {"value": 0, "unit": "%"}},
                            "ONE_MONTH": {"absolute": {"value": 0, "unit": "SEK"}, "relative": {"value": 0, "unit": "%"}},
                            "ONE_YEAR": {"absolute": {"value": 0, "unit": "SEK"}, "relative": {"value": 0, "unit": "%"}},
                            "SINCE_START": {"absolute": {"value": 0, "unit": "SEK"}, "relative": {"value": 0, "unit": "%"}},
                        },
                        "status": "ACTIVE",
                    },
                    {
                        "id": "acc-2",
                        "name": {"defaultName": "ISK", "userDefinedName": "Trading"},
                        "type": "ISK",
                        "totalValue": {"value": 5000, "unit": "SEK"},
                        "buyingPower": {"value": 750, "unit": "SEK"},
                        "profit": {
                            "absolute": {"value": 100, "unit": "SEK"},
                            "relative": {"value": 2, "unit": "%"},
                        },
                        "performance": {
                            "ONE_DAY": {"absolute": {"value": 10, "unit": "SEK"}, "relative": {"value": 0.2, "unit": "%"}},
                            "ONE_WEEK": {"absolute": {"value": 20, "unit": "SEK"}, "relative": {"value": 0.4, "unit": "%"}},
                            "ONE_MONTH": {"absolute": {"value": 30, "unit": "SEK"}, "relative": {"value": 0.6, "unit": "%"}},
                            "ONE_YEAR": {"absolute": {"value": 40, "unit": "SEK"}, "relative": {"value": 0.8, "unit": "%"}},
                            "SINCE_START": {"absolute": {"value": 90, "unit": "SEK"}, "relative": {"value": 1.8, "unit": "%"}},
                        },
                        "status": "ACTIVE",
                    }
                ]
            }

        def get_accounts_positions(self):
            return {
                "withOrderbook": [
                    {
                        "id": "pos-1",
                        "account": {"id": "acc-2"},
                        "instrument": {
                            "name": "Example AB",
                            "orderbook": {"id": "ob-1"},
                        },
                        "volume": {"value": 25, "unit": "st"},
                        "value": {"value": 1000, "unit": "SEK"},
                        "averageAcquiredPrice": {"value": 40, "unit": "SEK"},
                        "acquiredValue": {"value": 900, "unit": "SEK"},
                        "lastTradingDayPerformance": {
                            "relative": {"value": 1, "unit": "%"},
                            "absolute": {"value": 10, "unit": "SEK"},
                        },
                        "lastTradingWeekPerformance": {
                            "relative": {"value": 2, "unit": "%"},
                            "absolute": {"value": 20, "unit": "SEK"},
                        },
                        "lastTradingMonthPerformance": {
                            "relative": {"value": 3, "unit": "%"},
                            "absolute": {"value": 30, "unit": "SEK"},
                        },
                        "lastTradingYearPerformance": {
                            "relative": {"value": 4, "unit": "%"},
                            "absolute": {"value": 40, "unit": "SEK"},
                        },
                    }
                ],
                "withoutOrderbook": [],
                "cashPositions": [],
            }

        def get_all_stop_losses(self):
            return []

        def get_orders(self):
            return []

        def search_for_stock(self, query, limit):
            return [
                {
                    "name": "NewCo AB",
                    "tickerSymbol": "NEW",
                    "id": "ob-2",
                    "isin": "SE0000000002",
                    "currency": "SEK",
                }
            ]

        def get_market_data(self, order_book_id):
            return {"quote": {"last": 41.5, "changePercent": 1.2}}

        def edit_order(self, order_id, account_id, price, valid_until, volume):
            return {
                "orderRequestStatus": "SUCCESS",
                "orderId": order_id,
                "accountId": account_id,
                "price": price,
                "validUntil": valid_until.isoformat(),
                "volume": volume,
            }

        def place_stop_loss_order(self, parent_stop_loss_id, account_id, order_book_id, stop_loss_trigger, stop_loss_order_event):
            return {
                "status": "SUCCESS",
                "stoplossOrderId": "sl-new",
                "accountId": account_id,
                "orderBookId": order_book_id,
            }

        def delete_stop_loss_order(self, account_id, stop_loss_id):
            return {"deleted": True, "account_id": account_id, "stop_loss_id": stop_loss_id}

        def get_transactions_details(
            self,
            transaction_details_types=None,
            transactions_from=None,
            transactions_to=None,
            isin=None,
            max_elements=1000,
        ):
            _ = (transaction_details_types, transactions_from, transactions_to, isin, max_elements)
            return {
                "firstTransactionDate": "2024-01-01",
                "transactions": [
                    {
                        "tradeDate": "2026-04-30",
                        "account": {"id": "acc-2", "name": "Trading"},
                        "instrumentName": "Example AB",
                        "type": "BUY",
                        "volume": {"value": 10, "unit": "st"},
                        "priceInTransactionCurrency": {"value": 100, "unit": "SEK"},
                        "amount": {"value": 1000, "unit": "SEK"},
                        "commission": {"value": 1, "unit": "SEK"},
                        "result": {"value": 5, "unit": "SEK"},
                        "isin": "SE0000000001",
                        "description": "Filled order",
                    },
                    {
                        "tradeDate": "2026-04-29",
                        "account": {"id": "acc-2", "name": "Trading"},
                        "instrumentName": "Dividend Item",
                        "type": "DIVIDEND",
                        "amount": {"value": 25, "unit": "SEK"},
                        "isin": "SE0000000001",
                        "description": "Dividend",
                    },
                ],
            }

    monkeypatch.setattr("avanza_cli.Avanza", FakeAvanza)

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            app.query_one("#username").value = "alice"
            app.query_one("#password").value = "secret-password"
            app.query_one("#totp").value = "123456"

            app.handle_login()
            await pilot.pause()

            assert app.query_one("#login-screen").display is False
            assert app.query_one("#workspace").display is True
            assert app.query_one("#password").value == ""
            assert app.query_one("#totp").value == ""
            assert app.selected_account_id == "acc-2"
            total_metric = str(app.query_one("#metric-total").render())
            profit_label = str(app.query_one("#profit-cycle", Button).label)
            profit_metric = str(app.query_one("#metric-profit-value").render())
            assert "5,000" in total_metric or "5000" in total_metric
            assert "1D P/L" in profit_label
            assert "+10.00 SEK" in profit_metric
            app.cycle_profit_metric()
            assert "1W P/L" in str(app.query_one("#profit-cycle", Button).label)
            assert "+20.00 SEK" in str(app.query_one("#metric-profit-value").render())
            app.cycle_profit_metric()
            assert "1M P/L" in str(app.query_one("#profit-cycle", Button).label)
            assert "+30.00 SEK" in str(app.query_one("#metric-profit-value").render())
            app.cycle_profit_metric()
            assert "1Y P/L" in str(app.query_one("#profit-cycle", Button).label)
            assert "+40.00 SEK" in str(app.query_one("#metric-profit-value").render())
            app.cycle_profit_metric()
            assert "Since Start P/L" in str(app.query_one("#profit-cycle", Button).label)
            assert "+90.00 SEK" in str(app.query_one("#metric-profit-value").render())
            app.cycle_profit_metric()
            assert "Total P/L" in str(app.query_one("#profit-cycle", Button).label)
            assert "+100.00 SEK" in str(app.query_one("#metric-profit-value").render())
            assert app.query_one("#account-select").value == "acc-2"
            assert app.query_one("#instrument-select").value == "ob-1"
            assert app.holding_volumes_by_order_book == {"ob-1": "25"}
            assert app.live_refresh_timer is not None
            assert app.paper_mode_enabled is True
            assert app.execute_mcp_tool("avanza_status", {})["read_write"] is False
            assert app.execute_mcp_tool("avanza_status", {})["app_version"] == APP_VERSION
            accounts = app.execute_mcp_tool("avanza_accounts", {})
            assert accounts[1]["Name"] == "Trading"
            transactions = app.execute_mcp_tool("avanza_transactions", {"account_id": "acc-2"})
            assert transactions["first_available_date"] == "2024-01-01"
            assert len(transactions["transactions"]) == 1
            assert transactions["transactions"][0]["Type"] == "BUY"
            open_orders = app.execute_mcp_tool("avanza_open_orders", {"account_id": "acc-2"})
            assert open_orders["orders"] == []
            ongoing_orders = app.execute_mcp_tool("avanza_ongoing_orders", {"account_id": "acc-2"})
            assert ongoing_orders["open_orders"] == []
            assert ongoing_orders["stoplosses"] == []
            transactions_all_types = app.execute_mcp_tool(
                "avanza_transactions",
                {"account_id": "acc-2", "types": ["BUY", "DIVIDEND"], "executed_only": False},
            )
            assert len(transactions_all_types["transactions"]) == 2
            portfolio = app.execute_mcp_tool("avanza_portfolio", {})
            assert portfolio["positions"][0]["Real-time"] == "Unknown"
            dry_run = app.execute_mcp_tool(
                "avanza_stoploss_set",
                {
                    "account_id": "acc-2",
                    "order_book_id": "ob-1",
                    "trigger_value": 5,
                    "trigger_value_type": "%",
                    "valid_until": TEST_VALID_UNTIL,
                    "order_price": 1,
                    "order_price_type": "%",
                    "volume": 10,
                },
            )
            assert dry_run["dry_run"] is True
            assert dry_run["request"]["stop_loss_order_event"]["valid_days"] == 1
            snapshot = app.execute_mcp_tool("avanza_live_snapshot", {})
            assert snapshot["poll_interval_seconds"] == 5.0
            assert snapshot["portfolio"]["positions"][0]["Stock"] == "Example AB"
            assert snapshot["realtime_quotes"][0]["order_book_id"] == "ob-1"
            quotes = app.execute_mcp_tool("avanza_realtime_quotes", {})
            assert quotes["poll_interval_seconds"] == 5.0
            assert quotes["quotes"][0]["last"] == 41.5
            paper_order = app.execute_mcp_tool(
                "avanza_paper_stoploss_set",
                {
                    "account_id": "acc-2",
                    "order_book_id": "ob-1",
                    "instrument": "Example AB",
                    "trigger_value": 5,
                    "trigger_value_type": "%",
                    "valid_until": TEST_VALID_UNTIL,
                    "order_price": 1,
                    "order_price_type": "%",
                    "volume": 10,
                },
            )
            assert paper_order["paper"] is True
            assert paper_order["order"]["status"] == "ACTIVE"
            active_table = app.query_one("#active-trades-table", DataTable)
            assert active_table.row_count == 1
            paper_orders = app.execute_mcp_tool("avanza_paper_orders", {"active_only": True})
            assert len(paper_orders["orders"]) == 1
            cancelled = app.execute_mcp_tool("avanza_paper_cancel", {"paper_order_id": paper_order["order"]["id"]})
            assert cancelled["order"]["status"] == "CANCELLED"
            app.query_one("#valid-until").value = TEST_VALID_UNTIL
            app.query_one("#trigger-value").value = "5"
            app.query_one("#order-price").value = "1"
            app.query_one("#volume").value = "10"
            app.handle_place_live()
            assert len(app.execute_mcp_tool("avanza_paper_orders", {"active_only": True})["orders"]) == 1
            app.query_one("#regular-order-valid-until").value = TEST_VALID_UNTIL
            app.query_one("#regular-order-price").value = "100"
            app.query_one("#regular-order-volume").value = "3"
            app.query_one("#order-search").value = "new"
            app.handle_order_search_from_timer()
            assert app.query_one("#order-instrument-select").value == "ob-2"
            assert "result" in str(app.query_one("#order-search-status").render())
            app.handle_order_place_live()
            paper_after_order = app.execute_mcp_tool("avanza_paper_orders", {"active_only": True})["orders"]
            assert any(order["kind"] == "Order" and order["instrument"] == "NewCo AB" for order in paper_after_order)
            paper_regular = app.execute_mcp_tool(
                "avanza_paper_order_set",
                {
                    "account_id": "acc-2",
                    "order_book_id": "ob-1",
                    "instrument": "Example AB",
                    "order_type": "buy",
                    "price": 99,
                    "valid_until": TEST_VALID_UNTIL,
                    "volume": 2,
                    "condition": "normal",
                },
            )
            assert paper_regular["order"]["kind"] == "Order"
            cancel_target = app.paper_cancel_target(paper_regular["order"])
            app.open_cancel_modal(cancel_target)
            assert app.query_one("#cancel-modal").display is True
            app.handle_cancel_review()
            app.handle_cancel_confirm()
            cancelled_regular = app.execute_mcp_tool("avanza_paper_orders", {"active_only": False})["orders"]
            assert any(
                order["id"] == paper_regular["order"]["id"] and order["status"] == "CANCELLED"
                for order in cancelled_regular
            )
            with pytest.raises(PermissionError):
                app.execute_mcp_tool(
                    "avanza_stoploss_delete",
                    {"account_id": "acc-2", "stop_loss_id": "sl-1", "confirm": True},
                )
            with pytest.raises(PermissionError):
                app.execute_mcp_tool(
                    "avanza_order_edit",
                    {
                        "account_id": "acc-2",
                        "order_id": "ord-1",
                        "price": 101,
                        "valid_until": TEST_VALID_UNTIL,
                        "volume": 2,
                        "confirm": True,
                    },
                )
            app.mcp_write_enabled = True
            app.execute_mcp_tool("avanza_live_session_authorize", {"acknowledge": True, "reason": "unit test"})
            deletion = app.execute_mcp_tool(
                "avanza_stoploss_delete",
                {"account_id": "acc-2", "stop_loss_id": "sl-1", "confirm": True},
            )
            assert deletion["dry_run"] is False
            assert deletion["result"]["deleted"] is True
            order_edit = app.execute_mcp_tool(
                "avanza_order_edit",
                {
                    "account_id": "acc-2",
                    "order_id": "ord-1",
                    "price": 101,
                    "valid_until": TEST_VALID_UNTIL,
                    "volume": 2,
                    "confirm": True,
                },
            )
            assert order_edit["dry_run"] is False
            assert order_edit["result"]["orderRequestStatus"] == "SUCCESS"
            stoploss_edit_dry = app.execute_mcp_tool(
                "avanza_stoploss_edit",
                {
                    "account_id": "acc-2",
                    "stop_loss_id": "sl-1",
                    "order_book_id": "ob-1",
                    "trigger_type": "follow-upwards",
                    "trigger_value": 5,
                    "trigger_value_type": "%",
                    "valid_until": TEST_VALID_UNTIL,
                    "order_type": "sell",
                    "order_price": 1,
                    "order_price_type": "%",
                    "volume": 10,
                },
            )
            assert stoploss_edit_dry["dry_run"] is True
            assert stoploss_edit_dry["request"]["replacement"]["stop_loss_order_event"]["valid_days"] == 1
            open_order_edit_dry = app.execute_mcp_tool(
                "avanza_open_order_edit",
                {
                    "account_id": "acc-2",
                    "order_id": "ord-1",
                    "price": 101,
                    "valid_until": TEST_VALID_UNTIL,
                    "volume": 2,
                },
            )
            assert open_order_edit_dry["dry_run"] is True
            open_order_cancel_dry = app.execute_mcp_tool(
                "avanza_open_order_cancel",
                {"account_id": "acc-2", "order_id": "ord-1"},
            )
            assert open_order_cancel_dry["dry_run"] is True
            stoploss_edit = app.execute_mcp_tool(
                "avanza_stoploss_edit",
                {
                    "account_id": "acc-2",
                    "stop_loss_id": "sl-1",
                    "order_book_id": "ob-1",
                    "trigger_type": "follow-upwards",
                    "trigger_value": 5,
                    "trigger_value_type": "%",
                    "valid_until": TEST_VALID_UNTIL,
                    "order_type": "sell",
                    "order_price": 1,
                    "order_price_type": "%",
                    "volume": 10,
                    "confirm": True,
                },
            )
            assert stoploss_edit["dry_run"] is False
            assert stoploss_edit["result"]["place"]["stoplossOrderId"] == "sl-new"
            stoploss_replace_alias = app.execute_mcp_tool(
                "avanza_stoploss_replace",
                {
                    "account_id": "acc-2",
                    "stop_loss_id": "sl-1",
                    "order_book_id": "ob-1",
                    "trigger_type": "follow-upwards",
                    "trigger_value": 5,
                    "trigger_value_type": "%",
                    "valid_until": TEST_VALID_UNTIL,
                    "order_type": "sell",
                    "order_price": 1,
                    "order_price_type": "%",
                    "volume": 10,
                },
            )
            assert stoploss_replace_alias["dry_run"] is True
            assert "deprecated" in stoploss_replace_alias.get("warning", "").lower()
            app.start_mcp_bridge()
            session = load_mcp_session(tmp_path / "mcp-session.json")
            assert session["url"] == "http://127.0.0.1:62002"
            assert session["token"] == "token"
            assert app.mcp_status_payload()["enabled"] is True
            app.stop_mcp_bridge()

    asyncio.run(run_app())


def test_tui_login_shows_progress_while_authenticating(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class SlowAvanza:
        def __init__(self, _credentials):
            time.sleep(0.15)

        def get_overview(self):
            return {
                "accounts": [
                    {
                        "id": "acc-1",
                        "name": {"defaultName": "ISK"},
                        "type": "ISK",
                        "totalValue": {"value": 100, "unit": "SEK"},
                        "buyingPower": {"value": 10, "unit": "SEK"},
                        "status": "ACTIVE",
                    }
                ]
            }

        def get_accounts_positions(self):
            return {"withOrderbook": [], "withoutOrderbook": [], "cashPositions": []}

        def get_all_stop_losses(self):
            return []

        def get_orders(self):
            return []

    monkeypatch.setattr("avanza_cli.Avanza", SlowAvanza)

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            app.query_one("#username").value = "alice"
            app.query_one("#password").value = "secret"
            app.query_one("#totp").value = "123456"
            app.handle_login()
            await pilot.pause()
            if app.login_busy:
                assert app.query_one("#login-progress").display is True
                assert app.query_one("#login", Button).disabled is True

            for _ in range(40):
                await pilot.pause(0.05)
                if app.query_one("#workspace").display is True:
                    break

            for _ in range(10):
                await pilot.pause(0.02)
                if not app.login_busy:
                    break

            assert app.query_one("#workspace").display is True
            assert app.query_one("#login-screen").display is False

    asyncio.run(run_app())


def test_mcp_stoploss_snapshots_include_stop_loss_id_and_order_book_id():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_all_stop_losses(self):
            return [
                {
                    "id": "A2^1776317962417^627705",
                    "status": "ACTIVE",
                    "account": {"id": "acc-1", "name": "Trading"},
                    "orderbook": {"id": "369636", "name": "Broadcom"},
                    "trigger": {
                        "type": "FOLLOW_UPWARDS",
                        "value": 12,
                        "valueType": "PERCENTAGE",
                        "validUntil": "2026-07-28",
                    },
                    "order": {"type": "SELL", "volume": 4, "price": 99, "priceType": "PERCENTAGE"},
                }
            ]

        def get_orders(self):
            return []

        def get_accounts_positions(self):
            return {
                "withOrderbook": [
                    {
                        "id": "pos-1",
                        "account": {"id": "acc-1"},
                        "instrument": {"name": "Broadcom", "orderbook": {"id": "369636", "quote": {"isRealTime": True}}},
                        "volume": {"value": 4, "unit": "st"},
                        "value": {"value": 800, "unit": "SEK"},
                        "averageAcquiredPrice": {"value": 180, "unit": "SEK"},
                        "acquiredValue": {"value": 720, "unit": "SEK"},
                        "lastTradingDayPerformance": {
                            "relative": {"value": 1.0, "unit": "%"},
                            "absolute": {"value": 8, "unit": "SEK"},
                        },
                    }
                ],
                "withoutOrderbook": [],
                "cashPositions": [],
            }

        def get_market_data(self, _order_book_id):
            return {"quote": {"last": 200, "changePercent": 1.0}}

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.selected_account_id = "acc-1"

    stoplosses = app.execute_mcp_tool("avanza_stoplosses", {"account_id": "acc-1"})
    assert stoplosses["stoplosses"]
    first = stoplosses["stoplosses"][0]
    assert first["Stop Loss ID"] == "A2^1776317962417^627705"
    assert first["Order Book ID"] == "369636"
    assert first["Stock"] == "Broadcom"

    ongoing = app.execute_mcp_tool("avanza_ongoing_orders", {"account_id": "acc-1"})
    assert ongoing["stoplosses"]
    ongoing_first = ongoing["stoplosses"][0]
    assert ongoing_first["Stop Loss ID"] == "A2^1776317962417^627705"
    assert ongoing_first["Order Book ID"] == "369636"

    snapshot = app.execute_mcp_tool("avanza_live_snapshot", {"account_id": "acc-1"})
    snapshot_first = snapshot["stoplosses"]["stoplosses"][0]
    assert snapshot_first["Stop Loss ID"] == "A2^1776317962417^627705"
    assert snapshot_first["Order Book ID"] == "369636"


def test_mcp_live_stoploss_rejects_foreign_order_valid_days_above_one():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_market_data(self, _order_book_id):
            return {
                "quote": {"last": 198.5, "buy": 198.4, "sell": 198.6, "currency": "USD"},
                "marketPlaceName": "NASDAQ",
                "countryCode": "US",
                "instrumentType": "stock",
                "name": "NVIDIA (NVDA)",
            }

        def search_for_stock(self, _query, _limit):
            return {
                "stocks": [
                    {
                        "id": "4478",
                        "name": "NVIDIA (NVDA)",
                        "tickerSymbol": "NVDA",
                        "marketPlaceName": "NASDAQ",
                        "countryCode": "US",
                        "currency": "USD",
                        "instrumentType": "stock",
                    }
                ]
            }

        def place_stop_loss_order(self, **_kwargs):
            raise AssertionError("Live placement must be blocked by order_valid_days safety guard.")

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.selected_account_id = "acc-1"
    app.mcp_write_enabled = True
    app.execute_mcp_tool("avanza_live_session_authorize", {"acknowledge": True, "reason": "unit test"})

    dry_run = app.execute_mcp_tool(
        "avanza_stoploss_set",
        {
            "account_id": "acc-1",
            "order_book_id": "4478",
            "trigger_value": 5,
            "order_price": 1,
            "volume": 2,
            "order_valid_days": 3,
        },
    )
    assert dry_run["dry_run"] is True
    assert any("order_valid_days=3" in warning for warning in dry_run.get("warnings", []))

    with pytest.raises(ValueError, match="order_valid_days=3"):
        app.execute_mcp_tool(
            "avanza_stoploss_set",
            {
                "account_id": "acc-1",
                "order_book_id": "4478",
                "trigger_value": 5,
                "order_price": 1,
                "volume": 2,
                "order_valid_days": 3,
                "confirm": True,
            },
        )


def test_open_order_items_infers_side_from_buy_sell_buckets():
    from avanza_cli import open_order_items, open_order_side_value

    payload = {
        "buyOrders": [
            {
                "orderId": "ord-buy-1",
                "status": "PENDING",
                "orderbook": {"id": "111", "name": "Alpha"},
                "volume": 3,
                "price": 99.5,
                "validUntil": TEST_VALID_UNTIL,
                "account": {"id": "acc-1", "name": "Main"},
            }
        ],
        "sellOrders": [
            {
                "id": "ord-sell-1",
                "status": "PENDING",
                "orderbook": {"id": "222", "name": "Beta"},
                "volume": 2,
                "price": 120.0,
                "validUntil": TEST_VALID_UNTIL,
                "account": {"id": "acc-1", "name": "Main"},
            }
        ],
    }
    items = open_order_items(payload)
    assert len(items) == 2
    sides = {str(item.get("orderId") or item.get("id")): open_order_side_value(item) for item in items}
    assert sides["ord-buy-1"] == "BUY"
    assert sides["ord-sell-1"] == "SELL"


def test_mcp_open_orders_include_ids_side_and_raw_shapes():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_orders(self):
            return {
                "buyOrders": [
                    {
                        "orderId": "ord-buy-1",
                        "status": "PENDING",
                        "orderbook": {"id": "111", "name": "Alpha"},
                        "volume": 3,
                        "price": 99.5,
                        "validUntil": TEST_VALID_UNTIL,
                        "account": {"id": "acc-1", "name": "Main"},
                    }
                ],
                "sellOrders": [
                    {
                        "id": "ord-sell-1",
                        "status": "PENDING",
                        "orderbook": {"id": "222", "name": "Beta"},
                        "volume": 2,
                        "price": 120.0,
                        "validUntil": "2026-05-29",
                        "account": {"id": "acc-1", "name": "Main"},
                    }
                ],
            }

        def get_accounts_positions(self):
            return {"withOrderbook": [], "withoutOrderbook": [], "cashPositions": []}

        def get_all_stop_losses(self):
            return []

        def get_market_data(self, _order_book_id):
            return {"quote": {"last": 100, "changePercent": 0.1}}

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.selected_account_id = "acc-1"

    open_orders = app.execute_mcp_tool("avanza_open_orders", {"account_id": "acc-1"})
    assert len(open_orders["orders"]) == 2
    first = open_orders["orders"][0]
    required = {
        "Order ID",
        "Account ID",
        "Account Name",
        "Order Book ID",
        "Stock",
        "Side",
        "Volume",
        "Price",
        "Valid Until",
        "Status",
        "order_id",
        "order_book_id",
        "account_id",
    }
    assert required.issubset(set(first.keys()))
    ids = {row["Order ID"]: row for row in open_orders["orders"]}
    assert ids["ord-buy-1"]["Side"] == "BUY"
    assert ids["ord-buy-1"]["Order Book ID"] == "111"
    assert ids["ord-buy-1"]["order_id"] == "ord-buy-1"
    assert ids["ord-sell-1"]["Side"] == "SELL"
    assert ids["ord-sell-1"]["Order Book ID"] == "222"

    raw_snapshot = app.execute_mcp_tool("avanza_open_orders_raw", {"account_id": "acc-1"})
    assert "raw" in raw_snapshot
    assert "buyOrders" in raw_snapshot["raw"]

    live = app.execute_mcp_tool("avanza_live_snapshot", {"account_id": "acc-1"})
    live_ids = {row["Order ID"]: row for row in live["open_orders"]["orders"]}
    assert live_ids["ord-buy-1"]["Side"] == "BUY"
    assert live_ids["ord-sell-1"]["Side"] == "SELL"


def test_mcp_capabilities_and_live_session_authorization():
    from avanza_cli import AvanzaTradingTui, account_rows_from_overview

    class FakeAvanza:
        def get_overview(self):
            return {
                "accounts": [
                    {
                        "id": "acc-1",
                        "name": {"defaultName": "ISK", "userDefinedName": "DayTrading"},
                        "type": "ISK",
                        "totalValue": {"value": 10000, "unit": "SEK"},
                        "buyingPower": {"value": 4000, "unit": "SEK"},
                        "status": "ACTIVE",
                    }
                ]
            }

        def get_accounts_positions(self):
            return {"withOrderbook": [], "withoutOrderbook": [], "cashPositions": []}

        def get_all_stop_losses(self):
            return []

        def get_orders(self):
            return []

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.accounts = account_rows_from_overview(app.avanza.get_overview())
    app.selected_account_id = "acc-1"
    status = app.execute_mcp_tool("avanza_capabilities", {})
    assert status["live_trading_allowed_for_this_session"] is False
    assert status["can_place_live_orders"] is False
    with pytest.raises(PermissionError):
        app.execute_mcp_tool("avanza_live_session_authorize", {"acknowledge": True})

    app.mcp_write_enabled = True
    auth = app.execute_mcp_tool(
        "avanza_live_session_authorize",
        {"acknowledge": True, "reason": "unit test authorization"},
    )
    assert auth["live_trading_allowed_for_this_session"] is True
    status_after = app.execute_mcp_tool("avanza_status", {})
    assert status_after["can_place_live_orders"] is True
    revoked = app.execute_mcp_tool("avanza_live_session_revoke", {})
    assert revoked["live_trading_allowed_for_this_session"] is False


def test_mcp_search_stock_returns_structured_results():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def search_for_stock(self, _query, _limit):
            return {
                "stocks": [
                    {
                        "title": "Broadcom",
                        "urlSlugName": "broadcom-avgo",
                        "id": "369636",
                        "marketPlaceName": "NASDAQ",
                        "currency": "USD",
                        "countryCode": "US",
                        "instrumentType": "stock",
                    }
                ]
            }

        def get_market_data(self, order_book_id):
            assert order_book_id == "369636"
            return {"quote": {"last": 421.2, "buy": 421.1, "sell": 421.4}}

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool("avanza_search_stock", {"query": "Broad", "limit": 5})
    assert result["count"] >= 1
    first = result["results"][0]
    assert first["name"] == "Broadcom"
    assert first["ticker"] == "AVGO"
    assert first["display_symbol"] == "AVGO"
    assert first["orderbook_id"] == "369636"
    assert first["bid"] == 421.1
    assert first["ask"] == 421.4


def test_mcp_orderbook_quotes_supports_arbitrary_ids():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_market_data(self, order_book_id):
            payloads = {
                "111": {
                    "name": "Volvo B",
                    "quote": {
                        "last": 310.0,
                        "buy": 309.9,
                        "sell": 310.1,
                        "changePercent": 1.2,
                        "currency": "SEK",
                    },
                },
                "222": {
                    "name": "NVIDIA",
                    "quote": {
                        "last": 905.0,
                        "buy": 904.5,
                        "sell": 905.4,
                        "changePercent": -0.4,
                        "currency": "USD",
                    },
                },
            }
            return payloads.get(order_book_id, {})

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool("avanza_orderbook_quotes", {"orderbook_ids": ["111", "222"]})
    assert result["count"] == 2
    assert result["error_count"] == 0
    rows = {row["orderbook_id"]: row for row in result["quotes"]}
    assert rows["111"]["name"] == "Volvo B"
    assert rows["111"]["spread_absolute"] == pytest.approx(0.2)
    assert "total_value_traded" in rows["222"]
    assert rows["222"]["total_value_traded"] is None


def test_mcp_market_movers_uses_avanza_endpoint_and_filters(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    calls = {"post": 0, "get": 0}

    def fake_private_post(_avanza, path, body=None):
        calls["post"] += 1
        assert path == "/_api/market-stock-filter/stocks/gainers-losers"
        assert body["filter"]["countryCodes"] == ["SE"]
        return {
            "numberOfGainers": 2,
            "numberOfLosers": 1,
            "numberOfNeutrals": 0,
            "gainers": [
                {
                    "orderBookId": "1",
                    "name": "HANZA",
                    "countryCode": "SE",
                    "currency": "SEK",
                    "lastPrice": 75.0,
                    "oneDayChangePercent": 4.2,
                    "totalValueTraded": 5_000_000,
                },
                {
                    "orderBookId": "2",
                    "name": "Illiquid",
                    "countryCode": "SE",
                    "currency": "SEK",
                    "lastPrice": 1.0,
                    "oneDayChangePercent": 12.0,
                    "totalValueTraded": 100,
                },
            ],
            "losers": [
                {
                    "orderBookId": "3",
                    "name": "Loser AB",
                    "countryCode": "SE",
                    "currency": "SEK",
                    "lastPrice": 44.0,
                    "oneDayChangePercent": -3.1,
                    "totalValueTraded": 4_000_000,
                }
            ],
        }

    def fake_private_get(_avanza, path, options=None):
        calls["get"] += 1
        _ = options
        assert path == "/_api/market-stock-filter/stocks/filter-options"
        return {"marketPlaces": ["se.xsto.large cap stockholm"]}

    monkeypatch.setattr("avanza_cli.avanza_private_post", fake_private_post)
    monkeypatch.setattr("avanza_cli.avanza_private_get", fake_private_get)

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool(
        "avanza_market_movers",
        {"countryCodes": ["SE"], "min_total_value_traded": 1_000_000, "limit": 10},
    )
    assert result["numberOfGainers"] == 2
    assert result["gainers"][0]["name"] == "HANZA"
    assert all(float(item["total_value_traded"] or 0.0) >= 1_000_000 for item in result["gainers"])
    assert result["losers"][0]["orderbook_id"] == "3"
    assert "filter_options" in result
    assert calls["post"] == 1
    assert calls["get"] == 1


def test_mcp_market_movers_supports_market_place_filter(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    captured = {}

    def fake_private_post(_avanza, path, body=None):
        captured["path"] = path
        captured["body"] = body
        return {"gainers": [], "losers": [], "numberOfGainers": 0, "numberOfLosers": 0, "numberOfNeutrals": 0}

    monkeypatch.setattr("avanza_cli.avanza_private_post", fake_private_post)
    monkeypatch.setattr("avanza_cli.avanza_private_get", lambda *_args, **_kwargs: {"marketPlaces": []})

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.execute_mcp_tool(
        "avanza_market_movers",
        {
            "countryCodes": ["SE"],
            "marketPlaces": ["se.xsto.large cap stockholm"],
            "min_price": 5,
            "min_total_value_traded": 5_000_000,
            "limit": 10,
        },
    )
    assert captured["path"] == "/_api/market-stock-filter/stocks/gainers-losers"
    assert captured["body"]["filter"]["marketPlaces"] == ["se.xsto.large cap stockholm"]


def test_mcp_index_constituents_omxs30_shape(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    fake_rows = [
        {
            "orderBookId": str(1000 + index),
            "name": f"Stock {index}",
            "countryCode": "SE",
            "changePercent": index / 10.0,
            "tickerSymbol": f"S{index}",
        }
        for index in range(1, 31)
    ]

    def fake_private_get(_avanza, path, options=None):
        _ = options
        assert path == "/_api/market-index/19002/constituents"
        return fake_rows

    monkeypatch.setattr("avanza_cli.avanza_private_get", fake_private_get)
    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool("avanza_index_constituents", {"index_id": "19002", "index_name": "OMXS30"})
    assert result["index_id"] == "19002"
    assert result["index_name"] == "OMXS30"
    assert result["constituent_count"] == 30
    assert len(result["constituents"]) == 30
    first = result["constituents"][0]
    assert first["orderbook_id"] == "1001"
    assert first["name"] == "Stock 1"
    assert first["country_code"] == "SE"
    assert first["ticker"] == "S1"


def test_mcp_index_constituents_include_quotes_and_spread(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_market_data(self, order_book_id):
            return {
                "quote": {
                    "last": 100.0 + int(order_book_id),
                    "buy": 99.5 + int(order_book_id),
                    "sell": 100.5 + int(order_book_id),
                }
            }

    def fake_private_get(_avanza, path, options=None):
        _ = options
        assert path == "/_api/market-index/19002/constituents"
        return [
            {"orderBookId": "2001", "name": "Volvo B", "countryCode": "SE", "tickerSymbol": "VOLV B"},
            {"orderBookId": "2002", "name": "NVIDIA", "countryCode": "US", "tickerSymbol": "NVDA"},
        ]

    monkeypatch.setattr("avanza_cli.avanza_private_get", fake_private_get)
    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool(
        "avanza_index_constituents",
        {"index_id": "19002", "include_quotes": True, "include_spread": True},
    )
    assert result["constituent_count"] == 2
    rows = {row["orderbook_id"]: row for row in result["constituents"]}
    assert rows["2001"]["last"] == 2101.0
    assert rows["2001"]["bid"] == 2100.5
    assert rows["2001"]["ask"] == 2101.5
    assert rows["2001"]["spread_absolute"] == pytest.approx(1.0)
    assert rows["2001"]["spread_percent"] == pytest.approx((1.0 / 2100.5) * 100.0)


def test_mcp_search_stock_normalizes_last_price_scale_to_bid_ask():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def search_for_stock(self, query, _limit):
            q = str(query).upper()
            if "VOLV" in q:
                return {"stocks": [{"name": "Volvo B", "tickerSymbol": "VOLV B", "id": "5269", "lastPrice": 31600.0}]}
            if "HANZA" in q:
                return {"stocks": [{"name": "HANZA", "tickerSymbol": "HANZA", "id": "5401", "lastPrice": 17520.0}]}
            if "NVIDIA" in q:
                return {"stocks": [{"name": "NVIDIA", "tickerSymbol": "NVDA", "id": "804998", "lastPrice": 19848.0}]}
            return {"stocks": []}

        def get_market_data(self, order_book_id):
            prices = {
                "5269": {"quote": {"buy": 316.0, "sell": 316.2, "last": 316.1}},
                "5401": {"quote": {"buy": 175.1, "sell": 175.3, "last": 175.2}},
                "804998": {"quote": {"buy": 198.4, "sell": 198.6, "last": 198.5}},
            }
            return prices[order_book_id]

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    for query in ("VOLV B", "HANZA", "NVIDIA"):
        result = app.execute_mcp_tool("avanza_search_stock", {"query": query, "limit": 5})
        first = result["results"][0]
        assert first["last_price"] is not None
        assert first["bid"] is not None
        assert first["ask"] is not None
        mid = (first["bid"] + first["ask"]) / 2.0
        assert first["last_price"] == pytest.approx(mid, rel=0.02)


def test_mcp_orderbook_quotes_enriches_metadata_from_cache():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_market_data(self, _order_book_id):
            return {"quote": {"last": 100.0, "buy": 99.9, "sell": 100.1}}

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app._cache_orderbook_metadata(
        "5269",
        {
            "name": "Volvo B",
            "ticker": "VOLV B",
            "market": "NASDAQ Stockholm",
            "currency": "SEK",
            "country_code": "SE",
            "instrument_type": "STOCK",
        },
    )
    result = app.execute_mcp_tool("avanza_orderbook_quotes", {"orderbook_ids": ["5269"], "refresh": False})
    row = result["quotes"][0]
    assert row["name"] == "Volvo B"
    assert row["ticker"] == "VOLV B"
    assert row["market"] == "NASDAQ Stockholm"
    assert row["currency"] == "SEK"
    assert row["country"] == "SE"
    assert row["instrument_type"] == "STOCK"
    assert row["display_symbol"] == "VOLV B"


def test_mcp_orderbook_quotes_enriches_known_scalp_ids():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_market_data(self, order_book_id):
            return {"quote": {"last": float(int(order_book_id) % 1000) + 1.0, "buy": 10.0, "sell": 10.2}}

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    ids = ["5269", "5247", "5401", "488235", "804998", "4478", "529720", "1211627", "1138439"]
    result = app.execute_mcp_tool("avanza_orderbook_quotes", {"orderbook_ids": ids, "refresh": False})
    rows = {row["orderbook_id"]: row for row in result["quotes"]}
    expected_currencies = {
        "5269": "SEK",
        "5247": "SEK",
        "5401": "SEK",
        "488235": "SEK",
        "804998": "SEK",
        "4478": "USD",
        "529720": "USD",
        "1211627": "USD",
        "1138439": "USD",
    }
    for orderbook_id in ids:
        row = rows[orderbook_id]
        assert row["name"]
        assert row["ticker"]
        assert row["market"]
        assert row["currency"] == expected_currencies[orderbook_id]
        assert row["country"] in {"SE", "US"}
        assert row["instrument_type"] == "STOCK"
        assert row["display_symbol"]


def test_mcp_fee_estimate_infers_currency_from_metadata_and_warns_if_unknown():
    from avanza_cli import AvanzaTradingTui

    app = AvanzaTradingTui()
    app.avanza = object()

    swedish = app.execute_mcp_tool(
        "avanza_fee_estimate",
        {
            "account_id": "acc-1",
            "orderbook_id": "5269",
            "side": "buy",
            "price": 100.0,
            "quantity": 10,
        },
    )
    assert swedish["resolved_currency"] == "SEK"

    us = app.execute_mcp_tool(
        "avanza_fee_estimate",
        {
            "account_id": "acc-1",
            "orderbook_id": "4478",
            "side": "buy",
            "price": 100.0,
            "quantity": 10,
        },
    )
    assert us["resolved_currency"] == "USD"
    assert us["estimated_fx_fee"] > 0

    for orderbook_id in ("529720", "1211627", "1138439"):
        us_row = app.execute_mcp_tool(
            "avanza_fee_estimate",
            {
                "account_id": "acc-1",
                "orderbook_id": orderbook_id,
                "side": "buy",
                "price": 100.0,
                "quantity": 10,
            },
        )
        assert us_row["resolved_currency"] == "USD"
        assert us_row["estimated_fx_fee"] > 0

    unknown = app.execute_mcp_tool(
        "avanza_fee_estimate",
        {
            "account_id": "acc-1",
            "orderbook_id": "99999999",
            "side": "buy",
            "price": 100.0,
            "quantity": 10,
        },
    )
    assert unknown["resolved_currency"] == "USD"
    assert unknown["estimated_fx_fee"] > 0
    assert any("conservative" in str(item).lower() for item in unknown.get("warnings", []))


def test_mcp_search_stock_parses_display_symbol_from_parenthesized_name():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def search_for_stock(self, query, _limit):
            q = str(query).upper()
            rows = [
                {"name": "NVIDIA (NVDA)", "id": "4478", "marketPlaceName": "NASDAQ", "countryCode": "US", "instrumentType": "stock"},
                {
                    "name": "Advanced Micro Devices (AMD)",
                    "id": "529720",
                    "marketPlaceName": "NASDAQ",
                    "countryCode": "US",
                    "instrumentType": "stock",
                },
                {
                    "name": "Coinbase Global, Inc. - Class A (COIN)",
                    "id": "1211627",
                    "marketPlaceName": "NASDAQ",
                    "countryCode": "US",
                    "instrumentType": "stock",
                },
                {
                    "name": "Palantir Technologies (PLTR)",
                    "id": "1138439",
                    "marketPlaceName": "NYSE",
                    "countryCode": "US",
                    "instrumentType": "stock",
                },
                {
                    "name": "Sivers Semiconductors (SIVE)",
                    "id": "804998",
                    "marketPlaceName": "NASDAQ Stockholm",
                    "countryCode": "SE",
                    "instrumentType": "stock",
                },
                {
                    "name": "Volvo B (VOLV B)",
                    "id": "5269",
                    "marketPlaceName": "NASDAQ Stockholm",
                    "countryCode": "SE",
                    "instrumentType": "stock",
                },
                {
                    "name": "Investor B (INVE B)",
                    "id": "5247",
                    "marketPlaceName": "NASDAQ Stockholm",
                    "countryCode": "SE",
                    "instrumentType": "stock",
                },
            ]
            return {"stocks": [row for row in rows if q.split()[0] in row["name"].upper()]}

        def get_market_data(self, order_book_id):
            return {"quote": {"buy": 100.0, "sell": 100.5, "last": 100.2}}

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    expectations = {
        "NVIDIA": "NVDA",
        "AMD": "AMD",
        "Coinbase": "COIN",
        "Palantir": "PLTR",
        "Sivers": "SIVE",
        "VOLV B": "VOLV B",
        "Investor B": "INVE B",
    }
    for query, symbol in expectations.items():
        result = app.execute_mcp_tool("avanza_search_stock", {"query": query, "limit": 5})
        assert result["results"], query
        row = result["results"][0]
        assert row["display_symbol"] == symbol
        assert row["ticker"] == symbol
        assert row["currency"] in {"SEK", "USD"}


def test_mcp_quote_metadata_flows_from_search_cache():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def search_for_stock(self, query, _limit):
            _ = query
            return {
                "stocks": [
                    {
                        "name": "NVIDIA (NVDA)",
                        "id": "4478",
                        "marketPlaceName": "NASDAQ",
                        "countryCode": "US",
                        "instrumentType": "stock",
                    }
                ]
            }

        def get_market_data(self, order_book_id):
            assert order_book_id == "4478"
            return {"quote": {"buy": 198.4, "sell": 198.6, "last": 198.5}}

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    _ = app.execute_mcp_tool("avanza_search_stock", {"query": "NVIDIA", "limit": 5})
    quotes = app.execute_mcp_tool("avanza_orderbook_quotes", {"orderbook_ids": ["4478"], "refresh": False})
    row = quotes["quotes"][0]
    assert row["name"] == "NVIDIA (NVDA)"
    assert row["display_symbol"] == "NVDA"
    assert row["market"] == "NASDAQ"
    assert row["country"] == "US"
    assert row["currency"] == "USD"


def test_mcp_select_account_switches_context():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_overview(self):
            return {
                "accounts": [
                    {"id": "7616265", "name": {"defaultName": "Main"}, "type": "KF", "status": "ACTIVE"},
                    {"id": "931965", "name": {"defaultName": "DayTrading"}, "type": "ISK", "status": "ACTIVE"},
                ]
            }

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    switched = app.execute_mcp_tool("avanza_select_account", {"account_id": "931965"})
    assert switched["selected_account_id"] == "931965"
    assert switched["selected_account_name"] == "DayTrading"
    status = app.execute_mcp_tool("avanza_status", {})
    assert status["selected_account_id"] == "931965"


def test_mcp_tools_catalog_exposes_tenant_session_scope_fields():
    from avanza_cli import MCP_TOOLS, PAPER_SESSION_ID_TOOLS, TENANT_SESSION_SCOPED_TOOLS, mcp_tools_catalog

    raw_tools = {tool["name"]: tool for tool in MCP_TOOLS}
    scoped_tools = {tool["name"]: tool for tool in mcp_tools_catalog()}

    assert set(scoped_tools) == set(raw_tools)

    for name, tool in scoped_tools.items():
        if not name.startswith("avanza_"):
            continue
        schema = tool.get("inputSchema", {})
        properties = schema.get("properties", {})
        if name in TENANT_SESSION_SCOPED_TOOLS and name != "avanza_select_session":
            assert "tenant_session_id" in properties
        if name in TENANT_SESSION_SCOPED_TOOLS and name not in PAPER_SESSION_ID_TOOLS and name != "avanza_select_session":
            assert "session_id" in properties
        if name not in TENANT_SESSION_SCOPED_TOOLS:
            assert "tenant_session_id" not in properties
            if name != "avanza_select_session":
                assert "session_id" not in properties

    # Generic market-data tools are intentionally not tenant-scoped.
    for name in ("avanza_search_stock", "avanza_orderbook_quotes", "avanza_market_movers", "avanza_index_constituents"):
        props = scoped_tools[name]["inputSchema"]["properties"]
        assert "tenant_session_id" not in props
        assert "session_id" not in props


def test_mcp_status_can_be_scoped_by_tenant_session_without_switching_active():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def __init__(self, account_id: str, account_name: str):
            self._account_id = account_id
            self._account_name = account_name

    app = AvanzaTradingTui()
    first = app.register_tenant_session(
        FakeAvanza("acc-1", "Personal"),
        {"accounts": [{"id": "acc-1", "name": {"defaultName": "Personal"}, "type": "ISK", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Personal",
    )
    second = app.register_tenant_session(
        FakeAvanza("acc-2", "Company"),
        {"accounts": [{"id": "acc-2", "name": {"defaultName": "Company"}, "type": "KF", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Company",
    )
    app.load_active_state_from_tenant(first)

    scoped_status = app.execute_mcp_tool("avanza_status", {"tenant_session_id": second.session_id})
    assert scoped_status["active_session_id"] == second.session_id
    assert scoped_status["selected_account_id"] == "acc-2"
    assert scoped_status["selected_account_name"] == "Company"

    # Status scope call should not mutate active TUI context.
    assert app.active_session_id == first.session_id
    assert app.selected_account_id == "acc-1"


def test_mcp_legacy_session_id_alias_scopes_non_paper_tools():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def __init__(self, account_id: str, account_name: str):
            self._account_id = account_id
            self._account_name = account_name

        def get_overview(self):
            return {
                "accounts": [
                    {
                        "id": self._account_id,
                        "name": {"defaultName": self._account_name},
                        "type": "ISK",
                        "status": "ACTIVE",
                    }
                ]
            }

    app = AvanzaTradingTui()
    first = app.register_tenant_session(
        FakeAvanza("acc-1", "Personal"),
        {"accounts": [{"id": "acc-1", "name": {"defaultName": "Personal"}, "type": "ISK", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Personal",
    )
    second = app.register_tenant_session(
        FakeAvanza("acc-2", "Company"),
        {"accounts": [{"id": "acc-2", "name": {"defaultName": "Company"}, "type": "KF", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Company",
    )
    app.load_active_state_from_tenant(first)

    scoped_accounts = app.execute_mcp_tool("avanza_accounts", {"session_id": second.session_id})
    assert scoped_accounts[0]["ID"] == "acc-2"
    assert scoped_accounts[0]["Name"] == "Company"

    # Legacy scope alias should not mutate active TUI context.
    assert app.active_session_id == first.session_id
    assert app.selected_account_id == "acc-1"


def test_generic_avanza_tools_ignore_tenant_session_scope_argument():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def __init__(self, label: str):
            self.label = label

        def search_for_stock(self, query, _limit):
            return {
                "stocks": [
                    {
                        "name": f"{self.label} Result ({self.label[:3].upper()})",
                        "id": "1",
                        "tickerSymbol": self.label[:3].upper(),
                        "marketPlaceName": "NASDAQ Stockholm",
                        "countryCode": "SE",
                        "instrumentType": "stock",
                    }
                ]
            }

        def get_market_data(self, _order_book_id):
            return {"quote": {"buy": 100.0, "sell": 100.2, "last": 100.1}}

    app = AvanzaTradingTui()
    first = app.register_tenant_session(
        FakeAvanza("Personal"),
        {"accounts": [{"id": "acc-1", "name": {"defaultName": "Personal"}, "type": "ISK", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Personal",
    )
    second = app.register_tenant_session(
        FakeAvanza("Company"),
        {"accounts": [{"id": "acc-2", "name": {"defaultName": "Company"}, "type": "KF", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Company",
    )
    app.load_active_state_from_tenant(first)

    result = app.execute_mcp_tool("avanza_search_stock", {"query": "x", "tenant_session_id": second.session_id})
    assert result["results"][0]["name"].startswith("Personal")
    assert app.active_session_id == first.session_id


def test_mcp_sessions_list_and_select_session_without_mounted_tui():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def __init__(self, account_id: str, account_name: str):
            self._account_id = account_id
            self._account_name = account_name

        def get_overview(self):
            return {
                "accounts": [
                    {
                        "id": self._account_id,
                        "name": {"defaultName": self._account_name},
                        "type": "ISK",
                        "status": "ACTIVE",
                    }
                ]
            }

    app = AvanzaTradingTui()
    first = app.register_tenant_session(
        FakeAvanza("acc-1", "Personal"),
        {"accounts": [{"id": "acc-1", "name": {"defaultName": "Personal"}, "type": "ISK", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Personal",
    )
    second = app.register_tenant_session(
        FakeAvanza("acc-2", "Company"),
        {"accounts": [{"id": "acc-2", "name": {"defaultName": "Company"}, "type": "KF", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Company",
    )
    app.load_active_state_from_tenant(first)

    listed = app.execute_mcp_tool("avanza_sessions", {})
    assert listed["sessions_loaded"] == 2
    assert listed["active_session_id"] == first.session_id
    assert {row["session_id"] for row in listed["sessions"]} == {first.session_id, second.session_id}
    assert all("auth_valid" in row and "auth_error" in row for row in listed["sessions"])

    switched = app.execute_mcp_tool("avanza_select_session", {"session_id": second.session_id})
    assert switched["active_session_id"] == second.session_id
    assert app.active_session_id == second.session_id


def test_tui_ignores_stale_account_select_event_after_session_switch():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    overview_one = {
        "accounts": [
            {
                "id": "5227886",
                "name": {"userDefinedName": "Previous"},
                "totalValue": {"value": 1000, "unit": "SEK"},
            }
        ]
    }
    overview_two = {
        "accounts": [
            {
                "id": "7616265",
                "name": {"userDefinedName": "Active"},
                "totalValue": {"value": 2000, "unit": "SEK"},
            }
        ]
    }
    portfolio = {"withOrderbook": [], "withoutOrderbook": []}

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test():
            previous = app.register_tenant_session(FakeAvanza(), overview_one, portfolio, [], [], label="Previous")
            active = app.register_tenant_session(FakeAvanza(), overview_two, portfolio, [], [], label="Active")
            app.load_active_state_from_tenant(active)
            app.query_one("#login-screen").display = False
            app.query_one("#workspace").display = True
            app.apply_accounts_overview({"accounts": app.accounts}, announce=False)

            logs: list[str] = []
            app.write_log = lambda message: logs.append(str(message))  # type: ignore[method-assign]
            stale_event = SimpleNamespace(select=SimpleNamespace(id="account-select"), value=previous.selected_account_id)

            app.on_select_changed(stale_event)

            assert app.selected_account_id == active.selected_account_id
            assert app.query_one("#account-select", Select).value == active.selected_account_id
            assert not any("Account switch failed" in message for message in logs)

    asyncio.run(run_app())


def test_mcp_unauthorized_marks_scoped_session_expired():
    from avanza_cli import AvanzaTradingTui

    class UnauthorizedAvanza:
        def get_overview(self):
            raise HTTPError(
                "https://www.avanza.se/_api/account-overview/overview/categorizedAccounts",
                401,
                "Unauthorized",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

    app = AvanzaTradingTui()
    context = app.register_tenant_session(
        UnauthorizedAvanza(),
        {"accounts": [{"id": "acc-1", "name": {"defaultName": "Expired"}, "type": "ISK", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Expired Session",
    )
    app.load_active_state_from_tenant(context)
    assert context.auth_valid is True

    with pytest.raises(HTTPError):
        app.execute_mcp_tool("avanza_accounts", {"tenant_session_id": context.session_id})

    assert context.auth_valid is False
    assert context.session_id in app.live_refresh_auth_blocked_sessions
    assert "unauthorized" in context.auth_error.lower()

    status = app.execute_mcp_tool("avanza_status", {})
    session_rows = {row["session_id"]: row for row in status["sessions"]}
    assert session_rows[context.session_id]["auth_valid"] is False
    assert "unauthorized" in session_rows[context.session_id]["auth_error"].lower()


def test_background_session_heartbeat_marks_inactive_session_expired(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class HealthyAvanza:
        def get_overview(self):
            return {"accounts": [{"id": "acc-1", "name": {"defaultName": "Healthy"}, "type": "ISK", "status": "ACTIVE"}]}

    class UnauthorizedAvanza:
        def get_overview(self):
            raise HTTPError(
                "https://www.avanza.se/_api/account-overview/overview/categorizedAccounts",
                401,
                "Unauthorized",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

    app = AvanzaTradingTui()
    first = app.register_tenant_session(
        HealthyAvanza(),
        {"accounts": [{"id": "acc-1", "name": {"defaultName": "Healthy"}, "type": "ISK", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Healthy",
    )
    second = app.register_tenant_session(
        UnauthorizedAvanza(),
        {"accounts": [{"id": "acc-2", "name": {"defaultName": "Expired"}, "type": "ISK", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Expired",
    )
    app.load_active_state_from_tenant(first)

    monkeypatch.setattr(app, "safe_call_from_thread", lambda callback, *args: (callback(*args), True)[1])
    app._background_session_heartbeat_worker()

    assert first.auth_valid is True
    assert second.auth_valid is False
    assert second.session_id in app.live_refresh_auth_blocked_sessions


def test_mcp_account_scoped_portfolio_uses_matching_tenant_session():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def __init__(self, account_id: str, stock_name: str, orderbook_id: str):
            self._account_id = account_id
            self._stock_name = stock_name
            self._orderbook_id = orderbook_id

        def get_accounts_positions(self):
            return {
                "withOrderbook": [
                    {
                        "id": f"pos-{self._account_id}",
                        "account": {"id": self._account_id, "name": self._account_id},
                        "instrument": {"name": self._stock_name, "orderbook": {"id": self._orderbook_id}},
                        "volume": {"value": 1, "unit": "st"},
                        "value": {"value": 100.0, "unit": "SEK"},
                        "averageAcquiredPrice": {"value": 90.0, "unit": "SEK"},
                        "acquiredValue": {"value": 90.0, "unit": "SEK"},
                        "lastTradingDayPerformance": {
                            "relative": {"value": 1.0, "unit": "%"},
                            "absolute": {"value": 1.0, "unit": "SEK"},
                        },
                        "profit": {
                            "absolute": {"value": 10.0, "unit": "SEK"},
                            "relative": {"value": 11.11, "unit": "%"},
                        },
                    }
                ],
                "withoutOrderbook": [],
            }

    app = AvanzaTradingTui()
    first = app.register_tenant_session(
        FakeAvanza("acc-1", "Stock A", "ob-a"),
        {"accounts": [{"id": "acc-1", "name": {"defaultName": "Personal"}, "type": "ISK", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Personal",
    )
    second = app.register_tenant_session(
        FakeAvanza("acc-2", "Stock B", "ob-b"),
        {"accounts": [{"id": "acc-2", "name": {"defaultName": "Company"}, "type": "KF", "status": "ACTIVE"}]},
        {"withOrderbook": [], "withoutOrderbook": []},
        [],
        [],
        label="Company",
    )
    app.load_active_state_from_tenant(first)

    snapshot = app.execute_mcp_tool("avanza_portfolio", {"account_id": "acc-2"})
    assert snapshot["account_id"] == "acc-2"
    assert snapshot["positions"][0]["Stock"] == "Stock B"

    # Scoped execution should not change the currently active session context.
    assert app.active_session_id == first.session_id
    assert app.active_tenant_session() is not None
    assert app.active_tenant_session().selected_account_id == "acc-1"
    assert second.selected_account_id == "acc-2"


def test_mcp_paper_ledger_flow_with_risk_state():
    from avanza_cli import AvanzaTradingTui

    app = AvanzaTradingTui()
    app.avanza = object()
    app.selected_account_id = "acc-1"
    create = app.execute_mcp_tool(
        "avanza_paper_order_set",
        {
            "account_id": "acc-1",
            "order_book_id": "111",
            "instrument": "NVIDIA",
            "order_type": "buy",
            "price": 100.0,
            "valid_until": TEST_VALID_UNTIL,
            "volume": 2,
            "condition": "normal",
            "session_id": "scalp-1",
            "entry_reason": "breakout",
            "fill_immediately": True,
        },
    )
    assert create["paper"] is True
    positions = app.execute_mcp_tool(
        "avanza_paper_positions",
        {"account_id": "acc-1", "session_id": "scalp-1", "active_only": True},
    )
    assert len(positions["positions"]) == 1
    position_id = positions["positions"][0]["position_id"]

    risk = app.execute_mcp_tool(
        "avanza_paper_risk_state",
        {
            "account_id": "acc-1",
            "session_id": "scalp-1",
            "max_open_trades": 1,
            "max_trade_notional_sek": 1000,
            "max_loss_per_trade_sek": 200,
            "max_session_loss_sek": 500,
            "stop_after_consecutive_losses": 2,
        },
    )
    assert risk["open_trade_count"] == 1
    assert risk["can_enter_new_trade"] is False
    assert "max_open_trades" in risk["violations"]

    closed = app.execute_mcp_tool(
        "avanza_paper_order_exit",
        {
            "account_id": "acc-1",
            "position_id": position_id,
            "exit_price": 102.0,
            "session_id": "scalp-1",
            "exit_reason": "target hit",
        },
    )
    assert closed["paper"] is True
    assert closed["trade"]["status"] == "CLOSED"
    trades = app.execute_mcp_tool("avanza_paper_trades", {"account_id": "acc-1", "session_id": "scalp-1"})
    assert len(trades["trades"]) == 1
    summary = app.execute_mcp_tool("avanza_paper_session_summary", {"account_id": "acc-1", "session_id": "scalp-1"})
    assert summary["closed_trades"] == 1


def test_tui_tracks_terminal_resize():
    from avanza_cli import AvanzaTradingTui

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            app.on_resize(events.Resize(Size(120, 40), Size(120, 40)))
            await pilot.pause()

            assert app.last_resize == (120, 40)

    asyncio.run(run_app())


def test_orders_overlay_loads_completed_buy_sell_history():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_transactions_details(self, **_kwargs):
            return {
                "transactions": [
                    {
                        "tradeDate": "2026-05-01",
                        "type": "BUY",
                        "instrumentName": "Broadcom",
                        "volume": {"value": 4, "unit": "st"},
                        "priceInTransactionCurrency": {"value": 200, "unit": "USD"},
                        "amount": {"value": 800, "unit": "USD"},
                        "result": {"value": 12, "unit": "SEK"},
                        "account": {"id": "acc-1", "name": "Trading"},
                    },
                    {
                        "tradeDate": "2026-05-01",
                        "type": "DIVIDEND",
                        "instrumentName": "Broadcom",
                        "account": {"id": "acc-1", "name": "Trading"},
                    },
                ]
            }

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            app.avanza = FakeAvanza()
            app.selected_account_id = "acc-1"
            app.open_orders_overlay()
            await pilot.pause()
            table = app.query_one("#orders-history-table", DataTable)
            assert app.query_one("#orders-overlay").display is True
            assert table.row_count == 1
            assert table.get_row_at(0)[2] == "Broadcom"

    asyncio.run(run_app())


def test_transactions_overlay_loads_account_transactions():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_transactions_details(self, **_kwargs):
            return {
                "transactions": [
                    {
                        "tradeDate": "2026-05-01",
                        "type": "DEPOSIT",
                        "description": "Deposit",
                        "amount": {"value": 100000, "unit": "SEK"},
                        "account": {"id": "acc-1", "name": "Trading"},
                    },
                    {
                        "tradeDate": "2026-05-01",
                        "type": "SELL",
                        "instrumentName": "Apple",
                        "volume": {"value": 2, "unit": "st"},
                        "priceInTransactionCurrency": {"value": 270, "unit": "USD"},
                        "amount": {"value": 540, "unit": "USD"},
                        "result": {"value": 25, "unit": "SEK"},
                        "isin": "US0378331005",
                        "account": {"id": "acc-1", "name": "Trading"},
                    },
                ]
            }

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            app.avanza = FakeAvanza()
            app.selected_account_id = "acc-1"
            app.open_transactions_overlay()
            await pilot.pause()
            table = app.query_one("#transactions-history-table", DataTable)
            assert app.query_one("#transactions-overlay").display is True
            assert table.row_count == 2
            assert table.get_row_at(0)[2] in {"DEPOSIT", "SELL"}

    asyncio.run(run_app())


def test_load_mcp_session_requires_existing_session_file(tmp_path):
    with pytest.raises(RuntimeError):
        load_mcp_session(tmp_path / "missing-session.json")


def test_mcp_session_keychain_storage_mode(monkeypatch, tmp_path):
    from avanza_cli import load_mcp_session, remove_mcp_session_file, write_mcp_session_file

    session_path = tmp_path / "mcp-session.json"
    monkeypatch.setenv("AVANZA_MCP_SESSION_BACKEND", "keychain")
    monkeypatch.setattr("avanza_cli.tradingview_keychain_supported", lambda: True)

    store: dict[str, str] = {}

    def fake_set(path, token):
        store[str(path)] = token
        return True, ""

    def fake_get(path):
        return store.get(str(path), "")

    def fake_delete(path):
        return (store.pop(str(path), None) is not None), ""

    monkeypatch.setattr("avanza_cli.mcp_keychain_set_token", fake_set)
    monkeypatch.setattr("avanza_cli.mcp_keychain_get_token", fake_get)
    monkeypatch.setattr("avanza_cli.mcp_keychain_delete_token", fake_delete)

    write_mcp_session_file(
        session_path,
        {
            "url": "http://127.0.0.1:62000",
            "token": "super-secret-token",
            "read_write": False,
            "created_at": "2026-05-04T00:00:00",
            "proxy_command": "python avanza_cli.py mcp",
        },
    )
    metadata = json.loads(session_path.read_text(encoding="utf-8"))
    assert metadata["storage"] == "keychain"
    assert metadata["backend"] == "keychain"
    assert "token" not in metadata

    session = load_mcp_session(session_path)
    assert session["url"] == "http://127.0.0.1:62000"
    assert session["token"] == "super-secret-token"

    remove_mcp_session_file(session_path)
    assert not session_path.exists()
    assert store == {}


def test_call_mcp_bridge_handles_non_json_http_error(monkeypatch):
    import urllib.error

    class FakeHttpError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://127.0.0.1/call", 500, "boom", hdrs=None, fp=io.BytesIO(b"internal error"))

    def fake_urlopen(_request, timeout):
        raise FakeHttpError()

    monkeypatch.setattr("avanza_cli.urlopen", fake_urlopen)
    payload = call_mcp_bridge({"url": "http://127.0.0.1", "token": "x"}, "avanza_status", {})
    assert payload["ok"] is False
    assert payload["error"] == "internal error"


def test_mcp_stdio_lists_tools_without_tui_session_file(tmp_path):
    request_stream = io.BytesIO()
    write_mcp_message(
        request_stream,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    write_mcp_message(
        request_stream,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )

    process = subprocess.Popen(
        [
            sys.executable,
            "avanza_cli.py",
            "mcp",
            "--session-file",
            str(tmp_path / "missing-session.json"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output, error = process.communicate(request_stream.getvalue(), timeout=5)

    assert process.returncode == 0, error.decode()
    response_stream = io.BytesIO(output)
    initialize = read_mcp_message(response_stream)
    tools = read_mcp_message(response_stream)

    assert initialize["result"]["serverInfo"]["name"] == "avanza_cli"
    assert any(tool["name"] == "avanza_status" for tool in tools["result"]["tools"])
    tool_map = {tool["name"]: tool for tool in tools["result"]["tools"]}
    portfolio_properties = tool_map["avanza_portfolio"]["inputSchema"]["properties"]
    assert "tenant_session_id" in portfolio_properties
    assert "session_id" in portfolio_properties
    paper_set_properties = tool_map["avanza_paper_order_set"]["inputSchema"]["properties"]
    assert "tenant_session_id" in paper_set_properties
    assert "session_id" in paper_set_properties
    movers_properties = tool_map["avanza_market_movers"]["inputSchema"]["properties"]
    assert "tenant_session_id" not in movers_properties
    assert "session_id" not in movers_properties
    assert any(tool["name"] == "avanza_account_performance" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_live_snapshot" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_open_orders" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_open_orders_raw" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_ongoing_orders" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_paper_stoploss_set" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_paper_order_set" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_order_set" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_open_order_edit" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_open_order_cancel" for tool in tools["result"]["tools"])


def test_tui_sorts_table_when_header_is_clicked():
    from avanza_cli import AvanzaTradingTui

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            table = app.query_one("#portfolio-table", DataTable)
            table.add_row(
                "Beta", trade_action_badge("buy"), trade_action_badge("sell"),
                "ob-2", "1 st", Text("2,000.00 SEK"), "", "", "", "", "", "Yes",
            )
            table.add_row(
                "Alpha", trade_action_badge("buy"), trade_action_badge("sell"),
                "ob-1", "1 st", Text("1,000.00 SEK"), "", "", "", "", "", "No",
            )
            value_column = next(
                key for key, column in table.columns.items()
                if getattr(column.label, "plain", str(column.label)) == "Value"
            )
            label = table.columns[value_column].label

            app.on_data_table_header_selected(DataTable.HeaderSelected(table, value_column, 3, label))
            await pilot.pause()
            assert table.get_row_at(0)[0] == "Alpha"

            app.on_data_table_header_selected(DataTable.HeaderSelected(table, value_column, 3, label))
            await pilot.pause()
            assert table.get_row_at(0)[0] == "Beta"

    asyncio.run(run_app())


def test_mcp_account_performance_uses_selected_account_and_period_mapping():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def __init__(self):
            self.calls = []

        def get_account_performance_chart_data(self, url_parameters_ids, time_period):
            self.calls.append((url_parameters_ids, time_period))
            return {
                "timePeriod": "ALL_TIME",
                "absoluteSeries": [
                    {
                        "performance": {"value": 100.0, "unit": "SEK", "unitType": "MONETARY"},
                        "timestamp": 1711929600000,
                    },
                    {
                        "performance": {"value": 819745.35, "unit": "SEK", "unitType": "MONETARY"},
                        "timestamp": 1777586400000,
                    },
                ],
                "relativeSeries": [
                    {
                        "performance": {"value": 1.2, "unit": "percentage", "unitType": "PERCENTAGE"},
                        "timestamp": 1711929600000,
                    },
                    {
                        "performance": {"value": 224.406418, "unit": "percentage", "unitType": "PERCENTAGE"},
                        "timestamp": 1777586400000,
                    },
                ],
                "valueSeries": [
                    {
                        "performance": {"value": 900_000.0, "unit": "SEK", "unitType": "MONETARY"},
                        "timestamp": 1711929600000,
                    },
                    {
                        "performance": {"value": 1_173_126.31, "unit": "SEK", "unitType": "MONETARY"},
                        "timestamp": 1777759200000,
                    },
                ],
            }

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.selected_account_id = "acc-2"
    app.accounts = [
        {
            "id": "acc-2",
            "urlParameterId": "scrambled-acc-2",
            "name": {"defaultName": "ISK", "userDefinedName": "Trading"},
            "type": "ISK",
        }
    ]

    result = app.execute_mcp_tool("avanza_account_performance", {})
    assert app.avanza.calls[0][0] == ["scrambled-acc-2"]
    assert app.avanza.calls[0][1] == TimePeriod.ALL_TIME
    assert result["account_id"] == "acc-2"
    assert result["period"] == "SINCE_START"
    assert result["raw_period"] == "ALL_TIME"
    assert result["development_absolute"]["value"] == pytest.approx(819745.35)
    assert result["development_absolute"]["unit"] == "SEK"
    assert result["development_relative"]["value"] == pytest.approx(224.406418)
    assert result["development_relative"]["unit"] == "%"
    assert len(result["chart_points"]) == 3
    assert result["chart_points"][-1]["account_value"]["value"] == pytest.approx(1_173_126.31)
    assert result["chart_points"][0]["development_absolute"]["value"] == pytest.approx(100.0)
    assert result["chart_points"][0]["development_relative"]["value"] == pytest.approx(1.2)
    assert result["deposits"] is None
    assert result["withdrawals"] is None
    assert result["dividends"] is None


def test_mcp_account_performance_allows_explicit_account_and_period():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def __init__(self):
            self.calls = []

        def get_account_performance_chart_data(self, url_parameters_ids, time_period):
            self.calls.append((url_parameters_ids, time_period))
            return {"chartData": [[1711929600000, 1000], [1712016000000, 1100]]}

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.selected_account_id = "acc-1"
    app.accounts = [
        {
            "id": "acc-1",
            "urlParameterId": "scrambled-acc-1",
            "name": {"defaultName": "ISK", "userDefinedName": "Main"},
            "type": "ISK",
        }
    ]

    result = app.execute_mcp_tool(
        "avanza_account_performance",
        {"account_id": "acc-1", "period": "YEAR_TO_DATE"},
    )
    assert app.avanza.calls[0][0] == ["scrambled-acc-1"]
    assert app.avanza.calls[0][1] == TimePeriod.THIS_YEAR
    assert result["period"] == "YEAR_TO_DATE"
    assert result["raw_period"] == "THIS_YEAR"
    assert result["development_absolute"]["value"] == 100.0


def test_tradingview_symbol_snapshot_uses_scanner_and_recommendation_labels(monkeypatch):
    from avanza_cli import tradingview_symbol_snapshot

    def fake_fetch_json(url, **kwargs):
        assert "scanner.tradingview.com/america/scan" in url
        return {
            "totalCount": 1,
            "data": [
                {
                    "s": "NASDAQ:AAPL",
                    "d": [
                        "AAPL",
                        "Apple Inc.",
                        "NASDAQ",
                        "Electronic Technology",
                        "Telecommunications Equipment",
                        280.14,
                        3.24,
                        8.79,
                        79_000_000,
                        4_100_000_000_000,
                        "USD",
                        287.22,
                        278.37,
                        278.855,
                        2.7,
                        10.2,
                        9.7,
                        1.1,
                        2.9,
                        33.9,
                        0.60,
                        0.93,
                        0.27,
                        66.4,
                        4.3,
                        3.4,
                        74.4,
                        72.0,
                    ],
                }
            ],
        }

    monkeypatch.setattr("avanza_cli.external_fetch_json", fake_fetch_json)
    snapshot = tradingview_symbol_snapshot("AAPL", exchange="NASDAQ", market="america")

    assert snapshot["symbol"] == "NASDAQ:AAPL"
    assert snapshot["fallback_used"] is False
    assert snapshot["analytics"]["close"] == 280.14
    assert snapshot["technicals"]["overall_score"] == 0.60
    assert snapshot["technicals"]["overall_label"] == "Strong Buy"
    assert snapshot["technicals"]["moving_average_label"] == "Strong Buy"
    assert snapshot["unsafe_for_execution"] is False


def test_tradingview_symbol_full_snapshot_returns_rich_payload(monkeypatch):
    from avanza_cli import tradingview_symbol_full_snapshot

    scanner_calls: list[list[str]] = []

    def fake_fetch_json(url, **kwargs):
        payload = kwargs.get("payload", {})
        columns = list(payload.get("columns", []))
        scanner_calls.append(columns)
        if len(scanner_calls) == 1:
            return {"totalCount": 0, "data": None, "error": 'Unknown field "SMA100"'}
        values = []
        for column in columns:
            if column == "name":
                values.append("AAPL")
            elif column == "description":
                values.append("Apple Inc.")
            elif column == "exchange":
                values.append("NASDAQ")
            elif column == "close":
                values.append(280.14)
            elif column == "Recommend.All":
                values.append(0.62)
            elif column == "Recommend.MA":
                values.append(0.80)
            elif column == "Recommend.Other":
                values.append(0.10)
            else:
                values.append(None)
        return {"totalCount": 1, "data": [{"s": "NASDAQ:AAPL", "d": values}]}

    def fake_fetch_text(url, **kwargs):
        return """
        <html>
          <head>
            <title>Apple Inc. (AAPL) Stock Price | TradingView</title>
            <meta name="description" content="AAPL overview page">
            <link rel="canonical" href="https://www.tradingview.com/symbols/NASDAQ-AAPL/">
          </head>
          <body>
            <script>window.initData.symbolInfo = {"pro_symbol":"NASDAQ:AAPL","exchange":"NASDAQ","short_description":"Apple Inc.","flag":"us"};\n</script>
            <script type="application/ld+json">{"@context":"https://schema.org","@type":"FinancialProduct","name":"Apple Inc."}</script>
            <a href="/symbols/NASDAQ-MSFT/">MSFT</a>
            <a href="/symbols/NASDAQ-NVDA/">NVDA</a>
          </body>
        </html>
        """

    monkeypatch.setattr("avanza_cli.external_fetch_json", fake_fetch_json)
    monkeypatch.setattr("avanza_cli.external_fetch_text", fake_fetch_text)
    snapshot = tradingview_symbol_full_snapshot("AAPL", exchange="NASDAQ", market="america")

    assert snapshot["symbol"] == "NASDAQ:AAPL"
    assert snapshot["fallback_used"] is False
    assert snapshot["analytics"]["close"] == 280.14
    assert snapshot["technicals"]["overall_label"] == "Strong Buy"
    assert snapshot["profile"]["symbol_info"]["pro_symbol"] == "NASDAQ:AAPL"
    assert "NASDAQ:MSFT" in snapshot["related_symbols"]
    assert "SMA100" in snapshot["unsupported_fields"]
    assert snapshot["source"] == "tradingview-scanner+profile-html"
    assert len(scanner_calls) >= 2


def test_tradingview_symbol_snapshot_falls_back_to_crypto_market_for_ethusd(monkeypatch):
    from avanza_cli import tradingview_symbol_snapshot

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_fetch_json(url, **kwargs):
        payload = kwargs.get("payload", {})
        calls.append((url, payload))
        ticker = payload.get("symbols", {}).get("tickers", [""])[0]
        if "scanner.tradingview.com/america/scan" in url and ticker == "NASDAQ:ETHUSD":
            raise HTTPError(url, 400, "Bad Request", hdrs=None, fp=io.BytesIO(b'{"error":"bad request"}'))
        if "scanner.tradingview.com/crypto/scan" in url and ticker in {"CRYPTO:ETHUSD", "BITSTAMP:ETHUSD", "COINBASE:ETHUSD"}:
            return {
                "totalCount": 1,
                "data": [
                    {
                        "s": ticker,
                        "d": [
                            "Ethereum / U.S. Dollar",
                            "ETHUSD",
                            "CRYPTO",
                            "Crypto",
                            "Layer 1",
                            3123.45,
                            1.1,
                            34.0,
                            123456.0,
                            0.0,
                            "USD",
                            3200.0,
                            3000.0,
                            3050.0,
                            3.0,
                            7.0,
                            10.0,
                            22.0,
                            30.0,
                            70.0,
                            0.55,
                            0.40,
                            0.10,
                            60.0,
                            1.0,
                            1.0,
                            60.0,
                            50.0,
                        ],
                    }
                ],
            }
        return {"totalCount": 0, "data": []}

    monkeypatch.setattr("avanza_cli.external_fetch_json", fake_fetch_json)
    snapshot = tradingview_symbol_snapshot("ETHUSD", exchange="NASDAQ", market="america")

    assert snapshot["fallback_used"] is True
    assert snapshot["symbol"].endswith(":ETHUSD")
    assert snapshot["market"] == "crypto"
    assert snapshot["technicals"]["overall_label"] in {"Buy", "Strong Buy"}
    assert any("america/scan" in url for url, _ in calls)
    assert any("crypto/scan" in url for url, _ in calls)


def test_tradingview_symbol_snapshot_crypto_recovers_from_unknown_field_400(monkeypatch):
    from avanza_cli import tradingview_symbol_snapshot

    def fake_fetch_json(url, **kwargs):
        payload = kwargs.get("payload", {})
        columns = list(payload.get("columns", []))
        ticker = payload.get("symbols", {}).get("tickers", [""])[0]
        if "scanner.tradingview.com/america/scan" in url and ticker == "NASDAQ:ETHUSD":
            raise HTTPError(url, 400, "Bad Request", hdrs=None, fp=io.BytesIO(b'{"error":"bad request"}'))
        if "scanner.tradingview.com/crypto/scan" in url:
            if "industry" in columns:
                raise HTTPError(
                    url,
                    400,
                    "Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(b'{"totalCount":0,"error":"Unknown field \\"industry\\"","data":null}'),
                )
            if ticker not in {"BITSTAMP:ETHUSD", "COINBASE:ETHUSD", "KRAKEN:ETHUSD", "BINANCE:ETHUSDT"}:
                return {"totalCount": 0, "data": []}
            values = []
            for column in columns:
                if column == "name":
                    values.append("Ethereum / U.S. Dollar")
                elif column == "description":
                    values.append("ETHUSD")
                elif column == "exchange":
                    values.append("BITSTAMP")
                elif column == "close":
                    values.append(1983.43)
                elif column == "Recommend.All":
                    values.append(0.36)
                elif column == "Recommend.MA":
                    values.append(0.22)
                elif column == "Recommend.Other":
                    values.append(0.11)
                else:
                    values.append(None)
            return {"totalCount": 1, "data": [{"s": "BITSTAMP:ETHUSD", "d": values}]}
        return {"totalCount": 0, "data": []}

    monkeypatch.setattr("avanza_cli.external_fetch_json", fake_fetch_json)
    snapshot = tradingview_symbol_snapshot("ETHUSD", exchange="NASDAQ", market="america")

    assert snapshot["fallback_used"] is True
    assert snapshot["symbol"].endswith(":ETHUSD")
    assert snapshot["market"] == "crypto"
    assert "industry" in snapshot["unsupported_fields"]
    assert snapshot["analytics"]["close"] == 1983.43
    assert snapshot["technicals"]["overall_label"] in {"Buy", "Strong Buy"}


def test_tradingview_symbol_attempts_for_qualified_symbols_include_market_and_exchange_fallbacks():
    from avanza_cli import tradingview_symbol_attempts

    attempts = tradingview_symbol_attempts("LSE:BA.", exchange="NASDAQ", market="america")
    assert ("LSE:BA.", "america") in attempts
    assert ("LSE:BA.", "uk") in attempts

    us_attempts = tradingview_symbol_attempts("NASDAQ:W", exchange="NASDAQ", market="america")
    assert ("NASDAQ:W", "america") in us_attempts
    assert any(symbol == "NYSE:W" for symbol, _ in us_attempts)


def test_tradingview_symbol_snapshot_falls_back_from_america_to_exchange_market(monkeypatch):
    from avanza_cli import tradingview_symbol_snapshot

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_fetch_json(url, **kwargs):
        payload = kwargs.get("payload", {})
        calls.append((url, payload))
        ticker = payload.get("symbols", {}).get("tickers", [""])[0]
        if "scanner.tradingview.com/america/scan" in url and ticker == "LSE:BA.":
            raise HTTPError(url, 400, "Bad Request", hdrs=None, fp=io.BytesIO(b'{"error":"bad request"}'))
        if "scanner.tradingview.com/uk/scan" in url and ticker == "LSE:BA.":
            return {
                "totalCount": 1,
                "data": [
                    {
                        "s": "LSE:BA.",
                        "d": [
                            "BA.",
                            "BAE Systems plc",
                            "LSE",
                            "Electronic Technology",
                            "Aerospace & Defense",
                            16.43,
                            1.82,
                            0.29,
                            15_500_000,
                            49_300_000_000,
                            "GBP",
                            16.52,
                            16.08,
                            16.14,
                            2.9,
                            -4.3,
                            -8.4,
                            5.1,
                            49.6,
                            39.5,
                            0.18,
                            0.12,
                            0.07,
                            58.0,
                            0.5,
                            0.4,
                            61.0,
                            52.0,
                        ],
                    }
                ],
            }
        return {"totalCount": 0, "data": []}

    monkeypatch.setattr("avanza_cli.external_fetch_json", fake_fetch_json)
    snapshot = tradingview_symbol_snapshot("LSE:BA.", exchange="NASDAQ", market="america")

    assert snapshot["fallback_used"] is True
    assert snapshot["symbol"] == "LSE:BA."
    assert snapshot["market"] == "uk"
    assert snapshot["technicals"]["overall_label"] in {"Buy", "Neutral", "Strong Buy"}
    assert any("america/scan" in url for url, _ in calls)
    assert any("uk/scan" in url for url, _ in calls)


def test_tradingview_watchlist_entry_matches_target_variants():
    from avanza_cli import tradingview_watchlist_entry_matches_target

    entry = {"id": "id57177174", "name": "My Stocks", "raw_label": "My Stocks 128"}
    assert tradingview_watchlist_entry_matches_target(entry, "57177174", "")
    assert tradingview_watchlist_entry_matches_target(entry, "", "my stocks")
    assert tradingview_watchlist_entry_matches_target(entry, "https://www.tradingview.com/watchlists/57177174/", "") is False


def test_sec_recent_filings_snapshot_uses_ticker_index_and_submissions(monkeypatch):
    from avanza_cli import sec_recent_filings_snapshot

    def fake_fetch_json(url, **kwargs):
        if "company_tickers_exchange.json" in url:
            return {
                "fields": ["cik", "name", "ticker", "exchange"],
                "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"]],
            }
        if "submissions/CIK0000320193.json" in url:
            return {
                "name": "Apple Inc.",
                "filings": {
                    "recent": {
                        "form": ["10-Q", "8-K"],
                        "filingDate": ["2026-05-01", "2026-04-28"],
                        "reportDate": ["2026-03-31", "2026-04-27"],
                        "accessionNumber": ["0000320193-26-000010", "0000320193-26-000009"],
                        "primaryDocument": ["a10q.htm", "a8k.htm"],
                    }
                },
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("avanza_cli.external_fetch_json", fake_fetch_json)
    snapshot = sec_recent_filings_snapshot(ticker="AAPL", cik=None, limit=2)

    assert snapshot["cik"] == "0000320193"
    assert snapshot["ticker"] == "AAPL"
    assert len(snapshot["filings"]) == 2
    assert snapshot["filings"][0]["form"] == "10-Q"
    assert snapshot["filings"][0]["url"].endswith("/a10q.htm")


def test_fmp_analyst_recommendations_snapshot_parses_rows(monkeypatch):
    from avanza_cli import fmp_analyst_recommendations_snapshot

    def fake_fetch_text(url, **kwargs):
        assert "financialmodelingprep.com/api/v3/analyst-stock-recommendations/AAPL" in url
        assert "apikey=test-key" in url
        return json.dumps(
            [
                {
                    "symbol": "AAPL",
                    "date": "2026-05-01",
                    "strongBuy": 12,
                    "buy": 15,
                    "hold": 6,
                    "sell": 1,
                    "strongSell": 0,
                }
            ]
        )

    monkeypatch.setattr("avanza_cli.external_fetch_text", fake_fetch_text)
    snapshot = fmp_analyst_recommendations_snapshot("AAPL", api_key="test-key", limit=5)
    assert snapshot["symbol"] == "AAPL"
    assert snapshot["latest"]["strong_buy"] == 12
    assert snapshot["rows"][0]["buy"] == 15


def test_polygon_analyst_insights_snapshot_parses_rows(monkeypatch):
    from avanza_cli import polygon_analyst_insights_snapshot

    def fake_fetch_json(url, **kwargs):
        assert "api.polygon.io/benzinga/v1/analyst-insights" in url
        assert "ticker=AAPL" in url
        return {
            "status": "OK",
            "request_id": "req-1",
            "results": [
                {
                    "date": "2026-05-01",
                    "ticker": "AAPL",
                    "firm": "Example Research",
                    "rating": "Buy",
                    "rating_action": "upgrades",
                    "price_target": 320.0,
                    "insight": "Margin expansion and services growth",
                }
            ],
            "next_url": "",
        }

    monkeypatch.setattr("avanza_cli.external_fetch_json", fake_fetch_json)
    snapshot = polygon_analyst_insights_snapshot("AAPL", api_key="poly-key", limit=5)
    assert snapshot["symbol"] == "AAPL"
    assert snapshot["status"] == "OK"
    assert snapshot["rows"][0]["rating"] == "Buy"
    assert snapshot["rows"][0]["price_target"] == 320.0


def test_fred_observations_snapshot_parses_values(monkeypatch):
    from avanza_cli import fred_observations_snapshot

    def fake_fetch_json(url, **kwargs):
        assert "fred/series/observations" in url
        return {
            "title": "Federal Funds Effective Rate",
            "units": "Percent",
            "frequency": "Monthly",
            "observations": [
                {"date": "2026-03-01", "value": "4.33"},
                {"date": "2026-04-01", "value": "."},
            ],
        }

    monkeypatch.setattr("avanza_cli.external_fetch_json", fake_fetch_json)
    snapshot = fred_observations_snapshot("FEDFUNDS", api_key="test-key", limit=2, sort_order="desc")

    assert snapshot["series_id"] == "FEDFUNDS"
    assert snapshot["observations"][0]["value"] == 4.33
    assert snapshot["observations"][1]["value"] is None


def test_execute_mcp_signal_context_bundle_aggregates_sources(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    def fake_tv(symbol, **kwargs):
        return {
            "symbol": "NASDAQ:AAPL",
            "technicals": {"overall_label": "Buy"},
            "unsafe_for_execution": False,
        }

    def fake_zacks(symbol, **kwargs):
        return {"symbol": "AAPL", "rank": {"value": 2, "label": "Buy"}, "blocked": False}

    def fake_sec(*args, **kwargs):
        return {"ticker": "AAPL", "filings": [{"form": "10-Q"}], "unsafe_for_execution": False}

    monkeypatch.setattr("avanza_cli.tradingview_symbol_snapshot", fake_tv)
    monkeypatch.setattr("avanza_cli.zacks_symbol_snapshot", fake_zacks)
    monkeypatch.setattr("avanza_cli.sec_recent_filings_snapshot", fake_sec)

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool("signal_context_bundle", {"symbol": "AAPL", "include_zacks": True, "include_sec": True})

    assert result["symbol"] == "NASDAQ:AAPL"
    assert "tradingview" in result
    assert result["zacks"]["rank"]["value"] == 2
    assert result["sec"]["filings"][0]["form"] == "10-Q"
    assert result["unsafe_for_execution"] is False


def test_execute_mcp_signal_context_bundle_with_fmp_and_polygon(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    monkeypatch.setattr(
        "avanza_cli.tradingview_symbol_snapshot",
        lambda *args, **kwargs: {"symbol": "NASDAQ:AAPL", "technicals": {"overall_label": "Buy"}, "unsafe_for_execution": False},
    )
    monkeypatch.setattr(
        "avanza_cli.fmp_analyst_recommendations_snapshot",
        lambda *args, **kwargs: {"symbol": "AAPL", "rows": [{"buy": 10}], "unsafe_for_execution": False},
    )
    monkeypatch.setattr(
        "avanza_cli.polygon_analyst_insights_snapshot",
        lambda *args, **kwargs: {"symbol": "AAPL", "rows": [{"rating": "Buy"}], "unsafe_for_execution": False},
    )

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool(
        "signal_context_bundle",
        {
            "symbol": "AAPL",
            "include_fmp": True,
            "include_polygon": True,
            "include_zacks": False,
            "include_sec": False,
            "fmp_api_key": "x",
            "polygon_api_key": "y",
        },
    )

    assert "fmp" in result
    assert "polygon" in result
    assert "fmp" in result["sources"]
    assert "polygon" in result["sources"]


def test_execute_mcp_signal_context_bundle_uses_raw_symbol_for_tradingview_fallback(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    captured: dict[str, Any] = {}

    def fake_tv(symbol, **kwargs):
        captured["symbol"] = symbol
        captured["market"] = kwargs.get("market")
        captured["exchange"] = kwargs.get("exchange")
        return {
            "symbol": "CRYPTO:ETHUSD",
            "technicals": {"overall_label": "Buy"},
            "unsafe_for_execution": False,
        }

    def fake_zacks(symbol, **kwargs):
        captured["zacks_symbol"] = symbol
        return {"symbol": symbol, "blocked": False, "rank": {"value": 3, "label": "Hold"}}

    monkeypatch.setattr("avanza_cli.tradingview_symbol_snapshot", fake_tv)
    monkeypatch.setattr("avanza_cli.zacks_symbol_snapshot", fake_zacks)
    monkeypatch.setattr(
        "avanza_cli.sec_recent_filings_snapshot",
        lambda *args, **kwargs: {"ticker": "ETHUSD", "filings": [], "unsafe_for_execution": False},
    )

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool("signal_context_bundle", {"symbol": "ETHUSD", "include_zacks": True, "include_sec": True})

    assert captured["symbol"] == "ETHUSD"
    assert captured["market"] == "america"
    assert captured["exchange"] == "NASDAQ"
    assert captured["zacks_symbol"] == "ETHUSD"
    assert result["symbol"] == "CRYPTO:ETHUSD"
    assert result["unsafe_for_execution"] is False


def test_tradingview_session_lifecycle_helpers(tmp_path, monkeypatch):
    from avanza_cli import (
        clear_tradingview_session,
        load_tradingview_session,
        save_tradingview_session,
        tradingview_session_status,
    )

    session_path = tmp_path / ".avanza_tradingview_session.json"
    monkeypatch.setattr("avanza_cli.TRADINGVIEW_SESSION_FILE", session_path)
    monkeypatch.setenv("AVANZA_TV_SESSION_BACKEND", "file")

    empty_status = tradingview_session_status()
    assert empty_status["configured"] is False

    saved = save_tradingview_session("sessionid=abc123; sessionid_sign=sig987", source="test")
    assert saved["saved"] is True
    loaded = load_tradingview_session()
    assert loaded["cookie"].startswith("sessionid=abc123")

    status = tradingview_session_status()
    assert status["configured"] is True
    assert status["has_sessionid"] is True
    assert status["has_sessionid_sign"] is True
    assert status["storage"] == "file"

    assert clear_tradingview_session() is True
    assert tradingview_session_status()["configured"] is False


def test_tradingview_session_keychain_storage_mode(monkeypatch, tmp_path):
    from avanza_cli import (
        clear_tradingview_session,
        load_tradingview_session,
        save_tradingview_session,
        tradingview_session_status,
    )

    session_path = tmp_path / ".avanza_tradingview_session.json"
    monkeypatch.setattr("avanza_cli.TRADINGVIEW_SESSION_FILE", session_path)
    monkeypatch.setenv("AVANZA_TV_SESSION_BACKEND", "keychain")
    monkeypatch.setattr("avanza_cli.tradingview_keychain_supported", lambda: True)

    store: dict[str, str] = {}

    def fake_set(path, cookie):
        store[str(path)] = cookie
        return True, ""

    def fake_get(path):
        return store.get(str(path), "")

    def fake_delete(path):
        return (store.pop(str(path), None) is not None), ""

    monkeypatch.setattr("avanza_cli.tradingview_keychain_set_cookie", fake_set)
    monkeypatch.setattr("avanza_cli.tradingview_keychain_get_cookie", fake_get)
    monkeypatch.setattr("avanza_cli.tradingview_keychain_delete_cookie", fake_delete)

    saved = save_tradingview_session("sessionid=abc123; sessionid_sign=sig987", source="unit-test")
    assert saved["saved"] is True
    assert saved["storage"] == "keychain"

    metadata = json.loads(session_path.read_text(encoding="utf-8"))
    assert metadata.get("storage") == "keychain"
    assert "cookie" not in metadata

    loaded = load_tradingview_session()
    assert loaded["cookie"].startswith("sessionid=abc123")
    assert loaded["storage"] == "keychain"

    status = tradingview_session_status()
    assert status["configured"] is True
    assert status["storage"] == "keychain"

    assert clear_tradingview_session() is True
    assert tradingview_session_status()["configured"] is False


def test_mcp_tv_auth_session_start_set_status_clear(monkeypatch, tmp_path):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    monkeypatch.setattr("avanza_cli.TRADINGVIEW_SESSION_FILE", tmp_path / ".avanza_tradingview_session.json")
    monkeypatch.setattr("avanza_cli.webbrowser.open", lambda *args, **kwargs: True)

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()

    start = app.execute_mcp_tool("tv_auth_session_start", {"open_browser": True})
    assert start["browser_opened"] is True

    set_result = app.execute_mcp_tool(
        "tv_auth_session_set",
        {"sessionid": "abc", "sessionid_sign": "sig", "source": "unit-test"},
    )
    assert set_result["saved"] is True
    assert set_result["status"]["configured"] is True

    status = app.execute_mcp_tool("tv_auth_session_status", {})
    assert status["configured"] is True
    assert status["has_sessionid"] is True

    cleared = app.execute_mcp_tool("tv_auth_session_clear", {})
    assert cleared["cleared"] is True
    assert cleared["status"]["configured"] is False


def test_mcp_tv_auth_session_login_auto_delegates_and_saves(monkeypatch, tmp_path):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    called = {"thread_wrapper": False}

    def fake_worker(func, *args, **kwargs):
        called["thread_wrapper"] = True
        return {"captured": True, "status": {"configured": True}}

    monkeypatch.setattr("avanza_cli.TRADINGVIEW_SESSION_FILE", tmp_path / ".avanza_tradingview_session.json")
    monkeypatch.setattr("avanza_cli.run_blocking_in_thread", fake_worker)

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool("tv_auth_session_login_auto", {"timeout_seconds": 123})

    assert called["thread_wrapper"] is True
    assert result["captured"] is True
    assert result["status"]["configured"] is True


def test_tv_auth_symbol_analytics_uses_saved_session_cookie(monkeypatch, tmp_path):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    captured = {}

    def fake_snapshot(symbol, **kwargs):
        captured["cookie"] = kwargs.get("cookie", "")
        return {"symbol": "NASDAQ:AAPL", "technicals": {"overall_label": "Buy"}, "unsafe_for_execution": False}

    monkeypatch.setattr("avanza_cli.TRADINGVIEW_SESSION_FILE", tmp_path / ".avanza_tradingview_session.json")
    monkeypatch.setattr("avanza_cli.tradingview_symbol_snapshot", fake_snapshot)

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.execute_mcp_tool("tv_auth_session_set", {"sessionid": "abc", "sessionid_sign": "sig"})
    result = app.execute_mcp_tool("tv_auth_symbol_analytics", {"symbol": "AAPL"})

    assert result["mode"] == "authenticated_scrape"
    assert "sessionid=abc" in captured["cookie"]


def test_tv_scrape_symbol_full_delegates(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    def fake_snapshot(symbol, **kwargs):
        assert symbol == "AAPL"
        return {"symbol": "NASDAQ:AAPL", "field_count": 42, "unsafe_for_execution": False}

    monkeypatch.setattr("avanza_cli.tradingview_symbol_full_snapshot", fake_snapshot)
    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool("tv_scrape_symbol_full", {"symbol": "AAPL"})
    assert result["mode"] == "free_scrape"
    assert result["experimental_scrape_mode"] is True
    assert result["field_count"] == 42


def test_tv_auth_symbol_full_uses_saved_session_cookie(monkeypatch, tmp_path):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    captured = {}

    def fake_snapshot(symbol, **kwargs):
        captured["cookie"] = kwargs.get("cookie", "")
        return {"symbol": "NASDAQ:AAPL", "field_count": 12, "unsafe_for_execution": False}

    monkeypatch.setattr("avanza_cli.TRADINGVIEW_SESSION_FILE", tmp_path / ".avanza_tradingview_session.json")
    monkeypatch.setattr("avanza_cli.tradingview_symbol_full_snapshot", fake_snapshot)
    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.execute_mcp_tool("tv_auth_session_set", {"sessionid": "abc", "sessionid_sign": "sig"})
    result = app.execute_mcp_tool("tv_auth_symbol_full", {"symbol": "AAPL"})

    assert result["mode"] == "authenticated_scrape"
    assert "sessionid=abc" in captured["cookie"]


def test_tv_auth_custom_lists_uses_profile_scrape(monkeypatch):
    from avanza_cli import AvanzaTradingTui, TRADINGVIEW_WATCHLIST_ROW_LIMIT

    class FakeAvanza:
        pass

    captured: dict[str, Any] = {}

    def fake_thread_wrapper(func, *args, **kwargs):
        captured["function"] = getattr(func, "__name__", "")
        captured["kwargs"] = kwargs
        return {
            "lists": [{"id": "list-1", "name": "My Stocks", "count": 2}],
            "items": [{"symbol": "AAPL"}],
            "source": "tradingview-auth-watchlists",
        }

    monkeypatch.setattr("avanza_cli.run_blocking_in_thread", fake_thread_wrapper)

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool(
        "tv_auth_custom_lists",
        {"list_id": "https://www.tradingview.com/watchlists/57177174/", "limit": TRADINGVIEW_WATCHLIST_ROW_LIMIT + 25},
    )

    assert captured["function"] == "tradingview_custom_watchlists_from_profile"
    assert captured["kwargs"]["list_id"] == "57177174"
    assert captured["kwargs"]["limit"] == TRADINGVIEW_WATCHLIST_ROW_LIMIT
    assert result["mode"] == "authenticated_scrape"
    assert result["experimental_scrape_mode"] is True


def test_fmp_analyst_recommendations_tool_dispatch(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    def fake_snapshot(symbol, **kwargs):
        assert symbol == "AAPL"
        return {"symbol": "AAPL", "rows": [{"buy": 11}], "unsafe_for_execution": False}

    monkeypatch.setattr("avanza_cli.fmp_analyst_recommendations_snapshot", fake_snapshot)
    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool("fmp_analyst_recommendations", {"symbol": "AAPL", "api_key": "x"})
    assert result["mode"] == "api"
    assert result["rows"][0]["buy"] == 11


def test_polygon_analyst_insights_tool_dispatch(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    def fake_snapshot(symbol, **kwargs):
        assert symbol == "AAPL"
        return {"symbol": "AAPL", "rows": [{"rating": "Buy"}], "unsafe_for_execution": False}

    monkeypatch.setattr("avanza_cli.polygon_analyst_insights_snapshot", fake_snapshot)
    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    result = app.execute_mcp_tool("polygon_analyst_insights", {"symbol": "AAPL", "api_key": "x"})
    assert result["mode"] == "api"
    assert result["rows"][0]["rating"] == "Buy"


def test_tradingview_cookie_from_browser_cookies_extracts_session_tokens():
    from avanza_cli import tradingview_cookie_from_browser_cookies

    cookies = [
        {"name": "other", "value": "x", "domain": ".tradingview.com"},
        {"name": "sessionid", "value": "abc", "domain": ".tradingview.com"},
        {"name": "sessionid_sign", "value": "sig", "domain": ".tradingview.com"},
    ]
    header = tradingview_cookie_from_browser_cookies(cookies)
    assert "sessionid=abc" in header
    assert "sessionid_sign=sig" in header


def test_tui_portfolio_trade_action_opens_prefilled_order_ticket():
    from avanza_cli import AvanzaTradingTui

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            class RowKey:
                value = "row-1"

            class CellKey:
                row_key = RowKey()

            class FakeCellSelected:
                data_table = app.query_one("#portfolio-table", DataTable)
                cell_key = CellKey()
                value = trade_action_badge("sell")
                stopped = False

                def stop(self):
                    self.stopped = True

            app.portfolio_trade_targets_by_row_key["row-1"] = {
                "stock": "Example AB",
                "order_book_id": "ob-1",
                "volume": "10",
            }
            event = FakeCellSelected()
            app.on_data_table_cell_selected(event)
            await pilot.pause()

            assert event.stopped is True
            assert app.query_one("#order-modal").display is True
            assert app.query_one("#order-instrument-select", Select).value == "ob-1"
            assert app.query_one("#regular-order-type", Select).value == "sell"
            assert app.query_one("#regular-order-volume", Input).value == "10"

            app.open_order_modal_for_portfolio_action(
                "sell",
                {"stock": "Example AB", "order_book_id": "ob-1", "volume": "10"},
            )
            await pilot.pause()

            assert app.query_one("#order-modal").display is True
            assert app.query_one("#order-instrument-select", Select).value == "ob-1"
            assert app.query_one("#regular-order-type", Select).value == "sell"
            assert app.query_one("#regular-order-volume", Input).value == "10"
            app.query_one("#regular-order-price").value = "100"
            await pilot.pause()
            assert "1,000.00 SEK" in str(app.query_one("#regular-order-value").render())

            app.open_order_modal_for_portfolio_action(
                "buy",
                {"stock": "Example AB", "order_book_id": "ob-1", "volume": "10"},
            )
            await pilot.pause()

            assert app.query_one("#regular-order-type", Select).value == "buy"
            assert app.query_one("#regular-order-volume", Input).value == ""
            assert str(app.query_one("#regular-order-value").render()) == "Order value: -"

    asyncio.run(run_app())


def test_tui_order_ticket_validates_required_numeric_fields():
    from avanza_cli import AvanzaTradingTui

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test():
            app.selected_account_id = "acc-1"
            app.query_one("#order-instrument-select", Select).set_options([("Example AB", "ob-1")])
            app.query_one("#order-instrument-select", Select).value = "ob-1"
            app.query_one("#regular-order-valid-until").value = TEST_VALID_UNTIL
            app.query_one("#regular-order-volume").value = "3"

            with pytest.raises(ValueError, match="Limit price is required"):
                app.build_regular_order_request()

            app.query_one("#regular-order-price").value = "abc"
            with pytest.raises(ValueError, match="Limit price must be a number"):
                app.build_regular_order_request()

            app.query_one("#regular-order-price").value = "100"
            app.query_one("#regular-order-volume").value = ""
            with pytest.raises(ValueError, match="Volume is required"):
                app.build_regular_order_request()

            app.query_one("#regular-order-volume").value = "3"
            app.query_one("#regular-order-valid-until").value = ""
            with pytest.raises(ValueError, match="Valid until is required"):
                app.build_regular_order_request()

    asyncio.run(run_app())


def test_tui_order_search_includes_owned_holdings_when_remote_search_fails():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def search_for_stock(self, query, limit):
            raise RuntimeError("remote unavailable")

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            app.avanza = FakeAvanza()
            app.selected_account_id = "acc-1"
            app.latest_portfolio_data = {
                "withOrderbook": [
                    {
                        "account": {"id": "acc-1"},
                        "instrument": {"name": "Broadcom", "orderbook": {"id": "369636"}},
                        "volume": {"value": 17, "unit": "st"},
                    }
                ],
                "withoutOrderbook": [],
            }
            app.query_one("#order-search").value = "broad"
            await pilot.pause()

            app.handle_order_search()
            await pilot.pause()

            assert app.query_one("#order-instrument-select", Select).value == "369636"
            assert app.order_search_labels_by_order_book["369636"] == "Broadcom"
            assert "portfolio result" in str(app.query_one("#order-search-status").render())

    asyncio.run(run_app())


def test_table_selection_can_be_restored_after_rebuild():
    from avanza_cli import AvanzaTradingTui

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            table = app.query_one("#portfolio-table", DataTable)
            table.add_row("Alpha", key="row-a")
            table.add_row("Beta", key="row-b")
            table.move_cursor(row=1, animate=False, scroll=False)
            row_key = selected_table_row_key(table)
            assert table.get_row_at(table.cursor_row)[0] == "Beta"

            table.clear()
            table.add_row("Beta", key="row-b")
            table.add_row("Alpha", key="row-a")
            restore_table_row_selection(table, row_key)
            await pilot.pause()

            assert table.get_row_at(table.cursor_row)[0] == "Beta"

    asyncio.run(run_app())


def test_live_refresh_runs_in_background_thread():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_accounts_positions(self):
            time.sleep(0.2)
            return {"withOrderbook": [], "withoutOrderbook": []}

        def get_all_stop_losses(self):
            time.sleep(0.1)
            return []

        def get_orders(self):
            time.sleep(0.1)
            return []

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            app.avanza = FakeAvanza()
            app.selected_account_id = "acc-1"
            started = time.perf_counter()
            app.refresh_selected_account_live()
            elapsed = time.perf_counter() - started
            assert elapsed < 0.05
            await pilot.pause(0.6)
            assert app.live_refresh_inflight is False

    asyncio.run(run_app())


def test_live_refresh_worker_tolerates_call_from_thread_runtime_error(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def get_accounts_positions(self):
            return {"withOrderbook": [], "withoutOrderbook": []}

        def get_all_stop_losses(self):
            return []

        def get_orders(self):
            return []

    app = AvanzaTradingTui()
    app.avanza = FakeAvanza()
    app.selected_account_id = "acc-1"
    app.live_refresh_inflight = True
    monkeypatch.setattr(app, "require_connection", lambda: app.avanza)
    monkeypatch.setattr(app, "prefetch_quote_and_status_by_order_book", lambda *_args, **_kwargs: ({}, {}))
    monkeypatch.setattr(
        app,
        "call_from_thread",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("app is shutting down")),
    )

    app._refresh_selected_account_live_worker()

    assert app.live_refresh_inflight is False


def test_update_check_worker_tolerates_call_from_thread_runtime_error(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    app = AvanzaTradingTui()
    app.update_check_inflight = True
    monkeypatch.setattr("avanza_cli.github_latest_version_info", lambda _repo: {"version": "v9.9.9"})
    monkeypatch.setattr(
        app,
        "call_from_thread",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("app is shutting down")),
    )

    app._update_check_worker()

    assert app.update_check_inflight is False


def test_tv_lists_worker_tolerates_call_from_thread_runtime_error(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    app = AvanzaTradingTui()
    app.tv_lists_refresh_inflight = True
    monkeypatch.setattr("avanza_cli.tradingview_custom_watchlists_from_profile", lambda **_kwargs: {"lists": [], "items": []})
    monkeypatch.setattr(
        app,
        "call_from_thread",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("app is shutting down")),
    )

    app._refresh_tv_lists_worker(None, None)

    assert app.tv_lists_refresh_inflight is False
    assert app.tv_lists_refresh_pending_value is None


def test_logout_selected_session_switches_to_remaining_tenant():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    overview_one = {
        "accounts": [
            {
                "id": "acc-1",
                "name": {"defaultName": "Acc 1", "userDefinedName": "Acc 1"},
                "type": "ISK",
                "totalValue": {"value": 1000, "unit": "SEK"},
                "buyingPower": {"value": 500, "unit": "SEK"},
                "status": "ACTIVE",
            }
        ]
    }
    overview_two = {
        "accounts": [
            {
                "id": "acc-2",
                "name": {"defaultName": "Acc 2", "userDefinedName": "Acc 2"},
                "type": "ISK",
                "totalValue": {"value": 1500, "unit": "SEK"},
                "buyingPower": {"value": 700, "unit": "SEK"},
                "status": "ACTIVE",
            }
        ]
    }
    portfolio = {"withOrderbook": [], "withoutOrderbook": []}

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            first = app.register_tenant_session(FakeAvanza(), overview_one, portfolio, [], [], label="Session One")
            second = app.register_tenant_session(FakeAvanza(), overview_two, portfolio, [], [], label="Session Two")
            app.load_active_state_from_tenant(first)
            app.query_one("#login-screen").display = False
            app.query_one("#workspace").display = True
            app.apply_accounts_overview(overview_one, announce=False)
            app.refresh_session_select_options()
            app.query_one("#session-select", Select).value = first.session_id
            app.logout_selected_session()
            await pilot.pause()

            assert first.session_id not in app.tenant_sessions
            assert app.active_session_id == second.session_id
            assert app.avanza is second.avanza

    asyncio.run(run_app())


def test_logout_selected_session_logs_out_to_login_screen_when_last_session_removed():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    overview = {
        "accounts": [
            {
                "id": "acc-1",
                "name": {"defaultName": "Acc 1", "userDefinedName": "Acc 1"},
                "type": "ISK",
                "totalValue": {"value": 1000, "unit": "SEK"},
                "buyingPower": {"value": 500, "unit": "SEK"},
                "status": "ACTIVE",
            }
        ]
    }
    portfolio = {"withOrderbook": [], "withoutOrderbook": []}

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            first = app.register_tenant_session(FakeAvanza(), overview, portfolio, [], [], label="Session One")
            app.load_active_state_from_tenant(first)
            app.query_one("#login-screen").display = False
            app.query_one("#workspace").display = True
            app.apply_accounts_overview(overview, announce=False)
            app.refresh_session_select_options()
            app.query_one("#session-select", Select).value = first.session_id
            app.logout_selected_session()
            await pilot.pause()

            assert not app.tenant_sessions
            assert app.avanza is None
            assert app.query_one("#login-screen").display is True
            assert app.query_one("#workspace").display is False

    asyncio.run(run_app())


def test_tui_refresh_selected_session_opens_reauth_modal():
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        pass

    overview = {
        "accounts": [
            {
                "id": "acc-1",
                "name": {"defaultName": "Acc 1", "userDefinedName": "Acc 1"},
                "type": "ISK",
                "totalValue": {"value": 1000, "unit": "SEK"},
                "buyingPower": {"value": 500, "unit": "SEK"},
                "status": "ACTIVE",
            }
        ]
    }
    portfolio = {"withOrderbook": [], "withoutOrderbook": []}

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            session = app.register_tenant_session(FakeAvanza(), overview, portfolio, [], [], label="Session One")
            app.load_active_state_from_tenant(session)
            app.query_one("#login-screen").display = False
            app.query_one("#workspace").display = True
            app.apply_accounts_overview(overview, announce=False)
            app.refresh_session_select_options()
            app.query_one("#session-select", Select).value = session.session_id
            app.handle_refresh_selected_session()
            await pilot.pause()

            assert app.query_one("#extra-login-modal").display is True
            assert app.login_target_session_id == session.session_id
            assert "Refresh selected session login" in str(app.query_one("#extra-login-title", Static).renderable)
            assert app.query_one("#extra-session-label", Input).value == "Session One"

    asyncio.run(run_app())


def test_tui_1password_login_uses_op_credentials(monkeypatch, tmp_path):
    from avanza_cli import AvanzaTradingTui

    monkeypatch.setattr("avanza_cli.MCP_SESSION_FILE", tmp_path / "mcp-session.json")
    monkeypatch.setattr(
        "avanza_cli.onepassword_credentials",
        lambda item, vault=None: {
            "username": "alice",
            "password": "secret-password",
            "totpToken": "123456",
        },
    )

    class FakeAvanza:
        def __init__(self, credentials):
            self.credentials = credentials

        def get_overview(self):
            return {
                "accounts": [
                    {
                        "id": "acc-1",
                        "name": "ISK",
                        "type": "ISK",
                        "totalValue": {"value": 1000, "unit": "SEK"},
                        "buyingPower": {"value": 500, "unit": "SEK"},
                        "status": "ACTIVE",
                    }
                ]
            }

        def get_accounts_positions(self):
            return {"withOrderbook": [], "withoutOrderbook": []}

        def get_all_stop_losses(self):
            return []

        def get_orders(self):
            return []

    monkeypatch.setattr("avanza_cli.Avanza", FakeAvanza)

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            app.query_one("#onepassword-item").value = "Avanza"
            app.query_one("#onepassword-vault").value = "Private"

            app.handle_1password_login()
            await pilot.pause()

            assert app.query_one("#login-screen").display is False
            assert app.query_one("#workspace").display is True
            assert app.query_one("#password").value == ""
            assert app.query_one("#totp").value == ""
            assert app.selected_account_id == "acc-1"

    asyncio.run(run_app())


def test_prompt_credentials_uses_totp_token(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "alice")
    prompts = iter(["secret-password", "123456"])
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(prompts))

    credentials = prompt_credentials(None)

    assert credentials == {
        "username": "alice",
        "password": "secret-password",
        "totpToken": "123456",
    }


def test_onepassword_credentials_reads_item_and_otp(monkeypatch):
    calls = []

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(command, check, capture_output, text, timeout):
        calls.append(command)
        if command[:3] == ["op", "item", "get"] and "--format" in command:
            return Result(json.dumps({
                "fields": [
                    {"label": "username", "value": "alice"},
                    {"label": "password", "value": "secret-password"},
                ]
            }))
        if command[:3] == ["op", "item", "get"] and "--otp" in command:
            return Result("123456\n")
        raise AssertionError(command)

    monkeypatch.setattr("subprocess.run", fake_run)

    credentials = onepassword_credentials("Avanza", "Private")

    assert credentials == {
        "username": "alice",
        "password": "secret-password",
        "totpToken": "123456",
    }
    assert calls == [
        ["op", "item", "get", "Avanza", "--format", "json", "--vault", "Private"],
        ["op", "item", "get", "Avanza", "--otp", "--vault", "Private"],
    ]


def test_render_accounts_overview_outputs_human_table(capsys):
    from avanza_cli import render_accounts_overview

    render_accounts_overview(
        {
            "accounts": [
                {
                    "id": "acc-1",
                    "name": {"defaultName": "ISK", "userDefinedName": "Trading"},
                    "type": "ISK",
                    "totalValue": {"value": 1000, "unit": "SEK"},
                    "buyingPower": {"value": 250, "unit": "SEK"},
                    "status": "ACTIVE",
                }
            ]
        }
    )
    output = capsys.readouterr().out

    assert "Accounts" in output
    assert "Trading" in output
    assert "1000 SEK" in output
    assert '"accounts"' not in output


def test_stoploss_set_dry_run_does_not_require_login(capsys):
    parser = build_parser()
    args = parser.parse_args(
        [
            "stoploss",
            "set",
            "--account-id",
            "acc-1",
            "--order-book-id",
            "ob-1",
            "--trigger-type",
            "follow-upwards",
            "--trigger-value",
            "5",
            "--trigger-value-type",
            "%",
            "--valid-until",
            TEST_VALID_UNTIL,
            "--order-type",
            "sell",
            "--order-price",
            "1",
            "--order-price-type",
            "%",
            "--volume",
            "10",
        ]
    )

    args.func(args)
    output = capsys.readouterr().out

    assert "Dry Run" in output
    assert "Account: acc-1" in output
    assert "Order book: ob-1" in output
    assert "Trigger: FOLLOW_UPWARDS 5.0%" in output
    assert "Order valid days after trigger: 1" in output
    assert "Derived order expiry (if triggered today):" in output
    assert '"account_id"' not in output


def test_stoploss_set_dry_run_allows_explicit_order_valid_days(capsys):
    parser = build_parser()
    args = parser.parse_args(
        [
            "stoploss",
            "set",
            "--account-id",
            "acc-1",
            "--order-book-id",
            "ob-1",
            "--trigger-type",
            "follow-upwards",
            "--trigger-value",
            "5",
            "--order-price",
            "1",
            "--volume",
            "10",
            "--order-valid-days",
            "3",
        ]
    )

    args.func(args)
    output = capsys.readouterr().out

    assert "Order valid days after trigger: 3" in output
