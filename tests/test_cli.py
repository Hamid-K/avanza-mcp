import argparse
import json

import pytest

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


def test_dump_outputs_pretty_json(capsys):
    from avanza_cli import dump

    dump({"b": 1, "a": {"c": 2}})
    output = capsys.readouterr().out

    assert json.loads(output) == {"a": {"c": 2}, "b": 1}
    assert output.startswith("{\n")
    assert '  "a": {' in output


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

    assert "Dry run" in output
    assert '"account_id": "acc-1"' in output
    assert '"type": "FOLLOW_UPWARDS"' in output
    assert '"value_type": "PERCENTAGE"' in output
