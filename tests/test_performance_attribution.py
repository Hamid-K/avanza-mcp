from datetime import date

import pytest

from avanza_mcp.performance_attribution import (
    build_account_cost_attribution,
    summarize_cash_events,
)
from avanza_mcp.frozen_holdings_attribution import build_frozen_holdings_attribution


def point(day: str, relative: float, value: float) -> dict:
    return {
        "date": day,
        "timestamp": 0,
        "development_relative": {"value": relative, "unit": "%"},
        "account_value": {"value": value, "unit": "SEK"},
    }


def test_cost_attribution_reproduces_actual_path_and_adds_back_costs():
    result = build_account_cost_attribution(
        account_id="acc-1",
        performance_points=[
            point("2026-05-06", 0.0, 1000.0),
            point("2026-05-07", -1.0, 990.0),
            point("2026-05-08", -2.0, 980.0),
        ],
        transaction_rows=[
            {
                "Trade Date": "2026-05-07T10:00:00",
                "Type": "BUY",
                "Amount": "1000 SEK",
                "Commission": "10 SEK",
                "ISIN": "US0000000001",
            }
        ],
        cash_event_rows=[],
        start_date=date(2026, 5, 6),
        fx_fee_rate=0.0025,
        include_daily=True,
        top_cost_days=5,
    )

    assert result["returns"]["actual_percent"] == pytest.approx(-2.0)
    assert result["returns"]["zero_commission_percent"] == pytest.approx(-1.01010101)
    assert result["returns"]["zero_commission_plus_modeled_fx_percent"] == pytest.approx(-0.76262626)
    assert result["costs"]["posted_commission_sek"] == pytest.approx(10.0)
    assert result["costs"]["modeled_fx_sek"] == pytest.approx(2.5)
    assert result["monthly"][0]["month"] == "2026-05"
    assert len(result["daily"]) == 2


def test_cost_attribution_deduplicates_exact_transaction_rows():
    row = {
        "Trade Date": "2026-05-07",
        "Type": "SELL",
        "Amount": "500 SEK",
        "Commission": "5 SEK",
        "ISIN": "SE0000000001",
    }
    result = build_account_cost_attribution(
        account_id="acc-1",
        performance_points=[
            point("2026-05-06", 0.0, 1000.0),
            point("2026-05-07", -1.0, 990.0),
        ],
        transaction_rows=[row, dict(row)],
        cash_event_rows=[],
        start_date=None,
        fx_fee_rate=0.0025,
        include_daily=False,
        top_cost_days=1,
    )

    assert result["costs"]["raw_transaction_rows"] == 2
    assert result["costs"]["deduplicated_transaction_rows"] == 1
    assert result["costs"]["posted_commission_sek"] == pytest.approx(5.0)
    assert "daily" not in result


def test_cost_attribution_reports_missing_commission_as_lower_bound():
    result = build_account_cost_attribution(
        account_id="acc-1",
        performance_points=[
            point("2026-05-06", 0.0, 1000.0),
            point("2026-05-07", -1.0, 990.0),
        ],
        transaction_rows=[
            {
                "Trade Date": "2026-05-07",
                "Type": "SELL",
                "Amount": "500 SEK",
                "Commission": "None",
                "ISIN": "SE0000000001",
            }
        ],
        cash_event_rows=[],
        start_date=None,
        fx_fee_rate=0.0025,
        include_daily=False,
        top_cost_days=0,
    )

    assert result["status"] == "COMPLETE_COMMISSION_LOWER_BOUND_WITH_MODELED_FX"
    assert result["costs"]["missing_commission_rows"] == 1
    assert result["costs"]["missing_commission_notional_sek"] == pytest.approx(500.0)


