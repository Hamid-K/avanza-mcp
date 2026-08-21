#!/usr/bin/env python3
"""Validate the local buyback-ladder presentation contract.

This is deliberately read-only. It checks that the rendered table cannot
silently promote broker inventory or stale templates into active ladders.
"""

from __future__ import annotations

import json
import argparse
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_LADDER_LIVE_REFRESH_20260806.json"
TABLE_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_LADDER_TABLE_20260806.md"
DAILY_COVERAGE_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_DAILY_COVERAGE_20260806.md"
DAILY_COVERAGE_JSON_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_DAILY_COVERAGE_20260806.json"
CANDIDATE_OVERLAY_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_CANDIDATE_LIVE_OVERLAY_20260806_0311.json"
DYNAMIC_LIVE_GLOB = "PORTFOLIO_BUYBACK_LIVE_COVERAGE_[0-9]*.json"
SOLD_MARKER_REMEDIATION_GLOB = "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE_[0-9]*.json"

DYNAMIC_BUYBACK_STATES = {
    "LADDER_ACTIVE",
    "LADDER_DORMANT",
    "LEDGER_ONLY",
    "LADDER_GAP",
    "REPAIR_REQUIRED",
    "NAMED_EXCEPTION",
}
DYNAMIC_LOW_EXPOSURE_STATES = {
    "BUILD_REVIEW",
    "INTENTIONAL_MARKER_OR_CORE_HOLD",
    "EXIT_OR_NO_REENTRY_REVIEW",
    "NAMED_EXCEPTION",
    "NON_STOP_ELIGIBLE",
    "REPAIR_REQUIRED",
}
EXPECTED_DYNAMIC_SCOPES = {
    ("personal", "5227886"),
    ("darkcell", "7616265"),
}


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def latest_dynamic_coverage_path() -> Path | None:
    """Return the latest dated live-universe artifact without a fixed row count."""

    paths = sorted((ROOT / "output").glob(DYNAMIC_LIVE_GLOB))
    return paths[-1] if paths else None


def latest_sold_marker_remediation_path() -> Path | None:
    """Return the newest complete-path sold-marker remediation overlay."""

    paths = sorted((ROOT / "output").glob(SOLD_MARKER_REMEDIATION_GLOB))
    return paths[-1] if paths else None


def _artifact_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _remediation_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("tenant_session_id") or ""),
        str(row.get("account_id") or ""),
        str(row.get("orderbook_id") or ""),
    )


