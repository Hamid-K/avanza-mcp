import argparse
import asyncio

import pytest
from textual import events
from textual.geometry import Size

from avanza.constants import OrderType, StopLossPriceType

from avanza_cli import build_parser, enum_value, parse_date, prompt_credentials


def test_parse_date_accepts_iso_date():
    assert parse_date("2026-05-28").isoformat() == "2026-05-28"


def test_parse_date_rejects_non_iso_date():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_date("28-05-2026")


def test_enum_value_accepts_hyphenated_names():
    assert enum_value(StopLossPriceType, "percentage") is StopLossPriceType.PERCENTAGE
    assert enum_value(OrderType, "sell") is OrderType.SELL


def test_parser_includes_portfolio_commands():
    parser = build_parser()
    args = parser.parse_args(["portfolio", "positions", "--username", "alice"])

    assert args.command == "portfolio"
    assert args.portfolio_command == "positions"
    assert args.username == "alice"


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
    assert "--trigger-value-type {monetary,percentage}" in stoploss_help


def test_tui_mounts_headless():
    from avanza_cli import AvanzaTradingTui

    async def run_app() -> None:
        app = AvanzaTradingTui()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#login-screen").display is True
            assert app.query_one("#workspace").display is False
            assert app.query_one("#account-select") is not None
            assert app.query_one("#portfolio-table") is not None
            assert app.query_one("#stoploss-table") is not None
            assert app.query_one("#stoploss-modal").display is False

    asyncio.run(run_app())


def test_tui_login_hides_credentials_and_shows_workspace(monkeypatch):
    from avanza_cli import AvanzaTradingTui

    class FakeAvanza:
        def __init__(self, credentials):
            self.credentials = credentials

        def get_overview(self):
            return {
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

        def get_accounts_positions(self):
            return {"withOrderbook": [], "withoutOrderbook": [], "cashPositions": []}

        def get_all_stop_losses(self):
            return []

        def get_orders(self):
            return []

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
            assert app.selected_account_id == "acc-1"
            assert "Trading" in str(app.query_one("#selected-account").render())
            assert app.query_one("#account-select").value == "acc-1"
            assert app.live_refresh_timer is not None

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
            "percentage",
            "--valid-until",
            "2026-05-28",
            "--order-type",
            "sell",
            "--order-price",
            "1",
            "--order-price-type",
            "percentage",
            "--volume",
            "10",
        ]
    )

    args.func(args)
    output = capsys.readouterr().out

    assert "Dry Run" in output
    assert "Account: acc-1" in output
    assert "Order book: ob-1" in output
    assert "Trigger: FOLLOW_UPWARDS 5.0 PERCENTAGE" in output
    assert '"account_id"' not in output
