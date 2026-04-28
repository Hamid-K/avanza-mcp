import argparse
import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from textual import events
from textual.geometry import Size
from textual.widgets import Button, DataTable, Input, Select

from avanza.constants import OrderType, StopLossPriceType
from rich.text import Text

from avanza_cli import (
    build_parser,
    call_mcp_bridge,
    enum_value,
    load_mcp_session,
    onepassword_credentials,
    parse_date,
    parse_price_type,
    prompt_credentials,
    read_mcp_message,
    restore_table_row_selection,
    selected_table_row_key,
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


def test_enum_value_accepts_hyphenated_names():
    assert enum_value(StopLossPriceType, "percentage") is StopLossPriceType.PERCENTAGE
    assert enum_value(OrderType, "sell") is OrderType.SELL


def test_parse_price_type_accepts_percent_symbol():
    assert parse_price_type("%") == "percentage"
    assert parse_price_type("SEK") == "monetary"


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
            assert isinstance(app.query_one("#paper-mode-toggle"), Button)
            assert app.query_one("#paper-mode-label").renderable == "Paper"
            assert isinstance(app.query_one("#mcp-toggle"), Button)
            assert app.query_one("#mcp-label").renderable == "MCP"
            assert isinstance(app.query_one("#mcp-write-toggle"), Button)
            assert app.query_one("#mcp-write-label").renderable == "Live R/W"
            assert app.query_one("#mcp-log") is not None
            assert app.query_one("#order-ticket-resizer") is not None
            assert app.query_one("#stoploss-ticket-resizer") is not None
            resizer = app.query_one("#pane-resizer")
            assert resizer.renderable == "─"
            assert app.query_one("#stoploss-table") is not None
            assert app.query_one("#stoploss-modal").display is False
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
            assert app.positions_pane_weight == 3.5
            assert app.activity_pane_weight == 1.5
            resizer.on_mouse_up(FakeMouse(12))
            assert app.is_resizing_panes is False

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


def test_tui_login_hides_credentials_and_shows_workspace(monkeypatch, tmp_path):
    from avanza_cli import AvanzaTradingTui

    monkeypatch.setattr("avanza_cli.MCP_SESSION_FILE", tmp_path / "mcp-session.json")

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
                        "status": "ACTIVE",
                    },
                    {
                        "id": "acc-2",
                        "name": {"defaultName": "ISK", "userDefinedName": "Trading"},
                        "type": "ISK",
                        "totalValue": {"value": 5000, "unit": "SEK"},
                        "buyingPower": {"value": 750, "unit": "SEK"},
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

        def delete_stop_loss_order(self, account_id, stop_loss_id):
            return {"deleted": True, "account_id": account_id, "stop_loss_id": stop_loss_id}

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
            assert "Day P/L" in profit_label
            assert "+10.00 SEK" in profit_metric
            app.cycle_profit_metric()
            assert "Position P/L" in str(app.query_one("#profit-cycle", Button).label)
            assert app.query_one("#account-select").value == "acc-2"
            assert app.query_one("#instrument-select").value == "ob-1"
            assert app.holding_volumes_by_order_book == {"ob-1": "25.0"}
            assert app.live_refresh_timer is not None
            assert app.paper_mode_enabled is True
            assert app.execute_mcp_tool("avanza_status", {})["read_write"] is False
            accounts = app.execute_mcp_tool("avanza_accounts", {})
            assert accounts[1]["Name"] == "Trading"
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
            cancel_target = next(
                target
                for target in app.cancel_targets_by_row_key.values()
                if target["id"] == paper_regular["order"]["id"]
            )
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
            app.mcp_write_enabled = True
            deletion = app.execute_mcp_tool(
                "avanza_stoploss_delete",
                {"account_id": "acc-2", "stop_loss_id": "sl-1", "confirm": True},
            )
            assert deletion["dry_run"] is False
            assert deletion["result"]["deleted"] is True
            app.start_mcp_bridge()
            session = load_mcp_session(tmp_path / "mcp-session.json")
            bridge_status = await asyncio.to_thread(call_mcp_bridge, session, "avanza_status", {})
            assert bridge_status["ok"] is True
            assert bridge_status["result"]["enabled"] is True
            app.stop_mcp_bridge()

    asyncio.run(run_app())


def test_tui_tracks_terminal_resize():
    from avanza_cli import AvanzaTradingTui

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            app.on_resize(events.Resize(Size(120, 40), Size(120, 40)))
            await pilot.pause()

            assert app.last_resize == (120, 40)

    asyncio.run(run_app())


def test_load_mcp_session_requires_existing_session_file(tmp_path):
    with pytest.raises(RuntimeError):
        load_mcp_session(tmp_path / "missing-session.json")


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
    assert any(tool["name"] == "avanza_live_snapshot" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_paper_stoploss_set" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_paper_order_set" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "avanza_order_set" for tool in tools["result"]["tools"])


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

            app.open_order_modal_for_portfolio_action(
                "buy",
                {"stock": "Example AB", "order_book_id": "ob-1", "volume": "10"},
            )
            await pilot.pause()

            assert app.query_one("#regular-order-type", Select).value == "buy"
            assert app.query_one("#regular-order-volume", Input).value == ""

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