def validate_sold_marker_remediation(payload: dict[str, Any]) -> list[str]:
    """Validate complete-path evidence without treating it as trade authority."""

    errors: list[str] = []
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    authority = payload.get("authority", {})
    controls = payload.get("controls", [])
    verification = payload.get("verification", {})

    _require(
        payload.get("artifact") == "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE",
        "sold-marker remediation artifact id missing",
        errors,
    )
    _require(bool(str(payload.get("generated_at") or "").strip()), "sold-marker remediation generated_at missing", errors)
    _require(bool(str(payload.get("path_snapshot_at") or "").strip()), "sold-marker remediation path snapshot missing", errors)
    _require(authority.get("broker_mutation") is False, "sold-marker remediation broker mutation must be false", errors)
    _require(authority.get("paper_mutation") is False, "sold-marker remediation paper mutation must be false", errors)
    _require(authority.get("trade_authority") is False, "sold-marker remediation trade authority must be false", errors)
    _require(isinstance(rows, list), "sold-marker remediation rows must be a list", errors)
    if not isinstance(rows, list):
        return errors

    keys = [_remediation_key(row) for row in rows if isinstance(row, dict)]
    _require(len(keys) == len(rows), "sold-marker remediation contains a non-object row", errors)
    _require(len(keys) == len(set(keys)), "sold-marker remediation contains duplicate account/orderbook rows", errors)
    _require(
        all(key[:2] in EXPECTED_DYNAMIC_SCOPES and key[2] for key in keys),
        "sold-marker remediation account scope is invalid",
        errors,
    )
    _require(
        any("PORTFOLIO_SOLD_MARKER_FULL_PATH_AUDIT_" in str(source) for source in payload.get("sources", [])),
        "sold-marker remediation must cite the complete-path source",
        errors,
    )

    repair_rows = [row for row in rows if str(row.get("state") or "").startswith("REPAIR_REQUIRED")]
    percentage_gap_rows = [row for row in rows if row.get("state") == "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET"]
    partial_rows = [
        row for row in rows
        if str(row.get("state") or "").startswith("PARTIAL_SOLD_SLICE_RECOVERY")
    ]
    no_reentry_rows = [row for row in rows if row.get("state") == "EXPLICIT_NO_REENTRY_CURRENT_THESIS"]
    open_rows = [row for row in rows if int(row.get("remaining_open_quantity", 0) or 0) > 0]
    remaining_quantity = sum(int(row.get("remaining_open_quantity", 0) or 0) for row in rows)

    _require(
        summary.get("repair_required_missed_path_rows") == len(repair_rows),
        "sold-marker remediation repair count mismatch",
        errors,
    )
    _require(
        summary.get("percentage_not_set_open_rows") == len(percentage_gap_rows),
        "sold-marker remediation PERCENTAGE_NOT_SET count mismatch",
        errors,
    )
    _require(
        summary.get("partial_sale_attributed_active_rows") == len(partial_rows),
        "sold-marker remediation partial-attribution count mismatch",
        errors,
    )
    _require(
        summary.get("explicit_no_reentry_rows") == len(no_reentry_rows),
        "sold-marker remediation no-reentry count mismatch",
        errors,
    )
    _require(summary.get("open_material_rows") == len(open_rows), "sold-marker remediation open-row count mismatch", errors)
    _require(
        summary.get("remaining_open_quantity_across_material_rows") == remaining_quantity,
        "sold-marker remediation remaining quantity mismatch",
        errors,
    )
    _require(
        isinstance(summary.get("exact_account_rows_with_prior_same_account_sales"), int)
        and summary.get("exact_account_rows_with_prior_same_account_sales", 0) >= len(rows),
        "sold-marker remediation full-universe count is invalid",
        errors,
    )
    _require(
        summary.get("all_path_active_buy_attribution_gaps_after_registry_correction") == 0,
        "sold-marker remediation retains active-BUY attribution gaps",
        errors,
    )
    _require(
        summary.get("silent_active_buy_attribution_gaps_in_material_rows") == 0,
        "sold-marker remediation retains silent material attribution gaps",
        errors,
    )
    _require(summary.get("broker_mutations") == 0, "sold-marker remediation must record zero broker mutations", errors)

    control_text = " ".join(str(control).lower() for control in controls if isinstance(control, str))
    required_control_phrases = {
        "complete authenticated price path": "complete-path control missing",
        "rebound never erases": "rebound-persistence control missing",
        "durable metadata identifies the exact account": "exact-attribution control missing",
        "percentage_not_set is fail-closed": "PERCENTAGE_NOT_SET fail-closed control missing",
        "8 percent sold-marker drawdown is a mandatory review alarm": "8 percent review-alarm control missing",
        "do not chase a rebound": "no-rebound-chasing control missing",
    }
    for phrase, message in required_control_phrases.items():
        _require(phrase in control_text, message, errors)

    repair_ids_by_tenant = {
        tenant: sorted(
            str(row.get("orderbook_id"))
            for row in repair_rows
            if row.get("tenant_session_id") == tenant
        )
        for tenant in ("personal", "darkcell")
    }
    for tenant, account in (("personal", "5227886"), ("darkcell", "7616265")):
        proof = verification.get(tenant, {})
        _require(proof.get("tenant_session_id") == tenant, f"sold-marker {tenant} tenant proof missing", errors)
        _require(proof.get("account_id") == account, f"sold-marker {tenant} account proof missing", errors)
        _require(proof.get("session_authenticated") is True, f"sold-marker {tenant} session not authenticated", errors)
        _require(proof.get("live_authorization_off") is True, f"sold-marker {tenant} authorization must be off", errors)
        _require(
            proof.get("recovery_reachability_unresolved") == 0,
            f"sold-marker {tenant} recovery reachability is unresolved",
            errors,
        )
        _require(
            sorted(str(value) for value in proof.get("position_repair_required_orderbook_ids", []))
            == repair_ids_by_tenant[tenant],
            f"sold-marker {tenant} repair identities do not reconcile",
            errors,
        )
    return errors


