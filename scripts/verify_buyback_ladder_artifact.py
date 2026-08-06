#!/usr/bin/env python3
"""Validate the local buyback-ladder presentation contract.

This is deliberately read-only. It checks that the rendered table cannot
silently promote broker inventory or stale templates into active ladders.
"""

from __future__ import annotations

import json
import argparse
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_LADDER_LIVE_REFRESH_20260806.json"
TABLE_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_LADDER_TABLE_20260806.md"
DAILY_COVERAGE_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_DAILY_COVERAGE_20260806.md"
DAILY_COVERAGE_JSON_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_DAILY_COVERAGE_20260806.json"
CANDIDATE_OVERLAY_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_CANDIDATE_LIVE_OVERLAY_20260806_0311.json"


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_staged_row(row: dict[str, Any], expected_volumes: tuple[int, ...]) -> list[str]:
    errors: list[str] = []
    key = (row.get("account_id"), row.get("ticker"))
    required = {
        "account_id",
        "tenant_session_id",
        "ticker",
        "instrument",
        "holding",
        "current_value_sek",
        "reference",
        "classification",
        "stages",
        "fx_presentation_rate_usd_sek",
        "promotion_gate",
    }
    _require(required.issubset(row), f"stock-specific ladder fields missing for {key}", errors)
    _require(str(row.get("reference", "")).strip() != "", f"reference missing for {key}", errors)
    _require("NOT_VALIDATED_LADDER" in str(row.get("classification", "")), f"ladder classification must remain unvalidated for {key}", errors)
    _require(bool(str(row.get("promotion_gate", "")).strip()), f"promotion gate missing for {key}", errors)

    stages = row.get("stages", [])
    _require(isinstance(stages, list) and 1 <= len(stages) <= 3, f"stage count must be 1-3 for {key}", errors)
    if not isinstance(stages, list) or not stages:
        return errors
    _require(tuple(stage.get("volume") for stage in stages) == expected_volumes, f"stage volumes do not match source contract for {key}", errors)
    pulls = [float(stage.get("pullback_percent", 0)) for stage in stages]
    _require(all(pull > 0 for pull in pulls), f"pullback percentages must be positive for {key}", errors)
    _require(pulls == sorted(pulls) and len(set(pulls)) == len(pulls), f"pullback percentages must increase by stage for {key}", errors)
    _require(10.0 <= pulls[-1] <= 15.0, f"final stage must be within 10-15% for {key}", errors)
    _require(
        all({"volume", "pullback_percent", "review_price_usd", "mark_implied_sek"}.issubset(stage) for stage in stages),
        f"stage price fields missing for {key}",
        errors,
    )
    if all("review_price_usd" in stage for stage in stages):
        prices = [float(stage["review_price_usd"]) for stage in stages]
        _require(prices == sorted(prices, reverse=True), f"review prices must decrease by stage for {key}", errors)
    return errors


def validate(plan: dict[str, Any], table: str) -> list[str]:
    errors: list[str] = []
    authority = plan.get("authority", {})
    guard = plan.get("render_guard", {})
    contract = plan.get("presentation_contract", {})
    latest_recovery = plan.get("latest_sold_slice_recovery_refresh", {})
    latest_inventory = plan.get("latest_active_buy_governance_audit", {})

    _require(authority.get("trade_authority") is False, "trade authority must be false", errors)
    _require(authority.get("broker_mutation") is False, "broker mutation must be false", errors)
    ladders = plan.get("validated_ladders")
    _require(isinstance(ladders, list), "validated_ladders must be a list", errors)
    _require(guard.get("current_source_of_truth") is not None, "render source must be declared", errors)
    _require(guard.get("stale_template_status") == "HISTORICAL_METADATA_ONLY", "stale templates must be historical-only", errors)
    _require(guard.get("stale_template_count") == 3, "unexpected stale-template count", errors)
    _require("validated_ladders" in contract.get("main_table_rule", ""), "main table rule must name validated ladders", errors)
    _require("active broker BUY rows are not printed as ladders" in guard.get("table_assertions", []), "active-row render assertion missing", errors)
    _require("sold-slice recovery is a separate queue" in guard.get("table_assertions", []), "sold-slice separation assertion missing", errors)
    _require("repair-needed floating rows are separate" in guard.get("table_assertions", []), "floating-child separation assertion missing", errors)
    _require(bool(latest_recovery.get("darkcell_newmont")), "Newmont sold-slice refresh missing", errors)
    _require(bool(latest_recovery.get("darkcell_shopify")), "Shopify sold-slice refresh missing", errors)
    _require(latest_inventory.get("active_buy_rows") == 46, "active BUY inventory count is not the recorded 46-row control", errors)
    _require(latest_inventory.get("validated_ladders") == 0, "inventory must record zero validated ladders", errors)
    _require("**Validated ladder:** none." in table, "table must state that no ladder is validated", errors)
    _require("## Live sold-slice recovery queue" in table, "sold-slice queue section missing", errors)
    _require("## Structures requiring repair, not ladder promotion" in table, "repair section missing", errors)
    _require("## Conditional-row control inventory" in table, "control inventory section missing", errors)
    _require("20%" not in table, "historical 20% template must not render in current table", errors)
    return errors


