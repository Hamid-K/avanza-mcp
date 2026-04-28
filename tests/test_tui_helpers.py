from avanza_cli import (
    account_display_name,
    account_row,
    account_rows_from_overview,
    amount,
    cash_row,
    matches_account,
    position_state_row,
    position_row,
    stop_loss_row,
)


def test_amount_formats_value_objects():
    assert amount({"value": {"value": 123.45, "unit": "SEK"}}, "value") == "123.45 SEK"


def test_account_display_name_prefers_user_defined_name():
    assert (
        account_display_name(
            {"name": {"defaultName": "ISK", "userDefinedName": "Trading"}}
        )
        == "Trading"
    )


def test_account_row_formats_overview_account():
    row = account_row(
        {
            "id": "acc-1",
            "name": {"defaultName": "ISK", "userDefinedName": None},
            "type": "ISK",
            "totalValue": {"value": 10000, "unit": "SEK"},
            "buyingPower": {"value": 2500, "unit": "SEK"},
            "status": "ACTIVE",
        }
    )

    assert row == ("acc-1", "ISK", "ISK", "10000 SEK", "2500 SEK", "ACTIVE")


def test_account_rows_from_overview_uses_accounts_list():
    accounts = account_rows_from_overview(
        {
            "accounts": [
                {"id": "acc-1", "name": {"defaultName": "ISK"}},
                {"name": {"defaultName": "missing id"}},
                "not-account",
            ]
        }
    )

    assert accounts == [{"id": "acc-1", "name": {"defaultName": "ISK"}}]


def test_matches_account_filters_by_nested_account_id():
    item = {"account": {"id": "acc-1"}}

    assert matches_account(item, None)
    assert matches_account(item, "acc-1")
    assert not matches_account(item, "acc-2")


def test_position_row_extracts_nested_position_data():
    row = position_row(
        {
            "id": "pos-1",
            "account": {"name": "ISK", "id": "acc-1"},
            "instrument": {
                "name": "Example AB",
                "isin": "SE0000000001",
                "orderbook": {"id": "ob-1"},
            },
            "volume": {"value": 10, "unit": "st"},
            "value": {"value": 1000, "unit": "SEK"},
            "averageAcquiredPrice": {"value": 90, "unit": "SEK"},
            "acquiredValue": {"value": 900, "unit": "SEK"},
            "lastTradingDayPerformance": {
                "relative": {"value": 1.2, "unit": "%"},
            },
        }
    )

    assert row == (
        "ISK",
        "acc-1",
        "Example AB",
        "ob-1",
        "SE0000000001",
        "10 st",
        "1000 SEK",
        "90 SEK",
        "900 SEK",
        "1.2 %",
    )


def test_position_state_row_includes_day_and_profit_state():
    row = position_state_row(
        {
            "account": {"name": "ISK", "id": "acc-1"},
            "instrument": {
                "name": "Example AB",
                "orderbook": {"id": "ob-1"},
            },
            "volume": {"value": 10, "unit": "st"},
            "value": {"value": 1100, "unit": "SEK"},
            "averageAcquiredPrice": {"value": 90, "unit": "SEK"},
            "acquiredValue": {"value": 900, "unit": "SEK"},
            "lastTradingDayPerformance": {
                "relative": {"value": 1.25, "unit": "%"},
                "absolute": {"value": 13.75, "unit": "SEK"},
            },
        }
    )

    assert row == (
        "Example AB",
        "ob-1",
        "10 st",
        "1100 SEK",
        "90 SEK",
        "+1.25%",
        "+13.75 SEK",
        "+22.22%",
        "+200.00 SEK",
    )


def test_cash_row_formats_cash_position():
    row = cash_row(
        {
            "id": "cash-1",
            "account": {"name": "ISK", "id": "acc-1"},
            "totalBalance": {"value": 5000, "unit": "SEK"},
        }
    )

    assert row[:7] == ("ISK", "acc-1", "Cash", "", "", "", "5000 SEK")


def test_stop_loss_row_extracts_order_data():
    row = stop_loss_row(
        {
            "id": "sl-1",
            "status": "ACTIVE",
            "account": {"name": "ISK", "id": "acc-1"},
            "orderbook": {"name": "Example AB", "id": "ob-1"},
            "trigger": {
                "type": "FOLLOW_UPWARDS",
                "value": 5,
                "valueType": "PERCENTAGE",
                "validUntil": "2026-05-28",
            },
            "order": {
                "type": "SELL",
                "volume": 10,
                "price": 1,
                "priceType": "PERCENTAGE",
            },
        }
    )

    assert row == (
        "sl-1",
        "ACTIVE",
        "ISK",
        "acc-1",
        "Example AB",
        "ob-1",
        "FOLLOW_UPWARDS 5 PERCENTAGE",
        "SELL 10 @ 1 PERCENTAGE",
        "2026-05-28",
    )
