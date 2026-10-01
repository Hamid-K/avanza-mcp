"""Mocked provider tests: no account connection or mutation is made."""

from copy import deepcopy
from datetime import date, datetime

import pytest

import avanza_mcp.core.snapshots as snapshots
from avanza_mcp.core.snapshots import CoreSnapshotsMixin
from tests.test_performance_attribution import point


class FixtureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 5, 9, 12, tzinfo=tz)


class Provider(CoreSnapshotsMixin):
    def __init__(self):
        self.calls = []
        self.portfolio_reads = 0
        self.changed = False
        self.quote_changed = False
        self.truncated = False
        self.scope_valid = True
        self.foreign_row = False
        self.duplicate_row = False
        self.malformed_date = False

    def account_performance_snapshot(self, avanza, account_id, period):
        return {"account_id": account_id, "period": period, "chart_points": [point("2026-05-06", 0, 1000), point("2026-05-08", 10, 1100)]}

    def portfolio_snapshot(self, avanza, account_id, **kwargs):
        self.portfolio_reads += 1
        return {"account_id": account_id, "positions": [{"orderbook_id": "1", "volume": 8 if self.changed and self.portfolio_reads > 1 else 7, "price": 111 if self.quote_changed and self.portfolio_reads > 1 else 110}]}

    def transactions_snapshot(self, avanza, account_id, **kwargs):
        self.calls.append((account_id, kwargs))
        rows = []
        if kwargs["executed_only"]:
            rows = [{"id": "sale-fixture", "accountId": "other-account" if self.foreign_row else account_id, "tradeDate": "2026-05-09", "type": "SELL", "volume": {"value": 3, "unit": "ST"}, "orderbook": {"id": "1"}}]
            if self.duplicate_row:
                rows.append(deepcopy(rows[0]))
        return {"account_id": account_id, "transactions": [], "truncation_risk": self.truncated, "unparseable_date_rows_filtered": int(self.malformed_date), "raw_payload": {"transactions": rows}, "raw_scope": {"account_id": account_id, "exact_account_scope": self.scope_valid, "unidentified_rows_filtered": 0}}


class Broker:
    def __init__(self, currency="SEK", fail=False):
        self.currency = currency
        self.fail = fail

    def get_chart_data(self, orderbook_id, period, resolution):
        if self.fail:
            raise RuntimeError("fixture chart unavailable")
        return {"ohlc": [{"timestamp": "2026-05-06", "close": 100}, {"timestamp": "2026-05-08", "close": 110}]}

    def get_market_data(self, orderbook_id):
        return {"quote": {"currency": self.currency}}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(snapshots, "datetime", FixtureClock)


def run(provider, broker=None):
    return provider.frozen_holdings_attribution_snapshot(broker or Broker(), "fixture-account", "ONE_MONTH", start_date=date(2026, 5, 6), include_daily=True)


def test_provider_uses_inventory_day_and_exact_raw_account_scope():
    provider = Provider()
    result = run(provider)
    assert result["status"] == "COMPLETE"
    assert result["returns"]["frozen_starting_holdings_percent"] == pytest.approx(10)
    assert provider.portfolio_reads == 2
    for account_id, options in provider.calls:
        assert account_id == "fixture-account"
        assert options["transactions_to"] == date(2026, 5, 9)
        assert options["include_raw"] is True


@pytest.mark.parametrize("flag,issue", [
    ("changed", "portfolio_changed_during_reconstruction"),
    ("foreign_row", "transaction_account_mismatch"),
    ("duplicate_row", "duplicate_transaction_id"),
    ("malformed_date", "unparseable_transaction_dates"),
])
def test_provider_suppresses_benchmark_on_unverified_history(flag, issue):
    provider = Provider()
    setattr(provider, flag, True)
    result = run(provider)
    assert issue in {item["issue"] for item in result["issues"]}
    assert result["returns"]["frozen_starting_holdings_percent"] is None
    assert result["daily"] == []


def test_provider_price_movement_is_not_inventory_movement():
    provider = Provider()
    provider.quote_changed = True
    assert run(provider)["status"] == "COMPLETE"


def test_provider_fails_closed_without_raw_scope():
    provider = Provider()
    provider.scope_valid = False
    assert run(provider)["status"] == "BLOCKED_INCOMPLETE_HISTORY"


def test_provider_foreign_prices_require_historical_fx_not_current_quote():
    result = run(Provider(), Broker(currency="USD"))
    assert "missing_historical_fx" in {item["issue"] for item in result["issues"]}
    assert result["lineage"]["historical_fx_source"] is None
    assert result["returns"]["frozen_starting_holdings_percent"] is None


def test_provider_source_failure_never_leaks_partial_numeric_return():
    result = run(Provider(), Broker(fail=True))
    assert "chart_or_currency_source_error" in {item["issue"] for item in result["issues"]}
    assert result["reconstruction"]["start_cash_residual_sek"] is None
    assert result["daily"] == []


def test_provider_truncated_history_raises_before_reconstruction():
    provider = Provider()
    provider.truncated = True
    with pytest.raises(RuntimeError, match="20,000-row"):
        run(provider)
