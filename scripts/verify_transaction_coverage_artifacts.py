#!/usr/bin/env python3
"""Verify transaction coverage is exact in scope and fail-closed when stale."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = ROOT / "output" / "PORTFOLIO_TRANSACTION_COVERAGE_AUDIT_20260806.json"
EXPECTED_SCOPE = [
    {"tenant_session_id": "personal", "account_id": "5227886"},
    {"tenant_session_id": "darkcell", "account_id": "7616265"},
]
EXPECTED_ACCOUNT_ROWS = {
    ("personal", "5227886"): 43,
    ("darkcell", "7616265"): 64,
}
EXPECTED_EXITS = {
    ("personal", "5227886", "PLTR"): 18,
    ("darkcell", "7616265", "PLTR"): 26,
    ("darkcell", "7616265", "W"): 34,
    ("darkcell", "7616265", "SHOP"): 8,
    ("darkcell", "7616265", "NEM"): 26,
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("artifact") != "PORTFOLIO_TRANSACTION_COVERAGE_AUDIT":
        errors.append("transaction coverage artifact id is missing")
    if payload.get("scope") != EXPECTED_SCOPE:
        errors.append("transaction coverage scope is not exactly personal/5227886 plus darkcell/7616265")
    authority = payload.get("authority", {})
    for field in ("broker_mutation", "registry_mutation", "paper_mutation", "trade_authority"):
        if authority.get(field) is not False:
            errors.append(f"transaction coverage authority {field} must remain false")
    if payload.get("status") not in {
        "HISTORICAL_SUMMARY_RECONCILED_RECENT_LIVE_READBACK_REQUIRED",
        "LIVE_SCOPED_REFRESH_RAW_SOURCE_GAP",
    }:
        errors.append("transaction coverage status is not a recognized fail-closed state")

    freshness = payload.get("freshness", {})
    current_live = payload.get("status") == "LIVE_SCOPED_REFRESH_RAW_SOURCE_GAP"
    if freshness.get("current_live_verified") is not current_live:
        errors.append("transaction freshness does not match its status")
    if freshness.get("requires_new_scoped_live_refresh_before_action") is not (not current_live):
        errors.append("transaction refresh gate does not match its status")

    summary = payload.get("historical_account_summary", {})
    for label, scope in (("Personal", EXPECTED_SCOPE[0]), ("DarkCell", EXPECTED_SCOPE[1])):
        row = summary.get(label, {})
        if row.get("tenant_session_id") != scope["tenant_session_id"] or row.get("account_id") != scope["account_id"]:
            errors.append(f"historical {label} summary lacks exact tenant/account scope")
        expected = EXPECTED_ACCOUNT_ROWS[(scope["tenant_session_id"], scope["account_id"])]
        if row.get("current_position_rows") != expected:
            errors.append(f"historical {label} current-position count is not {expected}")
        if row.get("position_rows_with_transaction_summary") != expected:
            errors.append(f"historical {label} transaction-summary coverage is not {expected}")

    scoped_rows = payload.get("historical_account_position_rows", [])
    if len(scoped_rows) != 107:
        errors.append("historical transaction summary must cover exactly 107 account-position rows")
    counts: dict[tuple[str, str], int] = {}
    for row in scoped_rows:
        key = (str(row.get("tenant_session_id")), str(row.get("account_id")))
        counts[key] = counts.get(key, 0) + 1
        for field in ("instrument", "orderbook_id", "raw", "exact_text_dedup_floor"):
            if row.get(field) in (None, ""):
                errors.append(f"historical transaction row missing {field}")
    if counts != EXPECTED_ACCOUNT_ROWS:
        errors.append(f"historical transaction scope counts are not {EXPECTED_ACCOUNT_ROWS}: {counts}")

    historical_source = payload.get("historical_source", {})
    if not historical_source.get("raw_values_retained"):
        errors.append("historical raw and conservative values are not both retained")
    if not historical_source.get("identity_caveat"):
        errors.append("transaction identity caveat is missing")
    if historical_source.get("raw_rows_available") is not False:
        errors.append("current audit must explicitly record that raw source availability is unverified")

    exits = payload.get("recent_transaction_evidence", {}).get("manual_exit_rows", [])
    actual_exits = {
        (row.get("tenant_session_id"), row.get("account_id"), row.get("ticker")): row.get("quantity")
        for row in exits
        if row.get("parse_status") != "UNPARSED"
    }
    if actual_exits != EXPECTED_EXITS:
        errors.append(f"manual-exit coverage mismatch: {actual_exits}")
    if len(exits) != 5:
        errors.append("recent manual-exit overlay must contain exactly five parsed rows")

    same_day = payload.get("same_day_buy_fill_review", {})
    if current_live:
        if same_day.get("status") != "PROVEN_SCOPED_RECONCILIATION":
            errors.append("current same-day BUY-fill review must be proven from scoped reads")
        rows = same_day.get("same_day_buy_fill_rows")
        if not isinstance(rows, list) or len(rows) != 5:
            errors.append("current same-day BUY-fill review must cover five manual exits")
        elif any(row.get("same_day_related_buy_rows") != 0 for row in rows):
            errors.append("current same-day BUY-fill review contains a related BUY")
        if same_day.get("requires_new_scoped_live_transaction_read") is not False:
            errors.append("current same-day BUY-fill review must not retain a refresh gate")
    else:
        if same_day.get("status") != "NOT_PROVABLE_FROM_STAMPED_SUMMARY":
            errors.append("same-day BUY-fill review must remain explicitly unproven")
        if same_day.get("same_day_buy_fill_rows") is not None:
            errors.append("same-day BUY-fill rows must not be invented from a summary artifact")
        if same_day.get("requires_new_scoped_live_transaction_read") is not True:
            errors.append("same-day BUY-fill review must require a new scoped transaction read")

    validation = payload.get("validation", {})
    if validation.get("historical_account_position_rows") != 107:
        errors.append("validation does not report 107 historical account-position rows")
    if validation.get("history_unmatched_rows") != 0:
        errors.append("historical unmatched-row count is not zero")
    if validation.get("requires_new_scoped_live_refresh_before_action") is not (not current_live):
        errors.append("validation live-refresh gate does not match current evidence")

    blockers = {str(row.get("id")) for row in payload.get("completion_blockers", [])}
    expected_blockers = ("TX1",) if current_live else ("TX1", "TX2", "TX3")
    for blocker in expected_blockers:
        if blocker not in blockers:
            errors.append(f"transaction completion blocker {blocker} is missing")
    for blocker in ("TX2", "TX3") if current_live else ():
        if blocker in blockers:
            errors.append(f"transaction completion blocker {blocker} should be closed after live refresh")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    path = args.audit if args.audit.is_absolute() else ROOT / args.audit
    if not path.exists():
        print(f"[transaction-coverage] missing: {path}")
        return 2
    errors = validate(_read(path))
    if errors:
        for error in errors:
            print(f"[transaction-coverage] FAIL: {error}")
        return 1
    print("[transaction-coverage] PASS: exact historical scope is reconciled and recent/live gaps remain fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