def validate_current_live_refresh(plan: dict[str, Any], table: str) -> list[str]:
    """Validate a successful exact refresh without granting trade authority."""

    errors: list[str] = []
    authority = plan.get("authority", {})
    controls = plan.get("live_control", {})
    contract = plan.get("render_contract", {})
    freshness = plan.get("freshness", {})
    staged = plan.get("dormant_staged_rebuilds", [])

    _require(authority.get("trade_authority") is False, "trade authority must be false", errors)
    _require(authority.get("broker_mutation") is False, "broker mutation must be false", errors)
    _require(authority.get("paper_mutation") is False, "paper mutation must be false", errors)
    _require(
        freshness.get("status") in {"CURRENT_LIVE_REFRESH", "LIVE_REFRESH_VERIFIED"},
        "current ladder refresh status must be explicitly live-verified",
        errors,
    )
    _require(freshness.get("live_state_current") is True, "current ladder refresh must mark live state current", errors)
    _require(freshness.get("live_refresh_verified") is True, "current ladder refresh must be verified", errors)
    _require(
        freshness.get("requires_new_scoped_live_refresh_before_action") is False,
        "current ladder refresh must clear the refresh gate",
        errors,
    )
    _require(
        freshness.get("last_refresh_attempt_status") in {"LIVE_REFRESH_VERIFIED", "RECORDED"},
        "current ladder refresh attempt status must be explicit",
        errors,
    )
    _require("Fresh exact live snapshot:" in table, "current table must label the exact live snapshot", errors)
    _require("Latest stamped source snapshot:" not in table, "current table must not retain a stamped-only header", errors)
    _require("## Snapshot controls (not current live)" not in table, "current table must not retain stale controls", errors)
    _require(contract.get("active_broker_rows_are_not_ladders") is True, "broker rows must remain separate", errors)
    _require(contract.get("historical_20_percent_templates") == "HISTORICAL_METADATA_ONLY", "historical templates must remain metadata-only", errors)
    _require(
        contract.get("validated_ladder_count") == len(contract.get("validated_ladders", [])),
        "validated ladder count/list mismatch",
        errors,
    )
    _require(controls.get("live_authorization") == {"personal": False, "darkcell": False}, "live authorization must be off", errors)
    _require(controls.get("raw_failed_orders") == {"personal": 0, "darkcell": 0}, "raw failed-order control is not zero", errors)
    _require(controls.get("protection_gaps") == {"personal": 0, "darkcell": 0}, "protection-gap control is not zero", errors)
    _require(isinstance(staged, list), "current dormant staged rebuilds must be a list", errors)
    for row in staged:
        stages = row.get("stages", []) if isinstance(row, dict) else []
        expected_volumes = tuple(stage.get("volume") for stage in stages) if isinstance(stages, list) else ()
        errors.extend(validate_staged_row(row, expected_volumes))
    return errors


