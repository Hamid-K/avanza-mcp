"""Stop prices retain native currency or explicitly disclose unknown units."""

import pytest

from avanza_mcp.records import stop_loss_mcp_dict
from avanza_mcp.rendering import (
    active_stop_loss_row,
    stop_loss_activity_row,
    stop_loss_mcp_row,
    stop_loss_monetary_currency,
    stop_loss_row,
)


def stop(currency=None):
    return {
        "id": "fixture-stop",
        "account": {"id": "fixture-account", "name": "Fixture"},
        "orderbook": {"id": "fixture-instrument", "name": "Fixture", **({"currency": currency} if currency else {})},
        "trigger": {"type": "LESS_OR_EQUAL", "value": 100, "valueType": "MONETARY"},
        "order": {"type": "SELL", "volume": 2, "price": 95, "priceType": "MONETARY"},
    }


@pytest.mark.parametrize("currency", ["USD", "EUR", "SEK"])
def test_stop_mcp_exposes_explicit_native_currency(currency):
    result = stop_loss_mcp_dict(stop(currency))
    assert result["currency"] == currency
    assert result["currency_status"] == "EXPLICIT_NATIVE_UNIT"
    assert result["Trigger"].endswith(f"100 {currency}")
    assert result["Order"].endswith(f"95 {currency}")


@pytest.mark.parametrize("renderer", [stop_loss_row, stop_loss_mcp_row, stop_loss_activity_row, active_stop_loss_row])
@pytest.mark.parametrize("currency,expected", [("USD", "USD"), (None, "UNKNOWN")])
def test_stop_renderers_never_infer_sek(renderer, currency, expected):
    text = " ".join(str(value) for value in renderer(stop(currency)))
    assert f"100 {expected}" in text
    assert "SEK" not in text


def test_unknown_currency_is_null_in_mcp_and_not_a_sek_price():
    result = stop_loss_mcp_dict(stop())
    assert result["currency"] is None
    assert result["currency_status"] == "UNKNOWN_OR_CONFLICTING"
    assert result["Trigger"].endswith("100 UNKNOWN")
    assert result["Order"].endswith("95 UNKNOWN")


def test_conflicting_explicit_units_are_not_silently_selected():
    item = stop("USD")
    item["currency"] = "SEK"
    assert stop_loss_monetary_currency(item) is None
    assert stop_loss_mcp_dict(item)["currency"] is None


def test_percentage_stops_remain_percentages_without_currency():
    item = stop()
    item["trigger"]["valueType"] = item["order"]["priceType"] = "PERCENTAGE"
    result = stop_loss_mcp_dict(item)
    assert result["Trigger"].endswith("100%")
    assert result["Order"].endswith("95%")
