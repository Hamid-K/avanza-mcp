import argparse
import asyncio
import io
import json
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from textual import events
from textual.geometry import Size
from textual.widgets import Button, DataTable, Input, Select

from avanza.constants import OrderType, StopLossPriceType, TimePeriod
from rich.text import Text

from avanza_cli import (
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


def test_parse_date_accepts_iso_date():
    assert parse_date("2026-05-28").isoformat() == "2026-05-28"


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

    assert args.valid_until == max_valid_until_date()
    assert args.order_valid_days == STOPLOSS_ORDER_VALID_DAYS_DEFAULT

    _, _, preview = build_stop_loss_preview(vars(args))
    assert preview["stop_loss_trigger"]["valid_until"] == max_valid_until_date().isoformat()
    assert preview["stop_loss_order_event"]["valid_days"] == STOPLOSS_ORDER_VALID_DAYS_DEFAULT


def test_tui_mounts_headless():
    from avanza_cli import AvanzaTradingTui

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#login-screen").display is True
            assert app.query_one("#workspace").display is False
            assert app.query_one("#onepassword-item") is not None
            assert app.query_one("#onepassword-vault") is not None
            assert isinstance(app.query_one("#onepassword-login"), Button)
            assert app.query_one("#account-row") is not None
            assert app.query_one("#metric-grid") is not None
            assert app.query_one("#clock-status") is not None
            assert app.query_one("#button-controls") is not None
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
                    "valid_until": "2026-05-28",
                    "order_price": 1,
                    "order_price_type": "%",
                    "volume": 10,
                },
            )
            assert dry_run["dry_run"] is True
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
                    "valid_until": "2026-05-28",
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
            app.query_one("#valid-until").value = "2026-05-28"
            app.query_one("#trigger-value").value = "5"
            app.query_one("#order-price").value = "1"
            app.query_one("#volume").value = "10"
            app.handle_place_live()
            assert len(app.execute_mcp_tool("avanza_paper_orders", {"active_only": True})["orders"]) == 1
            app.query_one("#regular-order-valid-until").value = "2026-05-28"
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
                    "valid_until": "2026-05-28",
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
                        "valid_until": "2026-05-28",
                        "volume": 2,
                        "confirm": True,
                    },
                )
            app.mcp_write_enabled = True
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
                    "valid_until": "2026-05-28",
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
                    "valid_until": "2026-05-28",
                    "order_type": "sell",
                    "order_price": 1,
                    "order_price_type": "%",
                    "volume": 10,
                },
            )
            assert stoploss_edit_dry["dry_run"] is True
            open_order_edit_dry = app.execute_mcp_tool(
                "avanza_open_order_edit",
                {
                    "account_id": "acc-2",
                    "order_id": "ord-1",
                    "price": 101,
                    "valid_until": "2026-05-28",
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
                    "valid_until": "2026-05-28",
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
                    "valid_until": "2026-05-28",
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
    assert any(tool["name"] == "avanza_account_performance" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_live_snapshot" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_open_orders" for tool in tools["result"]["tools"])
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
    assert snapshot["analytics"]["close"] == 280.14
    assert snapshot["technicals"]["overall_score"] == 0.60
    assert snapshot["technicals"]["overall_label"] == "Strong Buy"
    assert snapshot["technicals"]["moving_average_label"] == "Strong Buy"
    assert snapshot["unsafe_for_execution"] is False


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
            app.query_one("#regular-order-valid-until").value = "2026-05-28"
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
            "2026-05-28",
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
    assert '"account_id"' not in output