def validate_live_refresh(plan: dict[str, Any], table: str) -> list[str]:
    """Validate the dated live-refresh schema used after the initial repair."""

    errors: list[str] = []
    authority = plan.get("authority", {})
    controls = plan.get("live_control", {})
    contract = plan.get("render_contract", {})
    staged = plan.get("dormant_staged_rebuilds", [])
    freshness = plan.get("freshness", {})

    if freshness.get("live_refresh_verified") is True:
        return validate_current_live_refresh(plan, table)

    _require(authority.get("trade_authority") is False, "trade authority must be false", errors)
    _require(authority.get("broker_mutation") is False, "broker mutation must be false", errors)
    _require(authority.get("paper_mutation") is False, "paper mutation must be false", errors)
    _require(freshness.get("status") == "STAMPED_REVIEW_SNAPSHOT", "ladder freshness must be stamped review-only", errors)
    _require(freshness.get("live_state_current") is False, "ladder source must not claim current live state", errors)
    _require(freshness.get("live_refresh_verified") is False, "ladder source must not claim verified live refresh", errors)
    _require(
        freshness.get("requires_new_scoped_live_refresh_before_action") is True,
        "ladder source must require a new scoped refresh before action",
        errors,
    )
    _require(freshness.get("last_refresh_attempt_status") == "SESSION_UNAVAILABLE", "ladder refresh failure state must be explicit", errors)
    _require("Latest stamped source snapshot:" in table, "table must label the source as stamped, not live", errors)
    _require("## Snapshot controls (not current live)" in table, "table must label controls as non-current", errors)
    _require("Fresh exact live snapshot:" not in table, "table must not claim a current live snapshot", errors)
    _require(contract.get("validated_ladders") == [], "validated ladder list must be empty until promotion", errors)
    _require(contract.get("validated_ladder_count") == 0, "validated ladder count must be zero", errors)
    _require(contract.get("active_broker_rows_are_not_ladders") is True, "broker rows must remain separate", errors)
    _require(contract.get("historical_20_percent_templates") == "HISTORICAL_METADATA_ONLY", "historical templates must be metadata-only", errors)
    _require(len(staged) == 8, "live source must contain exactly eight dormant staged rebuilds", errors)
    expected_stages = {
        ("5227886", "PLTR"): (6, 6, 6),
        ("7616265", "PLTR"): (8, 9, 9),
        ("7616265", "W"): (10, 12, 12),
        ("7616265", "NEM"): (8, 9, 9),
        ("5227886", "MRVL"): (2, 3, 4),
        ("5227886", "TER"): (2, 3, 4),
        ("7616265", "AKAM"): (1, 2, 3),
        ("7616265", "GMED"): (1, 1, 2),
    }
    for row in staged:
        key = (row.get("account_id"), row.get("ticker"))
        _require(key in expected_stages, f"unexpected dormant rebuild source row: {key}", errors)
        if key in expected_stages:
            errors.extend(validate_staged_row(row, expected_stages[key]))
    _require(controls.get("regular_open_orders") == {"personal": 0, "darkcell": 0}, "regular open-order control is not zero", errors)
    _require(controls.get("raw_failed_orders") == {"personal": 0, "darkcell": 0}, "raw failed-order control is not zero", errors)
    _require(controls.get("protection_gaps") == {"personal": 0, "darkcell": 0}, "protection-gap control is not zero", errors)
    _require(controls.get("live_authorization") == {"personal": False, "darkcell": False}, "live authorization must be off", errors)
    _require(controls.get("stop_audits") == {"personal": "28/28 recorded", "darkcell": "26/26 recorded"}, "stop-audit control is incomplete", errors)
    manual = {item.get("ticker"): item for item in plan.get("manual_exit_review", [])}
    _require(manual.get("PLTR", {}).get("classification") == "DORMANT_STAGED_REBUILD_NOT_VALIDATED_LADDER", "PLTR classification missing", errors)
    _require(manual.get("W", {}).get("classification") == "DORMANT_STAGED_REBUILD_NOT_VALIDATED_LADDER", "Wayfair classification missing", errors)
    _require(manual.get("NEM", {}).get("classification") == "REVIEW_SCAFFOLD_NOT_LADDER", "Newmont classification missing", errors)
    _require(manual.get("SHOP", {}).get("classification") == "MANUAL_EXIT_RECOVERY_REVIEW_NO_NEW_LADDER", "Shopify classification missing", errors)
    _require(len(plan.get("repair_needed", [])) == 3, "repair-needed floating-child inventory must contain three rows", errors)
    _require("## Dormant staged rebuilds" in table, "dormant rebuild section missing", errors)
    _require(
        ("## Manual exits without a ladder" in table or "## Manual exits and recovery separation" in table),
        "manual-exit section missing",
        errors,
    )
    _require("## Repair-needed floating children" in table, "repair section missing", errors)
    _require("Validated ladder:** none" in table, "table must state that no ladder is validated", errors)
    _require("A final approximately one-third stage may use" in table, "stock-specific final-stage policy must be visible", errors)
    _require("Ladder stages: volume @ % / USD / SEK" in table, "table must show percentage, native price, and SEK stage values", errors)
    _require("$149.84 / 1,428.62 SEK" in table, "Personal PLTR stage price missing from table", errors)
    _require("$101.33 / 966.06 SEK" in table, "Wayfair stage price missing from table", errors)
    _require("20%" not in table, "historical 20% template must not render in current table", errors)
    return errors


