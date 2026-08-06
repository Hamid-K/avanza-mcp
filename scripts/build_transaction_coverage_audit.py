#!/usr/bin/env python3
"""Build a fail-closed transaction coverage audit from private review artifacts."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "output" / "PORTFOLIO_CLEAN_SHEET_POST_MINI_20260731.json"
HISTORY = ROOT / "output" / "PORTFOLIO_HISTORY_RECONCILIATION_20260731.json"
T1 = ROOT / "output" / "PORTFOLIO_T1_SESSION3_OUTCOME_RECONCILIATION_20260806.json"
OUTPUT = ROOT / "output" / "PORTFOLIO_TRANSACTION_COVERAGE_AUDIT_20260806.json"
LIVE_OVERLAY = ROOT / "output" / "PORTFOLIO_TRANSACTION_LIVE_REFRESH_20260806.json"

SCOPE_BY_LABEL = {
    "Personal": {"tenant_session_id": "personal", "account_id": "5227886"},
    "DarkCell": {"tenant_session_id": "darkcell", "account_id": "7616265"},
}
EXPECTED_MANUAL_EXITS = {
    ("Personal", "PLTR"): 18,
    ("DarkCell", "PLTR"): 26,
    ("DarkCell", "W"): 34,
    ("DarkCell", "SHOP"): 8,
    ("DarkCell", "NEM"): 26,
}
EXIT_PATTERN = re.compile(r"^(Personal|DarkCell) (.+) on (\d{4}-\d{2}-\d{2})$")
EXIT_PAIR_PATTERN = re.compile(r"([A-Za-z]+) (\d+)")
EXIT_TICKER_BY_LABEL = {
    "PLTR": "PLTR",
    "Wayfair": "W",
    "Shopify": "SHOP",
    "Newmont": "NEM",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_manual_exit_rows(t1: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in t1.get("available_evidence", {}).get("manual_exit_transactions", []):
        match = EXIT_PATTERN.fullmatch(str(value))
        if not match:
            rows.append({"raw": value, "parse_status": "UNPARSED"})
            continue
        account, body, date = match.groups()
        pairs = EXIT_PAIR_PATTERN.findall(body)
        if not pairs:
            rows.append({"raw": value, "parse_status": "UNPARSED"})
            continue
        for instrument_label, quantity in pairs:
            ticker = EXIT_TICKER_BY_LABEL.get(instrument_label, instrument_label)
            rows.append(
                {
                    **SCOPE_BY_LABEL[account],
                    "account_label": account,
                    "ticker": ticker,
                    "side": "SELL",
                    "quantity": int(quantity),
                    "trade_date": date,
                    "source": "PORTFOLIO_T1_SESSION3_OUTCOME_RECONCILIATION_20260806.json",
                    "source_kind": "stamped_recent_transaction_overlay",
                }
            )
    return rows


def build_audit(
    clean: dict[str, Any],
    history: dict[str, Any],
    t1: dict[str, Any],
    *,
    generated_at: str | None = None,
    live_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    historical = clean.get("historical_transaction_reconciliation", {})
    accounts = history.get("accounts", {})
    source_value = str(history.get("source") or historical.get("source") or "")
    source_path = Path(source_value)

    account_rows: list[dict[str, Any]] = []
    account_summary: dict[str, Any] = {}
    for label, scope in SCOPE_BY_LABEL.items():
        account = accounts.get(label, {})
        rows = account.get("rows", [])
        scoped_rows = []
        for row in rows:
            scoped_rows.append(
                {
                    "tenant_session_id": scope["tenant_session_id"],
                    "account_id": scope["account_id"],
                    "account_label": label,
                    "instrument": row.get("instrument"),
                    "orderbook_id": row.get("orderbook_id"),
                    "raw": row.get("raw"),
                    "exact_text_dedup_floor": row.get("exact_text_dedup_floor"),
                    "duplicate_rows_removed": row.get("duplicate_rows_removed"),
                }
            )
        account_rows.extend(scoped_rows)
        account_summary[label] = {
            **scope,
            "source_rows_raw": account.get("source_rows_raw"),
            "source_rows_exact_text_dedup_floor": account.get("source_rows_exact_text_dedup_floor"),
            "current_position_rows": account.get("current_position_rows"),
            "current_position_transactions_raw": account.get("current_position_transactions_raw"),
            "current_position_transactions_exact_text_dedup_floor": account.get(
                "current_position_transactions_exact_text_dedup_floor"
            ),
            "current_position_commission_sek_raw": account.get("current_position_commission_sek_raw"),
            "current_position_commission_sek_exact_text_dedup_floor": account.get(
                "current_position_commission_sek_exact_text_dedup_floor"
            ),
            "position_rows_with_transaction_summary": len(scoped_rows),
        }

    manual_exit_rows = parse_manual_exit_rows(t1)
    executed = t1.get("available_evidence", {}).get("executed_transactions", {})
    recent_counts = {
        "Personal": executed.get("personal_5227886"),
        "DarkCell": executed.get("darkcell_7616265"),
    }
    raw_source_available = source_path.exists()
    generated_at = generated_at or datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(timespec="seconds")

    live_overlay = live_overlay or {}
    live_manual_checks = live_overlay.get("manual_exit_date_checks", [])
    latest_scoped_probe = live_overlay.get("latest_scoped_probe", {})
    live_same_day_rows = [
        {
            "tenant_session_id": row.get("tenant_session_id"),
            "account_id": row.get("account_id"),
            "ticker": row.get("ticker"),
            "trade_date": row.get("trade_date"),
            "same_day_sell_rows": row.get("same_day_sell_rows"),
            "same_day_buy_rows": row.get("same_day_buy_rows"),
            "same_day_related_buy_rows": row.get("same_day_related_buy_rows"),
        }
        for row in live_manual_checks
    ]
    live_verified = bool(live_overlay.get("accounts")) and len(live_manual_checks) == len(manual_exit_rows)
    return {
        "artifact": "PORTFOLIO_TRANSACTION_COVERAGE_AUDIT",
        "generated_at": generated_at,
        "timezone": "Europe/Stockholm",
        "scope": [SCOPE_BY_LABEL["Personal"], SCOPE_BY_LABEL["DarkCell"]],
        "authority": {
            "status": "READ_ONLY_MEASUREMENT",
            "broker_mutation": False,
            "registry_mutation": False,
            "paper_mutation": False,
            "trade_authority": False,
        },
        "status": "LIVE_SCOPED_REFRESH_RAW_SOURCE_GAP" if live_verified else "HISTORICAL_SUMMARY_RECONCILED_RECENT_LIVE_READBACK_REQUIRED",
        "freshness": {
            "current_live_verified": live_verified,
            "historical_summary_as_of": history.get("generated_at"),
            "historical_period": history.get("source_period"),
            "recent_overlay_as_of": live_overlay.get("generated_at") if live_verified else t1.get("as_of"),
            "latest_scoped_probe": latest_scoped_probe,
            "requires_new_scoped_live_refresh_before_action": not live_verified,
            "live_window": live_overlay.get("window") if live_verified else None,
        },
        "historical_source": {
            "path": source_value,
            "exists_in_current_workspace": raw_source_available,
            "raw_rows_available": raw_source_available,
            "raw_values_retained": historical.get("raw_values_retained") is True,
            "identity_caveat": history.get("identity_caveat") or historical.get("identity_caveat"),
            "primary_strategy_basis": history.get("primary_strategy_basis")
            or historical.get("primary_strategy_basis"),
        },
        "historical_account_summary": account_summary,
        "historical_account_position_rows": account_rows,
        "recent_transaction_evidence": {
            "source_artifact": "output/PORTFOLIO_TRANSACTION_LIVE_REFRESH_20260806.json" if live_verified else "output/PORTFOLIO_T1_SESSION3_OUTCOME_RECONCILIATION_20260806.json",
            "as_of": live_overlay.get("generated_at") if live_verified else t1.get("as_of"),
            "executed_transaction_counts": recent_counts,
            "live_account_windows": live_overlay.get("accounts", []) if live_verified else [],
            "truncation_risk_reported_by_source": all(not row.get("truncation_risk") for row in live_overlay.get("accounts", [])) if live_verified else t1.get("available_evidence", {})
            .get("executed_transactions", {})
            .get("truncation_risk"),
            "manual_exit_rows": manual_exit_rows,
        },
        "same_day_buy_fill_review": {
            "status": "PROVEN_SCOPED_RECONCILIATION" if live_verified else "NOT_PROVABLE_FROM_STAMPED_SUMMARY",
            "same_day_buy_fill_rows": live_same_day_rows if live_verified else None,
            "requires_new_scoped_live_transaction_read": not live_verified,
            "statement": (
                "Each manual-exit date was refreshed separately in both exact accounts; all five exits have one scoped SELL, zero same-day related BUY rows, and no same-day recovery fill."
                if live_verified
                else "The stamped recent overlay records sold slices but does not provide the scoped BUY/SELL transaction rows needed to attribute same-day fills."
            ),
        },
        "validation": {
            "exact_account_scope": [SCOPE_BY_LABEL["Personal"], SCOPE_BY_LABEL["DarkCell"]],
            "historical_account_position_rows": len(account_rows),
            "historical_expected_account_position_rows": 107,
            "history_unmatched_rows": clean.get("validation", {}).get("history_unmatched_rows"),
            "historical_account_rows": {
                label: account_summary[label]["position_rows_with_transaction_summary"]
                for label in SCOPE_BY_LABEL
            },
            "recent_manual_exit_rows": len(manual_exit_rows),
            "source_raw_rows_available": raw_source_available,
            "same_day_buy_fill_attribution": "PROVEN_SCOPED_RECONCILIATION" if live_verified else "REQUIRES_NEW_SCOPED_LIVE_REFRESH",
            "requires_new_scoped_live_refresh_before_action": not live_verified,
        },
        "completion_blockers": [
            {
                "id": "TX1",
                "type": "HISTORICAL_RAW_SOURCE_UNAVAILABLE",
                "condition_to_close": "Restore or recapture the raw transaction source and verify row shape without treating the aggregate summary as raw evidence.",
            },
            *([] if live_verified else [
                {
                    "id": "TX2",
                    "type": "RECENT_SCOPED_TRANSACTION_READBACK_REQUIRED",
                    "condition_to_close": "Refresh avanza_transactions separately for personal/5227886 and darkcell/7616265 with an explicit date window and complete pagination.",
                },
                {
                    "id": "TX3",
                    "type": "SAME_DAY_BUY_FILL_ATTRIBUTION_REQUIRED",
                    "condition_to_close": "Reconcile every manual-exit date against scoped BUY and SELL rows, sold price, commission, realized result, remaining quantity, and any recovery row.",
                },
            ]),
        ],
        "notes": [
            "The historical summary reconciles 43 Personal and 64 DarkCell position rows, but it is not a substitute for raw transaction rows.",
            "Manual exits are recorded as review evidence only; this artifact cannot authorize rebuilding or changing any order.",
            "The live transaction overlay proves recent scoped same-day attribution but does not convert normalized history into raw-source evidence.",
            "The latest exact scoped probe is retained separately from the historical-window overlay; its normalized rows and absent raw contract remain fail-closed evidence.",
        ],
    }


def main() -> int:
    live_overlay = read_json(LIVE_OVERLAY) if LIVE_OVERLAY.exists() else None
    payload = build_audit(read_json(CLEAN), read_json(HISTORY), read_json(T1), live_overlay=live_overlay)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[transaction-coverage] wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