def sold_marker_dynamic_reconciliation_rows(
    dynamic_payload: dict[str, Any],
    remediation_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build exact rows retained in the goal audit for independent verification."""

    dynamic_rows = {
        _remediation_key(row): row
        for row in dynamic_payload.get("rows", [])
        if isinstance(row, dict)
    }
    result: list[dict[str, Any]] = []
    for recovery in remediation_payload.get("rows", []):
        if not isinstance(recovery, dict):
            continue
        key = _remediation_key(recovery)
        dynamic = dynamic_rows.get(key, {})
        result.append({
            "tenant_session_id": key[0],
            "account_id": key[1],
            "orderbook_id": key[2],
            "instrument": recovery.get("instrument"),
            "sale_date": recovery.get("sale_date"),
            "sold_quantity": recovery.get("sold_quantity"),
            "remaining_open_quantity": recovery.get("remaining_open_quantity"),
            "sale_attributed_active_buy_quantity": recovery.get("sale_attributed_active_buy_quantity"),
            "recovery_state": recovery.get("state"),
            "recovery_recorded_stage_percentages_below_marker": recovery.get(
                "recorded_stage_percentages_below_marker"
            ),
            "recovery_recorded_stage_quantities": recovery.get("recorded_stage_quantities"),
            "dynamic_row_found": bool(dynamic),
            "dynamic_buyback_coverage_state": dynamic.get("buyback_coverage_state"),
            "dynamic_low_exposure_decision": dynamic.get("low_exposure_decision"),
            "dynamic_protection_classification": dynamic.get("current_protection_classification"),
            "dynamic_active_buy_volume": dynamic.get("active_buy_volume"),
            "dynamic_target_rebuild_quantity": dynamic.get("target_rebuild_quantity"),
            "dynamic_stages_percent_below_sold_marker": dynamic.get("stages_percent_below_sold_marker"),
            "dynamic_stage_quantities": dynamic.get("stage_quantities"),
            "dynamic_coverage_reason": dynamic.get("coverage_reason"),
        })
    return result


def _dormant_ladder_governance_gaps(row: dict[str, Any]) -> list[str]:
    """Return fail-closed defects for an open, fully governed dormant ladder."""

    gaps: list[str] = []
    remaining = int(row.get("remaining_open_quantity", 0) or 0)
    target = row.get("dynamic_target_rebuild_quantity")
    stages = row.get("dynamic_stages_percent_below_sold_marker")
    quantities = row.get("dynamic_stage_quantities")
    recorded_stages = row.get("recovery_recorded_stage_percentages_below_marker")
    recorded_quantities = row.get("recovery_recorded_stage_quantities")

    if row.get("dynamic_buyback_coverage_state") != "LADDER_DORMANT":
        gaps.append("dynamic state is not LADDER_DORMANT")
    if row.get("dynamic_low_exposure_decision") != "BUILD_REVIEW":
        gaps.append("low-exposure decision is not BUILD_REVIEW")
    if row.get("dynamic_protection_classification") == "REPAIR_REQUIRED":
        gaps.append("protection classification remains REPAIR_REQUIRED")
    if not isinstance(target, (int, float)) or float(target) != remaining:
        gaps.append("target rebuild quantity does not equal the remaining sold slice")
    if not isinstance(stages, list) or not 1 <= len(stages) <= 3:
        gaps.append("stage percentages are not a one-to-three-stage list")
    elif (
        any(not isinstance(value, (int, float)) or float(value) <= 0 for value in stages)
        or any(float(left) >= float(right) for left, right in zip(stages, stages[1:]))
    ):
        gaps.append("stage percentages are not positive and strictly increasing")
    if not isinstance(quantities, list) or not isinstance(stages, list) or len(quantities) != len(stages):
        gaps.append("stage quantities do not align with stage percentages")
    elif (
        any(not isinstance(value, (int, float)) or int(value) <= 0 or float(value) != int(value) for value in quantities)
        or sum(int(value) for value in quantities) != remaining
    ):
        gaps.append("stage quantities do not exactly cover the remaining sold slice")
    if recorded_stages != stages:
        gaps.append("dynamic percentages do not match the authenticated recovery record")
    if recorded_quantities != quantities:
        gaps.append("dynamic quantities do not match the authenticated recovery record")
    return gaps


def sold_marker_governance_gap_rows(
    reconciliation_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only unresolved governance rows, not every unfilled dormant ladder."""

    result: list[dict[str, Any]] = []
    for row in reconciliation_rows:
        if not isinstance(row, dict):
            result.append({"governance_gap_reasons": ["reconciliation row is not an object"]})
            continue
        state = str(row.get("recovery_state") or "")
        remaining = int(row.get("remaining_open_quantity", 0) or 0)
        reasons: list[str] = []
        if row.get("dynamic_row_found") is not True:
            reasons.append("dynamic buyback row is missing")
        if row.get("dynamic_active_buy_volume") != row.get("sale_attributed_active_buy_quantity"):
            reasons.append("active BUY attribution does not match the sold-slice record")

        if state.startswith("REPAIR_REQUIRED"):
            reasons.append("sold-marker path remains REPAIR_REQUIRED")
        elif state == "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET":
            reasons.append("material sold-marker path remains PERCENTAGE_NOT_SET")
        elif state.startswith("PARTIAL_SOLD_SLICE_RECOVERY") and remaining > 0:
            reasons.append("partial recovery retains an uncovered sold-slice remainder")
        elif state == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED":
            reasons.extend(_dormant_ladder_governance_gaps(row))
        elif state == "EXPLICIT_NO_REENTRY_CURRENT_THESIS":
            reason = str(row.get("dynamic_coverage_reason") or "").lower()
            if (
                remaining != 0
                or row.get("dynamic_buyback_coverage_state") != "LEDGER_ONLY"
                or row.get("dynamic_active_buy_volume") != 0
                or row.get("dynamic_stages_percent_below_sold_marker") != "PERCENTAGE_NOT_SET"
                or ("no-reentry" not in reason and "no re-entry" not in reason)
            ):
                reasons.append("explicit no-reentry state is incomplete or contradicted")
        elif remaining > 0:
            reasons.append("open sold-slice state has no recognized governed resolution")

        if reasons:
            item = dict(row)
            item["governance_gap_reasons"] = list(dict.fromkeys(reasons))
            result.append(item)
    return result


def validate_dynamic_against_sold_marker_recovery(
    dynamic_payload: dict[str, Any],
    remediation_payload: dict[str, Any],
) -> list[str]:
    """Prevent a latest quote or rebound from hiding an earlier unserved path."""

    errors = validate_sold_marker_remediation(remediation_payload)
    dynamic_errors = validate_dynamic_live_coverage(dynamic_payload)
    errors.extend(dynamic_errors)
    dynamic_generated = _artifact_time(dynamic_payload.get("generated_at"))
    remediation_generated = _artifact_time(remediation_payload.get("generated_at"))
    _require(dynamic_generated is not None, "dynamic buyback generated_at is invalid", errors)
    _require(remediation_generated is not None, "sold-marker remediation generated_at is invalid", errors)
    if dynamic_generated is not None and remediation_generated is not None:
        _require(
            dynamic_generated >= remediation_generated,
            "dynamic buyback coverage predates the authoritative sold-marker remediation",
            errors,
        )

    rows = sold_marker_dynamic_reconciliation_rows(dynamic_payload, remediation_payload)
    for row in rows:
        key = (row["tenant_session_id"], row["account_id"], row["orderbook_id"])
        state = str(row.get("recovery_state") or "")
        _require(row.get("dynamic_row_found") is True, f"dynamic buyback row missing for sold-marker recovery {key}", errors)
        if not row.get("dynamic_row_found"):
            continue
        _require(
            row.get("dynamic_active_buy_volume") == row.get("sale_attributed_active_buy_quantity"),
            f"dynamic active BUY attribution mismatch for sold-marker recovery {key}",
            errors,
        )
        reason = str(row.get("dynamic_coverage_reason") or "").lower()
        if state.startswith("REPAIR_REQUIRED"):
            _require(
                row.get("dynamic_buyback_coverage_state") == "REPAIR_REQUIRED"
                and row.get("dynamic_low_exposure_decision") == "REPAIR_REQUIRED"
                and row.get("dynamic_protection_classification") == "REPAIR_REQUIRED",
                f"missed sold-marker path is not REPAIR_REQUIRED in dynamic coverage for {key}",
                errors,
            )
            _require(
                row.get("dynamic_target_rebuild_quantity") == row.get("remaining_open_quantity"),
                f"repair target does not preserve exact open sold quantity for {key}",
                errors,
            )
        elif state == "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET":
            _require(
                row.get("dynamic_buyback_coverage_state") == "LADDER_GAP",
                f"unsupported material sold-marker path is not LADDER_GAP for {key}",
                errors,
            )
            _require(
                row.get("dynamic_stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET",
                f"unsupported material sold-marker path invented percentages for {key}",
                errors,
            )
            _require(
                row.get("dynamic_target_rebuild_quantity") == row.get("remaining_open_quantity"),
                f"material gap target does not preserve exact open sold quantity for {key}",
                errors,
            )
        elif state.startswith("PARTIAL_SOLD_SLICE_RECOVERY"):
            _require(
                row.get("dynamic_buyback_coverage_state") == "LEDGER_ONLY"
                and row.get("dynamic_stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET",
                f"partial sold-slice row must remain explicit ledger-only coverage for {key}",
                errors,
            )
            _require(
                row.get("dynamic_target_rebuild_quantity") == row.get("sold_quantity"),
                f"partial sold-slice target does not preserve exact sold quantity for {key}",
                errors,
            )
            _require(
                "sale-attributed" in reason and str(row.get("sale_date") or "") in reason,
                f"partial sold-slice provenance is missing from dynamic coverage for {key}",
                errors,
            )
        elif state == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED":
            for gap in _dormant_ladder_governance_gaps(row):
                errors.append(f"dormant sold-marker ladder is not fully governed for {key}: {gap}")
        elif state == "EXPLICIT_NO_REENTRY_CURRENT_THESIS":
            _require(
                row.get("dynamic_buyback_coverage_state") == "LEDGER_ONLY"
                and row.get("dynamic_active_buy_volume") == 0
                and row.get("dynamic_stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET",
                f"explicit no-reentry row is contradicted by dynamic coverage for {key}",
                errors,
            )
            _require(
                "no-reentry" in reason or "no re-entry" in reason,
                f"explicit no-reentry reason is missing from dynamic coverage for {key}",
                errors,
            )
    return errors


def _count_states(rows: list[dict[str, Any]], field: str, states: set[str]) -> dict[str, int]:
    counts = Counter(str(row.get(field) or "") for row in rows)
    return {state: counts.get(state, 0) for state in sorted(states)}


def _normalized_state_summary(value: Any, states: set[str]) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {state: int(source.get(state, 0) or 0) for state in sorted(states)}


def validate_dynamic_live_coverage(payload: dict[str, Any]) -> list[str]:
    """Validate current dynamic buyback governance without granting order authority."""

    errors: list[str] = []
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    governance = payload.get("live_governance", {})
    output_contract = payload.get("user_facing_output_contract", {})

    _require(
        payload.get("artifact") == "PORTFOLIO_BUYBACK_LIVE_COVERAGE",
        "dynamic buyback artifact id missing",
        errors,
    )
    _require(payload.get("authority") == "REVIEW_ONLY", "dynamic buyback authority must be REVIEW_ONLY", errors)
    _require(payload.get("broker_mutation_authorized") is False, "dynamic buyback broker mutation must be false", errors)
    _require(bool(str(payload.get("generated_at") or "").strip()), "dynamic buyback generated_at missing", errors)
    _require(bool(str(payload.get("live_state_as_of") or "").strip()), "dynamic buyback live_state_as_of missing", errors)
    _require(
        "No fixed historical candidate count" in str(payload.get("universe_contract") or ""),
        "dynamic universe contract must reject fixed historical counts",
        errors,
    )
    _require(output_contract.get("percentage_only") is True, "dynamic output must be percentage-only", errors)
    _require(output_contract.get("raw_prices_prohibited") is True, "dynamic output must prohibit raw prices", errors)
    _require(output_contract.get("raw_triggers_prohibited") is True, "dynamic output must prohibit raw triggers", errors)
    _require(
        output_contract.get("monetary_order_values_prohibited") is True,
        "dynamic output must prohibit monetary order values",
        errors,
    )
    _require(
        output_contract.get("unsupported_stages") == "PERCENTAGE_NOT_SET",
        "dynamic unsupported-stage marker must be PERCENTAGE_NOT_SET",
        errors,
    )

    scopes = {
        (str(row.get("tenant_session_id") or ""), str(row.get("account_id") or ""))
        for row in payload.get("scope", [])
        if isinstance(row, dict)
    }
    _require(scopes == EXPECTED_DYNAMIC_SCOPES, "dynamic buyback scope must contain both exact accounts", errors)
    _require(governance.get("sessions_verified") is True, "dynamic buyback sessions must be verified", errors)
    _require(
        governance.get("authorization_off") == {"personal": True, "darkcell": True},
        "dynamic buyback live authorization must be off for both tenants",
        errors,
    )
    _require(
        governance.get("personal_unresolved_position_drift") == 0
        and governance.get("darkcell_unresolved_position_drift") == 0,
        "dynamic buyback position drift must be zero",
        errors,
    )
    _require(isinstance(rows, list) and bool(rows), "dynamic buyback rows must be a non-empty list", errors)
    if not isinstance(rows, list):
        return errors

    keys: list[tuple[str, str, str]] = []
    supported_count = 0
    percentage_not_set_count = 0
    vectors_by_instrument: dict[tuple[float, ...], set[str]] = defaultdict(set)
    required_fields = {
        "tenant_session_id",
        "account_id",
        "instrument",
        "orderbook_id",
        "live_holding",
        "selection_reasons",
        "active_buy_volume",
        "active_sell_volume",
        "current_protection_classification",
        "low_exposure_decision",
        "buyback_coverage_state",
        "stages_percent_below_sold_marker",
        "coverage_reason",
        "exact_next_gate",
    }
    for row in rows:
        if not isinstance(row, dict):
            errors.append("dynamic buyback contains a non-object row")
            continue
        tenant = str(row.get("tenant_session_id") or "")
        account = str(row.get("account_id") or "")
        orderbook = str(row.get("orderbook_id") or "")
        instrument = str(row.get("instrument") or "").strip()
        key = (tenant, account, orderbook)
        keys.append(key)
        _require(required_fields.issubset(row), f"dynamic buyback row fields missing for {key}", errors)
        _require((tenant, account) in EXPECTED_DYNAMIC_SCOPES, f"dynamic buyback account scope invalid for {key}", errors)
        _require(bool(orderbook), f"dynamic buyback orderbook missing for {key}", errors)
        _require(bool(instrument), f"dynamic buyback instrument missing for {key}", errors)
        _require(
            isinstance(row.get("live_holding"), (int, float)) and row.get("live_holding", -1) >= 0,
            f"dynamic buyback holding invalid for {key}",
            errors,
        )
        _require(
            isinstance(row.get("active_buy_volume"), (int, float)) and row.get("active_buy_volume", -1) >= 0,
            f"dynamic buyback active BUY volume invalid for {key}",
            errors,
        )
        _require(
            isinstance(row.get("active_sell_volume"), (int, float)) and row.get("active_sell_volume", -1) >= 0,
            f"dynamic buyback active SELL volume invalid for {key}",
            errors,
        )
        reasons = row.get("selection_reasons")
        _require(isinstance(reasons, list) and bool(reasons), f"dynamic buyback selection reasons missing for {key}", errors)
        _require(
            row.get("buyback_coverage_state") in DYNAMIC_BUYBACK_STATES,
            f"dynamic buyback state invalid for {key}",
            errors,
        )
        _require(
            row.get("low_exposure_decision") in DYNAMIC_LOW_EXPOSURE_STATES,
            f"dynamic low-exposure decision invalid for {key}",
            errors,
        )
        _require(
            bool(str(row.get("current_protection_classification") or "").strip()),
            f"dynamic protection classification missing for {key}",
            errors,
        )
        _require(bool(str(row.get("coverage_reason") or "").strip()), f"dynamic coverage reason missing for {key}", errors)
        _require(bool(str(row.get("exact_next_gate") or "").strip()), f"dynamic exact next gate missing for {key}", errors)

        stages = row.get("stages_percent_below_sold_marker")
        quantities = row.get("stage_quantities")
        state = row.get("buyback_coverage_state")
        if stages == "PERCENTAGE_NOT_SET":
            percentage_not_set_count += 1
            _require(quantities is None, f"unsupported ladder must not carry stage quantities for {key}", errors)
            _require(
                state not in {"LADDER_ACTIVE", "LADDER_DORMANT"},
                f"active or dormant ladder lacks supported percentages for {key}",
                errors,
            )
            continue

        _require(isinstance(stages, list) and 1 <= len(stages) <= 3, f"dynamic ladder must have 1-3 stages for {key}", errors)
        if not isinstance(stages, list) or not stages:
            continue
        supported_count += 1
        _require(
            all(isinstance(value, (int, float)) and value > 0 for value in stages),
            f"dynamic ladder percentages must be positive for {key}",
            errors,
        )
        numeric_stages = tuple(float(value) for value in stages if isinstance(value, (int, float)))
        _require(
            len(numeric_stages) == len(stages)
            and list(numeric_stages) == sorted(numeric_stages)
            and len(set(numeric_stages)) == len(numeric_stages),
            f"dynamic ladder percentages must increase strictly for {key}",
            errors,
        )
        vectors_by_instrument[numeric_stages].add(orderbook)
        target = row.get("target_rebuild_quantity")
        _require(
            isinstance(target, (int, float)) and target > 0,
            f"supported dynamic ladder target quantity missing for {key}",
            errors,
        )
        if quantities is not None:
            _require(
                isinstance(quantities, list)
                and len(quantities) == len(stages)
                and all(isinstance(value, int) and value > 0 for value in quantities),
                f"dynamic ladder stage quantities invalid for {key}",
                errors,
            )
            if isinstance(quantities, list) and all(isinstance(value, int) for value in quantities):
                _require(sum(quantities) == target, f"dynamic ladder quantities do not equal target for {key}", errors)
        if state == "LADDER_ACTIVE":
            _require(row.get("active_buy_volume", 0) > 0, f"active dynamic ladder has no active BUY for {key}", errors)

    _require(len(keys) == len(set(keys)), "dynamic buyback contains duplicate account/orderbook rows", errors)
    for vector, orderbooks in vectors_by_instrument.items():
        _require(
            len(orderbooks) == 1,
            f"dynamic ladder vector {list(vector)} is duplicated across different instruments",
            errors,
        )

    personal_rows = sum(row.get("account_id") == "5227886" for row in rows if isinstance(row, dict))
    darkcell_rows = sum(row.get("account_id") == "7616265" for row in rows if isinstance(row, dict))
    one_share_rows = sum(row.get("live_holding") == 1 for row in rows if isinstance(row, dict))
    below_20000_rows = sum(
        row.get("market_value_band") == "BELOW_20000_SEK"
        for row in rows
        if isinstance(row, dict)
    )
    full_exit_rows = sum(
        "FULL_EXIT" in row.get("selection_reasons", [])
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("selection_reasons"), list)
    )
    pending_rows = sum(bool(row.get("pending_cleanup_id")) for row in rows if isinstance(row, dict))
    _require(summary.get("exact_account_rows") == len(rows), "dynamic summary exact row count mismatch", errors)
    _require(summary.get("personal_rows") == personal_rows, "dynamic summary Personal count mismatch", errors)
    _require(summary.get("darkcell_rows") == darkcell_rows, "dynamic summary DarkCell count mismatch", errors)
    _require(personal_rows + darkcell_rows == len(rows), "dynamic rows contain an unexpected account", errors)
    _require(summary.get("current_one_share_rows") == one_share_rows, "dynamic summary one-share count mismatch", errors)
    _require(summary.get("below_20000_sek_rows") == below_20000_rows, "dynamic summary low-exposure count mismatch", errors)
    _require(summary.get("full_exit_rows") == full_exit_rows, "dynamic summary full-exit count mismatch", errors)
    _require(
        _normalized_state_summary(summary.get("buyback_coverage_state_counts"), DYNAMIC_BUYBACK_STATES)
        == _count_states(rows, "buyback_coverage_state", DYNAMIC_BUYBACK_STATES),
        "dynamic buyback state counts mismatch",
        errors,
    )
    _require(
        _normalized_state_summary(summary.get("low_exposure_decision_counts"), DYNAMIC_LOW_EXPOSURE_STATES)
        == _count_states(rows, "low_exposure_decision", DYNAMIC_LOW_EXPOSURE_STATES),
        "dynamic low-exposure decision counts mismatch",
        errors,
    )
    _require(
        summary.get("percentage_ladders_with_supported_stages") == supported_count,
        "dynamic supported-ladder count mismatch",
        errors,
    )
    _require(
        summary.get("percentage_not_set_rows") == percentage_not_set_count,
        "dynamic PERCENTAGE_NOT_SET count mismatch",
        errors,
    )
    _require(supported_count + percentage_not_set_count == len(rows), "dynamic percentage coverage is incomplete", errors)
    _require(summary.get("pending_r6a_cleanup_rows") == pending_rows, "dynamic pending-cleanup count mismatch", errors)
    return errors


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
        dynamic_path = latest_dynamic_coverage_path()
        if dynamic_path is None:
            errors.append(f"dynamic live coverage missing for glob: {DYNAMIC_LIVE_GLOB}")
        else:
            dynamic_payload = json.loads(dynamic_path.read_text(encoding="utf-8"))
            remediation_path = latest_sold_marker_remediation_path()
            if remediation_path is None:
                errors.append(f"sold-marker remediation missing for glob: {SOLD_MARKER_REMEDIATION_GLOB}")
            else:
                remediation_payload = json.loads(remediation_path.read_text(encoding="utf-8"))
                errors.extend(
                    validate_dynamic_against_sold_marker_recovery(
                        dynamic_payload,
                        remediation_payload,
                    )
                )
    if errors:
        for error in errors:
            print(f"[buyback] FAIL: {error}")
        return 1
    ladders = plan.get("validated_ladders", plan.get("render_contract", {}).get("validated_ladders", []))
    dynamic_path = latest_dynamic_coverage_path()
    dynamic_rows = 0
    if dynamic_path is not None:
        dynamic_rows = len(json.loads(dynamic_path.read_text(encoding="utf-8")).get("rows", []))
    remediation_path = latest_sold_marker_remediation_path()
    remediation_rows = 0
    if remediation_path is not None:
        remediation_rows = len(json.loads(remediation_path.read_text(encoding="utf-8")).get("rows", []))
    print(
        f"[buyback] PASS: {len(ladders)} validated ladders; "
        f"{dynamic_rows} dynamic live rows; {remediation_rows} sold-marker remediation rows; "
        "broker inventory remains separately classified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