def validate_daily_coverage(table: str) -> list[str]:
    """Validate the recurring candidate-coverage ledger render."""

    errors: list[str] = []
    account_rows = [
        line for line in table.splitlines()
        if line.startswith("| Personal | ") or line.startswith("| DarkCell | ")
    ]
    personal_rows = [line for line in account_rows if line.startswith("| Personal | ")]
    darkcell_rows = [line for line in account_rows if line.startswith("| DarkCell | ")]
    _require(len(account_rows) == 44, f"daily coverage must contain 44 candidate rows, found {len(account_rows)}", errors)
    _require(len(personal_rows) == 18, f"daily coverage must contain 18 Personal rows, found {len(personal_rows)}", errors)
    _require(len(darkcell_rows) == 26, f"daily coverage must contain 26 DarkCell rows, found {len(darkcell_rows)}", errors)
    _require("## Coverage contract" in table, "daily coverage contract section missing", errors)
    _require("## Dormant stock-specific ladder designs currently visible" in table, "daily staged-ladder section missing", errors)
    _require("## Daily candidate coverage" in table, "daily candidate section missing", errors)
    _require("## Daily procedure" in table, "daily procedure section missing", errors)
    _require("LADDER_DORMANT" in table, "dormant ladder state missing", errors)
    _require("LEDGER_ONLY" in table, "ledger-only state missing", errors)
    _require("LADDER_GAP" in table, "ladder-gap state missing", errors)
    _require("REPAIR_REQUIRED" in table, "repair-required state missing", errors)
    _require("NAMED_EXCEPTION" in table, "named-exception state missing", errors)
    _require("Palantir (`PLTR`)" in table, "Personal PLTR row missing", errors)
    _require("Wayfair A (`W`)" in table, "Wayfair staged row missing", errors)
    _require("Newmont" in table and "Manual exit-1" in table, "Newmont manual exit-1 coverage missing", errors)
    _require("SpaceX" in table and "fresh exact SpaceX approval" in table, "SpaceX exception coverage missing", errors)
    _require("ordinary broker BUY row" in table and "not proof that a buyback ladder exists" in table, "broker-row separation rule missing", errors)
    _require("20%" not in table, "historical 20% template must not render in daily table", errors)
    _require("Current control state: live authorization off" in table, "daily live-control footer missing", errors)
    return errors


def validate_candidate_rows(rows: list[dict[str, Any]]) -> list[str]:
    """Require daily coverage and explicit promotion/rejection evidence."""

    errors: list[str] = []
    valid_states = {"LADDER_DORMANT", "LEDGER_ONLY", "LADDER_GAP", "REPAIR_REQUIRED", "NAMED_EXCEPTION"}
    for row in rows:
        key = (row.get("account_id"), row.get("ticker"))
        account_id = str(row.get("account_id", ""))
        expected_tenant = {"5227886": "personal", "7616265": "darkcell"}.get(account_id)
        _require(expected_tenant is not None, f"candidate account scope is invalid for {key}", errors)
        _require(row.get("tenant_session_id") == expected_tenant, f"candidate tenant scope mismatch for {key}", errors)
        _require(bool(str(row.get("instrument", "")).strip()), f"candidate instrument missing for {key}", errors)
        _require(bool(str(row.get("ticker", "")).strip()), f"candidate ticker missing for {key}", errors)
        _require(bool(str(row.get("orderbook_id", "")).strip()), f"candidate orderbook missing for {key}", errors)
        _require(isinstance(row.get("holding"), (int, float)) and row.get("holding", 0) >= 1, f"candidate holding invalid for {key}", errors)
        _require(isinstance(row.get("value_sek"), (int, float)) and row.get("value_sek", -1) >= 0, f"candidate value invalid for {key}", errors)
        _require(row.get("coverage_state") in valid_states, f"candidate coverage state invalid for {key}", errors)
        _require(bool(str(row.get("next_daily_evidence", "")).strip()), f"candidate next evidence missing for {key}", errors)
        promotion = str(row.get("promotion_evidence", "")).strip()
        rejection = str(row.get("rejection_evidence", "")).strip()
        _require(bool(promotion), f"candidate promotion evidence missing for {key}", errors)
        _require(bool(rejection), f"candidate rejection evidence missing for {key}", errors)
        promotion_markers = ("event", "catalyst", "higher low", "reclaim", "support", "range", "approval", "friction")
        rejection_markers = ("reject", "failed", "thesis", "friction", "capacity", "risk", "spread", "approval", "support", "reclaim")
        _require(any(marker in promotion.lower() for marker in promotion_markers), f"candidate promotion evidence is generic for {key}", errors)
        _require(any(marker in rejection.lower() for marker in rejection_markers), f"candidate rejection evidence is generic for {key}", errors)
    return errors


