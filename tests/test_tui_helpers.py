from avanza_cli import amount, cash_row, position_row, stop_loss_row


def test_amount_formats_value_objects():
    assert amount({"value": {"value": 123.45, "unit": "SEK"}}, "value") == "123.45 SEK"


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
