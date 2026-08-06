from scripts.build_transaction_coverage_audit import build_audit
from scripts.verify_transaction_coverage_artifacts import validate


def _source_payloads():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    clean = json.loads((root / "output/PORTFOLIO_CLEAN_SHEET_POST_MINI_20260731.json").read_text())
    history = json.loads((root / "output/PORTFOLIO_HISTORY_RECONCILIATION_20260731.json").read_text())
    t1 = json.loads((root / "output/PORTFOLIO_T1_SESSION3_OUTCOME_RECONCILIATION_20260806.json").read_text())
    return clean, history, t1


def test_transaction_audit_reconciles_exact_historical_scope_and_keeps_live_gate_open():
    payload = build_audit(*_source_payloads(), generated_at="2026-08-06T12:00:00+02:00")

    assert validate(payload) == []
    assert payload["validation"]["historical_account_position_rows"] == 107
    assert payload["freshness"]["current_live_verified"] is False


def test_transaction_audit_rejects_missing_manual_exit():
    payload = build_audit(*_source_payloads(), generated_at="2026-08-06T12:00:00+02:00")
    payload["recent_transaction_evidence"]["manual_exit_rows"].pop()

    errors = validate(payload)

    assert any("manual-exit coverage mismatch" in error for error in errors)


def test_transaction_audit_rejects_false_same_day_fill_certainty():
    payload = build_audit(*_source_payloads(), generated_at="2026-08-06T12:00:00+02:00")
    payload["same_day_buy_fill_review"]["status"] = "CLEAR"

    errors = validate(payload)

    assert any("same-day BUY-fill review must remain explicitly unproven" in error for error in errors)


def test_transaction_audit_accepts_current_scoped_refresh_but_keeps_raw_gap():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    overlay = json.loads((root / "output/PORTFOLIO_TRANSACTION_LIVE_REFRESH_20260806.json").read_text())
    payload = build_audit(*_source_payloads(), generated_at="2026-08-06T15:20:00+02:00", live_overlay=overlay)

    assert validate(payload) == []
    assert payload["status"] == "LIVE_SCOPED_REFRESH_RAW_SOURCE_GAP"
    assert payload["freshness"]["current_live_verified"] is True
    assert payload["same_day_buy_fill_review"]["status"] == "PROVEN_SCOPED_RECONCILIATION"
    assert {row["id"] for row in payload["completion_blockers"]} == {"TX1"}