def validate_daily_coverage_json(table: str) -> list[str]:
    errors: list[str] = []
    if not DAILY_COVERAGE_JSON_PATH.exists():
        return [f"daily coverage JSON missing: {DAILY_COVERAGE_JSON_PATH}"]
    payload = json.loads(DAILY_COVERAGE_JSON_PATH.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    table_rows = [
        line for line in table.splitlines()
        if line.startswith("| Personal | ") or line.startswith("| DarkCell | ")
    ]
    _require(payload.get("artifact") == "PORTFOLIO_BUYBACK_DAILY_COVERAGE", "daily coverage JSON artifact id missing", errors)
    freshness = payload.get("freshness", {})
    current_live = freshness.get("live_refresh_verified") is True
    if current_live:
        _require(freshness.get("status") in {"CURRENT_LIVE_REFRESH", "LIVE_REFRESH_VERIFIED"}, "daily coverage live freshness must be explicit", errors)
        _require(freshness.get("live_state_current") is True, "daily coverage live state must be current", errors)
        _require(freshness.get("requires_new_scoped_live_refresh_before_action") is False, "daily coverage live refresh gate must be cleared", errors)
        _require("Fresh exact live snapshot:" in table, "daily table must label the exact live snapshot", errors)
    else:
        _require(freshness.get("status") == "STAMPED_REVIEW_SNAPSHOT", "daily coverage freshness must be explicitly stamped", errors)
        _require(freshness.get("live_state_current") is False, "daily coverage must not claim current live state", errors)
        _require(freshness.get("requires_new_scoped_live_refresh_before_action") is True, "fresh scoped refresh gate is missing", errors)
        _require("Stamped source snapshot:" in table, "daily table must label stamped evidence", errors)
    _require(payload.get("authority", {}).get("trade_authority") is False, "daily coverage JSON trade authority must be false", errors)
    _require(payload.get("authority", {}).get("broker_mutation") is False, "daily coverage JSON broker mutation must be false", errors)
    _require(len(rows) == len(table_rows) == 44, "daily coverage JSON/table row count mismatch", errors)
    _require(payload.get("candidate_universe", {}).get("count") == 44, "daily coverage JSON candidate count mismatch", errors)
    _require(payload.get("candidate_universe", {}).get("account_rows") == {"personal_5227886": 18, "darkcell_7616265": 26}, "daily coverage JSON account counts mismatch", errors)
    _require(payload.get("candidate_universe", {}).get("one_share_rows") == 42, "daily coverage JSON one-share count mismatch", errors)
    _require(payload.get("candidate_universe", {}).get("low_sek_rows") == 43, "daily coverage JSON low-SEK count mismatch", errors)
    _require(payload.get("candidate_universe", {}).get("without_active_buy_rows") == 14, "daily coverage JSON no-BUY count mismatch", errors)
    required_states = {"LADDER_DORMANT", "LEDGER_ONLY", "LADDER_GAP", "REPAIR_REQUIRED", "NAMED_EXCEPTION"}
    _require(required_states.issubset(set(payload.get("coverage_states", {}))), "daily coverage JSON state coverage incomplete", errors)
    _require(all({"account_id", "tenant_session_id", "ticker", "instrument", "orderbook_id", "holding", "value_sek", "existing_buy", "coverage_state", "next_daily_evidence", "promotion_evidence", "rejection_evidence"}.issubset(row) for row in rows), "daily coverage JSON row fields incomplete", errors)
    errors.extend(validate_candidate_rows(rows))
    _require(payload.get("live_controls", {}).get("live_authorization") == {"personal": False, "darkcell": False}, "daily coverage JSON authorization must be off", errors)
    return errors


def validate_candidate_live_overlay() -> list[str]:
    """Validate the latest live value overlay without promoting any row."""

    errors: list[str] = []
    if not CANDIDATE_OVERLAY_PATH.exists():
        return [f"candidate live overlay missing: {CANDIDATE_OVERLAY_PATH}"]
    payload = json.loads(CANDIDATE_OVERLAY_PATH.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    _require(payload.get("artifact") == "PORTFOLIO_BUYBACK_CANDIDATE_LIVE_OVERLAY", "candidate overlay artifact id missing", errors)
    _require(payload.get("authority", {}).get("trade_authority") is False, "candidate overlay trade authority must be false", errors)
    _require(payload.get("authority", {}).get("broker_mutation") is False, "candidate overlay broker mutation must be false", errors)
    _require(payload.get("authority", {}).get("paper_mutation") is False, "candidate overlay paper mutation must be false", errors)
    _require(payload.get("row_count") == 44, "candidate overlay row count must be 44", errors)
    _require(len(rows) == 44, "candidate overlay must contain 44 rows", errors)
    _require(
        {row.get("account_id") for row in rows} == {"5227886", "7616265"},
        "candidate overlay account scope mismatch",
        errors,
    )
    _require(sum(row.get("account_id") == "5227886" for row in rows) == 18, "candidate overlay Personal row count mismatch", errors)
    _require(sum(row.get("account_id") == "7616265" for row in rows) == 26, "candidate overlay DarkCell row count mismatch", errors)
    keys = [(row.get("account_id"), str(row.get("orderbook_id"))) for row in rows]
    _require(len(set(keys)) == 44, "candidate overlay contains duplicate account/orderbook rows", errors)
    valid_states = {"LADDER_DORMANT", "LEDGER_ONLY", "LADDER_GAP", "REPAIR_REQUIRED", "NAMED_EXCEPTION"}
    _require(all(row.get("state") in valid_states for row in rows), "candidate overlay contains an unknown coverage state", errors)
    _require(all(isinstance(row.get("holding"), (int, float)) and row.get("holding") >= 1 for row in rows), "candidate overlay holding values are invalid", errors)
    _require(all(isinstance(row.get("value_sek"), (int, float)) and row.get("value_sek") >= 0 for row in rows), "candidate overlay SEK values are invalid", errors)
    if DAILY_COVERAGE_JSON_PATH.exists():
        source = json.loads(DAILY_COVERAGE_JSON_PATH.read_text(encoding="utf-8"))
        source_keys = {(row.get("account_id"), str(row.get("orderbook_id"))) for row in source.get("rows", [])}
        _require(set(keys) == source_keys, "candidate overlay identity does not match daily coverage source", errors)
        _require(
            payload.get("coverage_state_counts") == source.get("coverage_states"),
            "candidate overlay coverage counts do not match daily coverage source",
            errors,
        )
    else:
        errors.append(f"daily coverage JSON missing for candidate overlay parity: {DAILY_COVERAGE_JSON_PATH}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--table", type=Path, default=TABLE_PATH)
    args = parser.parse_args()
    plan_path = args.plan if args.plan.is_absolute() else ROOT / args.plan
    table_path = args.table if args.table.is_absolute() else ROOT / args.table
    if not plan_path.exists() or not table_path.exists():
        missing = [str(path) for path in (plan_path, table_path) if not path.exists()]
        print("[buyback] missing: " + ", ".join(missing))
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    table = table_path.read_text(encoding="utf-8")
    errors = validate_live_refresh(plan, table) if "LIVE_REFRESH" in plan_path.name else validate(plan, table)
    if table_path == TABLE_PATH or table_path.name == "PORTFOLIO_BUYBACK_LADDER_TABLE_20260806.md":
        daily_path = DAILY_COVERAGE_PATH
        if not daily_path.exists():
            errors.append(f"daily coverage table missing: {daily_path}")
        else:
            daily_table = daily_path.read_text(encoding="utf-8")
            errors.extend(validate_daily_coverage(daily_table))
            errors.extend(validate_daily_coverage_json(daily_table))
            errors.extend(validate_candidate_live_overlay())
    if errors:
        for error in errors:
            print(f"[buyback] FAIL: {error}")
        return 1
    ladders = plan.get("validated_ladders", plan.get("render_contract", {}).get("validated_ladders", []))
    print(f"[buyback] PASS: {len(ladders)} validated ladders; broker inventory remains separately classified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