def test_zero_commission_is_not_reported_as_missing():
    result = build_account_cost_attribution(
        account_id="acc-1",
        performance_points=[
            point("2026-05-06", 0.0, 1000.0),
            point("2026-05-07", -1.0, 990.0),
        ],
        transaction_rows=[
            {
                "Trade Date": "2026-05-07",
                "Type": "SELL",
                "Amount": "500 SEK",
                "Commission": 0,
                "ISIN": "SE0000000001",
            }
        ],
        cash_event_rows=[],
        start_date=None,
        fx_fee_rate=0.0025,
        include_daily=False,
        top_cost_days=0,
    )

    assert result["costs"]["missing_commission_rows"] == 0


def test_cash_event_summary_separates_external_flows():
    summary = summarize_cash_events(
        [
            {"Type": "DEPOSIT", "Amount": "100 SEK"},
            {"Type": "WITHDRAW", "Amount": "-40 SEK"},
            {"Type": "DIVIDEND", "Amount": "5 SEK"},
        ]
    )

    assert summary["net_external_flow_sek"] == pytest.approx(60.0)
    assert summary["amounts_sek"]["DIVIDEND"] == pytest.approx(5.0)


def test_cost_attribution_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="fx_fee_rate"):
        build_account_cost_attribution(
            account_id="acc-1",
            performance_points=[point("2026-05-06", 0.0, 1000.0), point("2026-05-07", 1.0, 1010.0)],
            transaction_rows=[],
            cash_event_rows=[],
            start_date=None,
            fx_fee_rate=0.5,
            include_daily=False,
            top_cost_days=1,
        )


def test_frozen_holdings_reconstructs_start_volume_and_cash_flow_adjusted_path():
    result = build_frozen_holdings_attribution(
        account_id="acc-1",
        start_date=date(2026, 5, 6),
        performance_points=[
            point("2026-05-06", 0.0, 1000.0),
            point("2026-05-07", 1.0, 1050.0),
            point("2026-05-08", 2.0, 1100.0),
        ],
        portfolio_rows=[{"orderbook_id": "1", "stock": "Example", "volume": 10}],
        transaction_rows=[
            {"Trade Date": "2026-05-07", "Type": "BUY", "Volume": 2, "Order Book ID": "1"},
            {"Trade Date": "2026-05-07", "Type": "SELL", "Volume": 1, "Order Book ID": "1"},
        ],
        cash_event_rows=[
            {"Trade Date": "2026-05-07", "Type": "DEPOSIT", "Amount": "50 SEK"},
            {"Trade Date": "2026-05-08", "Type": "DIVIDEND", "Amount": "5 SEK"},
        ],
        chart_points_by_orderbook={
            "1": [
                {"date": "2026-05-06", "close": 100},
                {"date": "2026-05-07", "close": 110},
                {"date": "2026-05-08", "close": 120},
            ]
        },
        include_daily=True,
    )

    assert result["status"] == "COMPLETE"
    assert result["reconstruction"]["reconstructed_start_instrument_count"] == 1
    assert result["reconstruction"]["start_holdings_value_sek"] == pytest.approx(900.0)
    assert result["reconstruction"]["start_cash_residual_sek"] == pytest.approx(100.0)
    assert result["daily"][0]["external_flow_sek"] == pytest.approx(50.0)
    assert result["daily"][1]["dividends_sek"] == pytest.approx(5.0)
    assert result["returns"]["frozen_starting_holdings_percent"] == pytest.approx(18.08333333)


def test_frozen_holdings_fails_closed_on_unknown_cash_event_and_missing_chart():
    result = build_frozen_holdings_attribution(
        account_id="acc-1",
        start_date=date(2026, 5, 6),
        performance_points=[point("2026-05-06", 0.0, 1000.0), point("2026-05-07", 1.0, 1010.0)],
        portfolio_rows=[{"orderbook_id": "1", "stock": "Example", "volume": 10}],
        transaction_rows=[],
        cash_event_rows=[{"Trade Date": "2026-05-07", "Type": "UNKNOWN", "Amount": "10 SEK"}],
        chart_points_by_orderbook={},
        include_daily=False,
    )

    assert result["status"] == "BLOCKED_INCOMPLETE_HISTORY"
    assert {item["issue"] for item in result["issues"]} == {"missing_chart_history", "Unsupported non-zero cash event type: UNKNOWN"}
