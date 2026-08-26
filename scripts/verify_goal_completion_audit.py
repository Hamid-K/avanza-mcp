#!/usr/bin/env python3
"""Validate that the portfolio objective audit cannot claim completion early."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from scripts.verify_buyback_ladder_artifact import sold_marker_governance_gap_rows
except ModuleNotFoundError:  # Direct script execution resolves sibling modules.
    from verify_buyback_ladder_artifact import sold_marker_governance_gap_rows


ROOT = Path(__file__).resolve().parents[1]
AUDIT_GLOB = "PORTFOLIO_REQUIREMENT_LEVEL_COMPLETION_AUDIT_*.json"
DEFAULT_AUDIT_PATH = ROOT / "output" / "PORTFOLIO_REQUIREMENT_LEVEL_COMPLETION_AUDIT_20260806_0256.json"
REQUIRED_REQUIREMENTS = {f"R{index}" for index in range(1, 9)}
TERMINAL_STATUSES = {"COMPLETE", "COMPLETED", "DONE", "CLOSED"}
CURRENT_AUDIT_STATUSES = {"RECORDED", "RECORDED_WITH_ZERO_RELEVANT_DRIFT_OR_ERROR", "PASSED"}
EXPECTED_MANUAL_EXITS = {
    ("personal", "5227886", "PLTR"): 18,
    ("darkcell", "7616265", "PLTR"): 26,
    ("darkcell", "7616265", "W"): 34,
    ("darkcell", "7616265", "SHOP"): 8,
    ("darkcell", "7616265", "NEM"): 26,
}
EXPECTED_BUYBACK_SCOPES = {
    ("personal", "5227886"),
    ("darkcell", "7616265"),
}
CURRENT_BUYBACK_STATES = {
    "LADDER_ACTIVE",
    "LADDER_DORMANT",
    "LEDGER_ONLY",
    "LADDER_GAP",
    "REPAIR_REQUIRED",
    "NAMED_EXCEPTION",
}
CURRENT_LOW_EXPOSURE_STATES = {
    "BUILD_REVIEW",
    "INTENTIONAL_MARKER_OR_CORE_HOLD",
    "EXIT_OR_NO_REENTRY_REVIEW",
    "NAMED_EXCEPTION",
    "NON_STOP_ELIGIBLE",
    "REPAIR_REQUIRED",
}


def latest_audit_path() -> Path:
    """Select the newest dated private audit without trusting a stale filename."""

    candidates = sorted((ROOT / "output").glob(AUDIT_GLOB))
    return candidates[-1] if candidates else DEFAULT_AUDIT_PATH


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _validate_current_buyback_link(
    payload: dict[str, Any],
    errors: list[str],
    *,
    require_clean: bool,
) -> None:
    """Validate the latest variable-size coverage link without fixed counts."""

    current = payload.get("current_buyback_coverage", {})
    summary = current.get("summary", {})
    governance = current.get("live_governance", {})
    validation = current.get("validation", {})
    _require(
        current.get("artifact") == "PORTFOLIO_BUYBACK_LIVE_COVERAGE",
        "current dynamic buyback coverage link is missing",
        errors,
    )
    _require(
        "PORTFOLIO_BUYBACK_LIVE_COVERAGE_" in str(current.get("source") or "")
        and "DAILY_COVERAGE_20260806" not in str(current.get("source") or ""),
        "current dynamic buyback source is invalid",
        errors,
    )
    _require(bool(str(current.get("generated_at") or "").strip()), "current dynamic buyback generated_at missing", errors)
    _require(bool(str(current.get("live_state_as_of") or "").strip()), "current dynamic buyback live_state_as_of missing", errors)
    _require(current.get("authority") == "REVIEW_ONLY", "current dynamic buyback must remain review-only", errors)
    _require(current.get("broker_mutation_authorized") is False, "current dynamic buyback broker mutation must be false", errors)
    _require(
        "No fixed historical candidate count" in str(current.get("universe_contract") or ""),
        "current dynamic buyback universe must reject fixed historical counts",
        errors,
    )
    scopes = {
        (str(row.get("tenant_session_id") or ""), str(row.get("account_id") or ""))
        for row in current.get("scope", [])
        if isinstance(row, dict)
    }
    _require(scopes == EXPECTED_BUYBACK_SCOPES, "current dynamic buyback scope is incomplete", errors)
    _require(validation.get("status") == "PASSED", "current dynamic buyback validation did not pass", errors)
    _require(validation.get("error_count") == 0 and validation.get("errors") == [], "current dynamic buyback validation has errors", errors)
    _require(governance.get("sessions_verified") is True, "current dynamic buyback sessions were not verified", errors)
    _require(
        governance.get("authorization_off") == {"personal": True, "darkcell": True},
        "current dynamic buyback authorization must be off for both tenants",
        errors,
    )
    _require(
        governance.get("personal_unresolved_position_drift") == 0
        and governance.get("darkcell_unresolved_position_drift") == 0,
        "current dynamic buyback position drift must be zero",
        errors,
    )

    row_count = current.get("row_count")
    valid_row_count = isinstance(row_count, int) and row_count > 0
    _require(valid_row_count, "current dynamic buyback row count is invalid", errors)
    _require(summary.get("exact_account_rows") == row_count, "current dynamic buyback exact row count mismatch", errors)
    personal_rows = summary.get("personal_rows")
    darkcell_rows = summary.get("darkcell_rows")
    _require(
        isinstance(personal_rows, int)
        and isinstance(darkcell_rows, int)
        and personal_rows >= 0
        and darkcell_rows >= 0
        and personal_rows + darkcell_rows == row_count,
        "current dynamic buyback account counts do not reconcile",
        errors,
    )
    for field in ("current_one_share_rows", "below_20000_sek_rows", "full_exit_rows"):
        value = summary.get(field)
        _require(
            valid_row_count and isinstance(value, int) and 0 <= value <= row_count,
            f"current dynamic buyback {field} is invalid",
            errors,
        )
    state_counts = summary.get("buyback_coverage_state_counts", {})
    low_counts = summary.get("low_exposure_decision_counts", {})
    _require(
        isinstance(state_counts, dict)
        and set(state_counts).issubset(CURRENT_BUYBACK_STATES)
        and all(isinstance(value, int) and value >= 0 for value in state_counts.values())
        and sum(state_counts.values()) == row_count,
        "current dynamic buyback state counts do not reconcile",
        errors,
    )
    _require(
        isinstance(low_counts, dict)
        and set(low_counts).issubset(CURRENT_LOW_EXPOSURE_STATES)
        and all(isinstance(value, int) and value >= 0 for value in low_counts.values())
        and sum(low_counts.values()) == row_count,
        "current dynamic low-exposure counts do not reconcile",
        errors,
    )
    supported = summary.get("percentage_ladders_with_supported_stages")
    percentage_not_set = summary.get("percentage_not_set_rows")
    _require(
        isinstance(supported, int)
        and isinstance(percentage_not_set, int)
        and supported + percentage_not_set == row_count,
        "current dynamic percentage coverage does not reconcile",
        errors,
    )
    pending_cleanup = summary.get("pending_r6a_cleanup_rows")
    _require(isinstance(pending_cleanup, int) and pending_cleanup >= 0, "current dynamic cleanup count is invalid", errors)
    if require_clean:
        _require(state_counts.get("REPAIR_REQUIRED", 0) == 0, "completed goal retains buyback REPAIR_REQUIRED rows", errors)
        _require(low_counts.get("REPAIR_REQUIRED", 0) == 0, "completed goal retains low-exposure REPAIR_REQUIRED rows", errors)
        _require(pending_cleanup == 0, "completed goal retains pending buyback cleanup rows", errors)


def _artifact_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _sold_marker_has_open_work(payload: dict[str, Any]) -> bool:
    current = payload.get("current_sold_marker_recovery", {})
    reconciliation_rows = current.get("dynamic_reconciliation", {}).get("rows")
    if isinstance(reconciliation_rows, list):
        return bool(sold_marker_governance_gap_rows(reconciliation_rows))
    summary = current.get("summary", {})
    return any(
        int(summary.get(field, 0) or 0) > 0
        for field in ("repair_required_missed_path_rows", "percentage_not_set_open_rows")
    )


def _validate_sold_marker_strategy_reconciliation(
    payload: dict[str, Any],
    errors: list[str],
    *,
    require_clean: bool,
) -> None:
    audit = payload.get("strategy_audit_coverage", {})
    if audit.get("live_refresh_verified") is not True:
        return
    position_rows = {
        str(row.get("tenant_session_id") or ""): row
        for row in audit.get("audits", [])
        if isinstance(row, dict) and row.get("tool") == "avanza_position_strategy_audit"
    }
    for tenant in ("personal", "darkcell"):
        row = position_rows.get(tenant, {})
        actual = sorted(str(value) for value in row.get("protection_repair_required_orderbook_ids", []))
        _require(
            row.get("protection_repair_required_count") == len(actual),
            f"{tenant} position repair count is internally inconsistent",
            errors,
        )
        _require(
            len(actual) == len(set(actual)) and all(value for value in actual),
            f"{tenant} position repair identities must be unique and non-empty",
            errors,
        )
        if require_clean:
            _require(
                not actual,
                f"{tenant} position protection repairs must be zero before completion",
                errors,
            )


def _validate_sold_marker_recovery_link(
    payload: dict[str, Any],
    errors: list[str],
    *,
    require_clean: bool,
) -> None:
    """Require complete-path recovery evidence to supersede a rebound snapshot."""

    current = payload.get("current_sold_marker_recovery", {})
    summary = current.get("summary", {})
    authority = current.get("authority", {})
    rows = current.get("rows", [])
    reconciliation = current.get("dynamic_reconciliation", {})
    controls = current.get("controls", [])

    _require(
        current.get("artifact") == "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE",
        "current sold-marker recovery link is missing",
        errors,
    )
    _require(
        "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE_" in str(current.get("source") or ""),
        "current sold-marker recovery source is invalid",
        errors,
    )
    _require(bool(str(current.get("generated_at") or "").strip()), "current sold-marker recovery generated_at missing", errors)
    _require(bool(str(current.get("path_snapshot_at") or "").strip()), "current sold-marker path snapshot missing", errors)
    _require(authority.get("broker_mutation") is False, "current sold-marker broker mutation must be false", errors)
    _require(authority.get("paper_mutation") is False, "current sold-marker paper mutation must be false", errors)
    _require(authority.get("trade_authority") is False, "current sold-marker trade authority must be false", errors)
    _require(
        any("PORTFOLIO_SOLD_MARKER_FULL_PATH_AUDIT_" in str(source) for source in current.get("sources", [])),
        "current sold-marker recovery must cite complete-path evidence",
        errors,
    )
    _require(isinstance(rows, list), "current sold-marker recovery rows must be a list", errors)
    if not isinstance(rows, list):
        return
    _require(current.get("row_count") == len(rows), "current sold-marker recovery row count mismatch", errors)

    keys = [
        (
            str(row.get("tenant_session_id") or ""),
            str(row.get("account_id") or ""),
            str(row.get("orderbook_id") or ""),
        )
        for row in rows
        if isinstance(row, dict)
    ]
    _require(len(keys) == len(rows), "current sold-marker recovery contains a non-object row", errors)
    _require(len(keys) == len(set(keys)), "current sold-marker recovery contains duplicate rows", errors)
    repair_rows = [row for row in rows if str(row.get("state") or "").startswith("REPAIR_REQUIRED")]
    percentage_gap_rows = [row for row in rows if row.get("state") == "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET"]
    partial_rows = [row for row in rows if str(row.get("state") or "").startswith("PARTIAL_SOLD_SLICE_RECOVERY")]
    no_reentry_rows = [row for row in rows if row.get("state") == "EXPLICIT_NO_REENTRY_CURRENT_THESIS"]
    open_rows = [row for row in rows if int(row.get("remaining_open_quantity", 0) or 0) > 0]
    remaining = sum(int(row.get("remaining_open_quantity", 0) or 0) for row in rows)
    _require(summary.get("repair_required_missed_path_rows") == len(repair_rows), "current sold-marker repair count mismatch", errors)
    _require(summary.get("percentage_not_set_open_rows") == len(percentage_gap_rows), "current sold-marker unsupported count mismatch", errors)
    _require(summary.get("partial_sale_attributed_active_rows") == len(partial_rows), "current sold-marker partial count mismatch", errors)
    _require(summary.get("explicit_no_reentry_rows") == len(no_reentry_rows), "current sold-marker no-reentry count mismatch", errors)
    _require(summary.get("open_material_rows") == len(open_rows), "current sold-marker open-row count mismatch", errors)
    _require(summary.get("remaining_open_quantity_across_material_rows") == remaining, "current sold-marker quantity mismatch", errors)
    _require(
        summary.get("all_path_active_buy_attribution_gaps_after_registry_correction") == 0,
        "current sold-marker recovery retains attribution gaps",
        errors,
    )
    _require(
        summary.get("silent_active_buy_attribution_gaps_in_material_rows") == 0,
        "current sold-marker recovery retains silent material gaps",
        errors,
    )
    _require(summary.get("broker_mutations") == 0, "current sold-marker recovery must record zero broker mutations", errors)

    control_text = " ".join(str(control).lower() for control in controls if isinstance(control, str))
    for phrase in (
        "complete authenticated price path",
        "rebound never erases",
        "durable metadata identifies the exact account",
        "percentage_not_set is fail-closed",
        "8 percent sold-marker drawdown is a mandatory review alarm",
        "do not chase a rebound",
        "no-reentry decisions expire",
    ):
        _require(phrase in control_text, f"current sold-marker control missing: {phrase}", errors)

    current_buyback = payload.get("current_buyback_coverage", {})
    _require(
        reconciliation.get("source") == current_buyback.get("source"),
        "sold-marker reconciliation does not use the current dynamic buyback source",
        errors,
    )
    _require(reconciliation.get("status") == "PASSED", "sold-marker dynamic reconciliation did not pass", errors)
    _require(
        reconciliation.get("error_count") == 0 and reconciliation.get("errors") == [],
        "sold-marker dynamic reconciliation has errors",
        errors,
    )
    reconciliation_rows = reconciliation.get("rows", [])
    _require(isinstance(reconciliation_rows, list), "sold-marker dynamic reconciliation rows missing", errors)
    if not isinstance(reconciliation_rows, list):
        return
    _require(reconciliation.get("row_count") == len(rows) == len(reconciliation_rows), "sold-marker reconciliation count mismatch", errors)
    reconciliation_by_key = {
        (
            str(row.get("tenant_session_id") or ""),
            str(row.get("account_id") or ""),
            str(row.get("orderbook_id") or ""),
        ): row
        for row in reconciliation_rows
        if isinstance(row, dict)
    }
    _require(set(reconciliation_by_key) == set(keys), "sold-marker reconciliation identities mismatch", errors)
    governance_gaps = sold_marker_governance_gap_rows(reconciliation_rows)
    invalid_no_reentry_rows = [
        row for row in governance_gaps
        if row.get("recovery_state") == "EXPLICIT_NO_REENTRY_CURRENT_THESIS"
    ]
    _require(
        not invalid_no_reentry_rows,
        "current sold-marker no-reentry evidence is missing, expired, or contradicted",
        errors,
    )
    _require(
        not any(
            row.get("dynamic_low_exposure_decision") == "EXIT_OR_NO_REENTRY_REVIEW"
            and isinstance(row.get("dynamic_active_buy_volume"), (int, float))
            and not isinstance(row.get("dynamic_active_buy_volume"), bool)
            and row.get("dynamic_active_buy_volume") > 0
            for row in reconciliation_rows
            if isinstance(row, dict)
        ),
        "current sold-marker exit/no-reentry classification conflicts with active recovery inventory",
        errors,
    )
    for recovery in rows:
        if not isinstance(recovery, dict):
            continue
        key = (
            str(recovery.get("tenant_session_id") or ""),
            str(recovery.get("account_id") or ""),
            str(recovery.get("orderbook_id") or ""),
        )
        dynamic = reconciliation_by_key.get(key, {})
        state = str(recovery.get("state") or "")
        _require(dynamic.get("dynamic_row_found") is True, f"sold-marker dynamic row missing for {key}", errors)
        _require(
            dynamic.get("dynamic_active_buy_volume") == recovery.get("sale_attributed_active_buy_quantity"),
            f"sold-marker dynamic active BUY attribution mismatch for {key}",
            errors,
        )
        if state.startswith("REPAIR_REQUIRED"):
            _require(
                dynamic.get("dynamic_buyback_coverage_state") == "REPAIR_REQUIRED"
                and dynamic.get("dynamic_low_exposure_decision") == "REPAIR_REQUIRED"
                and dynamic.get("dynamic_protection_classification") == "REPAIR_REQUIRED",
                f"sold-marker missed path is not retained as REPAIR_REQUIRED for {key}",
                errors,
            )
        elif state == "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET":
            _require(
                dynamic.get("dynamic_buyback_coverage_state") == "LADDER_GAP"
                and dynamic.get("dynamic_stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET",
                f"sold-marker unsupported material path is not retained as LADDER_GAP for {key}",
                errors,
            )

    current_generated = _artifact_time(current.get("generated_at"))
    dynamic_generated = _artifact_time(reconciliation.get("generated_at"))
    _require(current_generated is not None, "current sold-marker recovery timestamp is invalid", errors)
    _require(dynamic_generated is not None, "sold-marker dynamic reconciliation timestamp is invalid", errors)
    if current_generated is not None and dynamic_generated is not None:
        _require(dynamic_generated >= current_generated, "dynamic buyback coverage predates sold-marker remediation", errors)

    if require_clean:
        _require(
            not governance_gaps,
            "completed goal retains sold-marker governance gaps",
            errors,
        )


def _validate_completed(payload: dict[str, Any]) -> list[str]:
    """Validate the positive completion contract after live evidence exists."""

    errors: list[str] = []
    _require(payload.get("artifact") == "PORTFOLIO_REQUIREMENT_LEVEL_COMPLETION_AUDIT", "audit artifact id missing", errors)
    _require(payload.get("complete") is True, "completed audit must set complete=true", errors)
    _require(payload.get("overall_status") == "COMPLETE", "completed audit must set overall_status=COMPLETE", errors)
    _require(payload.get("goal_completion_claim") is True, "completed audit must set goal_completion_claim=true", errors)
    _require(payload.get("broker_mutation") is False, "broker mutation must remain false", errors)
    _require(payload.get("registry_mutation") is False, "registry mutation must remain false", errors)

    control = payload.get("current_control_state", {})
    _require(control.get("broker_mutation") is False, "current broker mutation flag must be false", errors)
    _require(control.get("paper_mutation") is False, "current paper mutation flag must be false", errors)
    _require(control.get("trade_authority") is False, "current trade authority flag must be false", errors)
    _require(
        control.get("live_authorization") == {"personal": False, "darkcell": False},
        "current live authorization must be off for both tenants",
        errors,
    )
    _require(control.get("live_state_current") is True, "completed audit must use current live state", errors)
    _require(
        control.get("live_refresh_required_before_action") is False,
        "completed audit must not retain a live-refresh gate",
        errors,
    )
    _validate_current_buyback_link(payload, errors, require_clean=True)
    _validate_sold_marker_recovery_link(payload, errors, require_clean=True)

    audit_coverage = payload.get("strategy_audit_coverage", {})
    _require(
        audit_coverage.get("artifact") == "PER_ACCOUNT_STRATEGY_AUDIT_COVERAGE",
        "per-account strategy audit coverage link is missing",
        errors,
    )
    _require(
        audit_coverage.get("live_refresh_verified") is True,
        "completed audit requires verified live refresh",
        errors,
    )
    expected_audit_keys = {
        (tool, tenant, account)
        for tool in ("avanza_position_strategy_audit", "avanza_stoploss_strategy_audit")
        for tenant, account in (("personal", "5227886"), ("darkcell", "7616265"))
    }
    actual_audit_keys = {
        (row.get("tool"), row.get("tenant_session_id"), row.get("account_id"))
        for row in audit_coverage.get("audits", [])
    }
    _require(actual_audit_keys == expected_audit_keys, "completed audit must contain four exact audit calls", errors)
    _require(
        all(row.get("current_run_status") in CURRENT_AUDIT_STATUSES for row in audit_coverage.get("audits", [])),
        "every exact position and stop audit must be recorded with zero relevant drift or error",
        errors,
    )
    _validate_sold_marker_strategy_reconciliation(payload, errors, require_clean=True)

    strategy = payload.get("strategy_coverage", {})
    _require(strategy.get("unique_instruments") == 65, "strategy coverage must report 65 instruments", errors)
    _require(strategy.get("account_position_rows") == 107, "strategy coverage must report 107 account rows", errors)
    _require(strategy.get("exact_account_scope_complete") is True, "strategy exact scope must be complete", errors)
    _require(strategy.get("top_level_stop_recovery_rows") == 65, "strategy stop/recovery coverage must report 65 instruments", errors)
    _require(strategy.get("top_level_review_schedule_rows") == 65, "strategy review schedule coverage must report 65 instruments", errors)
    _require(strategy.get("account_semantic_rows") == 107, "strategy semantic coverage must report 107 account rows", errors)
    _require(strategy.get("account_semantic_stop_recovery_rows") == 107, "account stop/recovery coverage must report 107 rows", errors)
    _require(strategy.get("generic_recommendation_rows_remaining") == 0, "strategy generic recommendations must be zero", errors)
    _require(
        "0 stop/order error rows" in str(strategy.get("current_drift_or_error_rows", "")),
        "strategy drift summary must report zero stop/order error rows",
        errors,
    )

    controls = payload.get("portfolio_control_coverage", {})
    for label, control_payload in controls.items():
        if not isinstance(control_payload, dict):
            continue
        freshness = control_payload.get("freshness", {})
        if label == "buyback":
            _require(
                control_payload.get("role") == "HISTORICAL_STAMPED_SNAPSHOT"
                and control_payload.get("historical_snapshot") is True,
                "historical buyback snapshot role is missing",
                errors,
            )
            _require(
                freshness.get("status") == "STAMPED_REVIEW_SNAPSHOT"
                and freshness.get("live_refresh_verified") is False,
                "historical buyback snapshot must remain stamped and non-current",
                errors,
            )
            continue
        _require(
            freshness.get("live_state_current") is True and freshness.get("live_refresh_verified") is True,
            f"{label} control must be reconciled to current live state",
            errors,
        )
        _require(
            freshness.get("requires_new_scoped_live_refresh_before_action") is False,
            f"{label} control must not retain a live-refresh gate",
            errors,
        )

    buyback = controls.get("buyback", {})
    _require(buyback.get("artifact") == "PORTFOLIO_BUYBACK_DAILY_COVERAGE", "buyback coverage link is missing", errors)
    _require(buyback.get("historical_snapshot") is True, "buyback historical snapshot marker is missing", errors)

    reconciliation = controls.get("artifact_reconciliation", {})
    _require(
        reconciliation.get("status") in {"RECONCILED_CURRENT_COUNTS", "RECONCILED_CURRENT_LIVE_STATE"},
        "cross-artifact reconciliation must be current and reconciled",
        errors,
    )
    delta = reconciliation.get("count_delta_live_minus_pending")
    if isinstance(delta, dict):
        _require(
            all(value == 0 for value in delta.values()),
            "current live and pending order counts must reconcile to zero delta",
            errors,
        )

    forward_kpi = controls.get("forward_kpi", {})
    _require(
        forward_kpi.get("forward_outcome_proven") is True,
        "forward outcome evidence must be proven before completion",
        errors,
    )
    _require(
        forward_kpi.get("completed_forward_scorecard_measures") == forward_kpi.get("scorecard_measure_count"),
        "all forward scorecard measures must be completed",
        errors,
    )

    transaction = payload.get("transaction_coverage", {})
    _require(
        transaction.get("requires_new_scoped_live_refresh_before_action") is False,
        "transaction coverage must not retain a live-refresh gate",
        errors,
    )
    _require(transaction.get("source_raw_rows_available") is True, "raw transaction rows must be available", errors)
    _require(
        transaction.get("same_day_buy_fill_attribution") not in {None, "REQUIRES_NEW_SCOPED_LIVE_REFRESH"},
        "same-day BUY attribution must be proven",
        errors,
    )

    scheduler = payload.get("scheduler_coverage", {})
    _require(
        scheduler.get("requires_new_scoped_live_refresh_before_action") is False,
        "scheduler coverage must not retain a live-refresh gate",
        errors,
    )
    _require(int(scheduler.get("terminal_rows_in_active_section", 0) or 0) == 0, "terminal scheduler rows must be archived", errors)

    catalyst = payload.get("catalyst_coverage", {})
    _require(
        catalyst.get("requires_new_scoped_live_refresh_before_action") is False,
        "catalyst coverage must not retain a live-refresh gate",
        errors,
    )

    blockers = payload.get("completion_blockers")
    _require(blockers == [], "completed audit must have no completion blockers", errors)
    requirements = payload.get("requirements", [])
    _require({row.get("id") for row in requirements if isinstance(row, dict)} == REQUIRED_REQUIREMENTS, "requirement coverage must be exactly R1-R8", errors)
    for row in requirements if isinstance(requirements, list) else []:
        _require(str(row.get("status", "")).upper() in TERMINAL_STATUSES, f"requirement {row.get('id')} is not terminal", errors)
        _require(not str(row.get("remaining_proof") or "").strip(), f"requirement {row.get('id')} retains remaining proof", errors)
    return errors


def _validate_incomplete(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _require(payload.get("artifact") == "PORTFOLIO_REQUIREMENT_LEVEL_COMPLETION_AUDIT", "audit artifact id missing", errors)
    _require(payload.get("complete") is False, "objective audit must remain incomplete", errors)
    _require(payload.get("overall_status") == "ACTIVE_NOT_COMPLETE", "audit must remain ACTIVE_NOT_COMPLETE", errors)
    _require(payload.get("goal_completion_claim") is False, "goal completion claim must remain false", errors)
    _require(payload.get("broker_mutation") is False, "broker mutation must remain false", errors)
    _require(payload.get("registry_mutation") is False, "registry mutation must remain false", errors)

    audit_coverage = payload.get("strategy_audit_coverage", {})
    _require(
        audit_coverage.get("artifact") == "PER_ACCOUNT_STRATEGY_AUDIT_COVERAGE",
        "per-account strategy audit coverage link is missing",
        errors,
    )
    audit_is_current = (
        audit_coverage.get("status") == "LIVE_REFRESH_VERIFIED_REVIEW_REQUIRED"
        and audit_coverage.get("live_refresh_verified") is True
    )
    audit_is_unavailable = (
        audit_coverage.get("status") == "REQUIRES_NEW_SCOPED_LIVE_REFRESH"
        and audit_coverage.get("live_refresh_verified") is False
    )
    _require(audit_is_current or audit_is_unavailable, "per-account strategy audit coverage state is invalid", errors)
    expected_audit_keys = {
        (tool, tenant, account)
        for tool in ("avanza_position_strategy_audit", "avanza_stoploss_strategy_audit")
        for tenant, account in (("personal", "5227886"), ("darkcell", "7616265"))
    }
    actual_audit_keys = {
        (row.get("tool"), row.get("tenant_session_id"), row.get("account_id"))
        for row in audit_coverage.get("audits", [])
    }
    _require(actual_audit_keys == expected_audit_keys, "per-account audit coverage must contain four exact audit calls", errors)
    if audit_is_unavailable:
        _require(
            all(row.get("current_run_status") == "NOT_RUN_SESSION_UNAVAILABLE" for row in audit_coverage.get("audits", [])),
            "per-account audit coverage must not imply an unexecuted audit passed",
            errors,
        )
    else:
        _require(
            all(
                row.get("tenant_session_id") in {"personal", "darkcell"}
                and row.get("account_id") in {"5227886", "7616265"}
                and row.get("tool") in {"avanza_position_strategy_audit", "avanza_stoploss_strategy_audit"}
                for row in audit_coverage.get("audits", [])
            ),
            "current per-account audit coverage must contain only exact scoped calls",
            errors,
        )
        _require(
            sum(row.get("tool") == "avanza_position_strategy_audit" for row in audit_coverage.get("audits", [])) == 2
            and sum(row.get("tool") == "avanza_stoploss_strategy_audit" for row in audit_coverage.get("audits", [])) == 2,
            "current per-account audit coverage must contain two position and two stop audits",
            errors,
        )
        _require(
            all(row.get("unresolved_mismatch_count", 0) == 0 for row in audit_coverage.get("audits", []) if row.get("tool") == "avanza_position_strategy_audit")
            and all(row.get("error_count", 0) == 0 for row in audit_coverage.get("audits", []) if row.get("tool") == "avanza_stoploss_strategy_audit"),
            "current per-account audits must have no unresolved position mismatch or stop error",
            errors,
        )
        exception_metadata = audit_coverage.get("holding_only_exception_metadata", {})
        _require(
            exception_metadata.get("status") == "COMPLETE"
            and exception_metadata.get("count") == exception_metadata.get("expected_count"),
            "acknowledged holding-only exceptions must have complete metadata",
            errors,
        )
        for entry in exception_metadata.get("entries", []):
            _require(
                entry.get("metadata_status") == "COMPLETE"
                and bool(entry.get("owner"))
                and bool(entry.get("reason"))
                and bool(entry.get("review_due"))
                and entry.get("allowed_mismatches") == ["holding"]
                and entry.get("rebaseline_authorized") is False,
                f"holding-only exception metadata is incomplete for {entry.get('orderbook_id')}",
                errors,
            )
    _validate_sold_marker_strategy_reconciliation(payload, errors, require_clean=False)

    control = payload.get("current_control_state", {})
    _require(control.get("broker_mutation") is False, "current broker mutation flag must be false", errors)
    _require(control.get("paper_mutation") is False, "current paper mutation flag must be false", errors)
    _require(control.get("trade_authority") is False, "current trade authority flag must be false", errors)
    _require(
        control.get("live_authorization") == {"personal": False, "darkcell": False},
        "current live authorization must be off for both tenants",
        errors,
    )
    _validate_current_buyback_link(payload, errors, require_clean=False)
    _validate_sold_marker_recovery_link(payload, errors, require_clean=False)

    strategy = payload.get("strategy_coverage", {})
    _require(strategy.get("artifact") == "PORTFOLIO_INSTRUMENT_STRATEGY_MASTER", "strategy coverage link is missing", errors)
    _require(strategy.get("unique_instruments") == 65, "strategy coverage must report 65 instruments", errors)
    _require(strategy.get("account_position_rows") == 107, "strategy coverage must report 107 account rows", errors)
    _require(strategy.get("exact_account_scope_rows") == 107, "strategy coverage must report 107 exact scopes", errors)
    _require(strategy.get("exact_account_scope_complete") is True, "strategy exact scope must be complete", errors)
    _require(strategy.get("generic_recommendation_rows_remaining") == 0, "strategy generic recommendations must be zero", errors)
    _require(
        "0 stop/order error rows" in str(strategy.get("current_drift_or_error_rows", "")),
        "strategy drift summary must report zero stop/order error rows",
        errors,
    )

    controls = payload.get("portfolio_control_coverage", {})
    for label, control_payload in controls.items():
        if not isinstance(control_payload, dict):
            continue
        freshness = control_payload.get("freshness", {})
        if label == "buyback":
            _require(
                control_payload.get("role") == "HISTORICAL_STAMPED_SNAPSHOT"
                and control_payload.get("historical_snapshot") is True,
                "historical buyback snapshot role is missing",
                errors,
            )
            _require(
                freshness.get("status") == "STAMPED_REVIEW_SNAPSHOT",
                "historical buyback control freshness must remain stamped",
                errors,
            )
            _require(
                freshness.get("live_refresh_verified") is False
                and freshness.get("requires_new_scoped_live_refresh_before_action") is True,
                "historical buyback control must remain non-current and refresh-gated",
                errors,
            )
            continue
        _require(
            freshness.get("status") == "STAMPED_ANALYSIS_SNAPSHOT",
            f"{label} control freshness must be stamped analysis snapshot",
            errors,
        )
        _require(
            freshness.get("live_state_current") is False
            and freshness.get("live_refresh_verified") is False
            and freshness.get("requires_new_scoped_live_refresh_before_action") is True,
            f"{label} control freshness must remain non-current and refresh-gated",
            errors,
        )
    factor = controls.get("factor", {})
    _require(factor.get("artifact") == "PORTFOLIO_FACTOR_EXPOSURE", "factor coverage link is missing", errors)
    _require(factor.get("instrument_rows") == 65 and factor.get("unique_instruments") == 65, "factor coverage must report 65 instruments", errors)
    _require(factor.get("account_position_rows") == 107, "factor coverage must report 107 account rows", errors)
    pending = controls.get("pending_order", {})
    _require(pending.get("artifact") == "PORTFOLIO_PENDING_ORDER_IMPLEMENTATION", "pending-order coverage link is missing", errors)
    _require(pending.get("active_rows") == 54 and pending.get("unique_stop_ids") == 54, "pending-order coverage must report 54 unique active rows", errors)
    _require(pending.get("buy_rows") == 46 and pending.get("sell_rows") == 8, "pending-order BUY/SELL coverage must be 46/8", errors)
    _require(pending.get("generic_implementation_rows") == 0, "pending-order generic implementation rows must be zero", errors)
    _require(pending.get("all_strategy_intents_recorded") is True, "pending-order strategy intents must be complete", errors)
    displacement = controls.get("displacement", {})
    _require(displacement.get("artifact") == "PORTFOLIO_CAPITAL_DISPLACEMENT", "displacement coverage link is missing", errors)
    _require(displacement.get("rows") == 23, "displacement coverage must report 23 rows", errors)
    _require(displacement.get("candidate_before_cancellation_remains_binding") is True, "candidate-before-cancellation must remain binding", errors)
    risk = controls.get("risk", {})
    _require(risk.get("artifact") == "PORTFOLIO_RISK_GOVERNANCE", "risk coverage link is missing", errors)
    _require(risk.get("authorization") == "ANALYSIS_AND_POLICY_ONLY", "risk coverage must remain analysis-only", errors)
    _require(risk.get("hard_churn_brake_active") is True, "risk coverage must keep hard churn brake active", errors)
    buyback = controls.get("buyback", {})
    _require(buyback.get("artifact") == "PORTFOLIO_BUYBACK_DAILY_COVERAGE", "buyback coverage link is missing", errors)
    _require(buyback.get("historical_snapshot") is True, "buyback historical snapshot marker is missing", errors)
    _require(buyback.get("candidate_rows") == 44, "historical buyback snapshot must report 44 candidate rows", errors)
    _require(
        buyback.get("personal_rows") == 18 and buyback.get("darkcell_rows") == 26,
        "historical buyback snapshot account rows must be 18/26",
        errors,
    )
    _require(buyback.get("one_share_rows") == 42, "historical buyback snapshot must report 42 one-share rows", errors)
    _require(buyback.get("low_sek_rows") == 43, "historical buyback snapshot must report 43 low-SEK rows", errors)
    _require(buyback.get("without_active_buy_rows") == 14, "historical buyback snapshot must report 14 rows without active BUY", errors)
    _require(
        {
            "ladder_dormant": 8,
            "ledger_only": 32,
            "ladder_gaps": 0,
            "repair_required": 3,
            "named_exceptions": 1,
        }
        == {key: buyback.get(key) for key in ("ladder_dormant", "ledger_only", "ladder_gaps", "repair_required", "named_exceptions")},
        "buyback coverage state counts are incomplete",
        errors,
    )
    buy_governance = controls.get("buy_governance", {})
    _require(
        buy_governance.get("artifact") == "PORTFOLIO_ACTIVE_BUY_GOVERNANCE_AUDIT",
        "active-buy governance coverage link is missing",
        errors,
    )
    _require(
        buy_governance.get("active_buy_rows") == 46
        and buy_governance.get("active_sell_rows") == 8
        and buy_governance.get("fixed_monetary_buy_rows") == 43
        and buy_governance.get("relative_buy_rows") == 3,
        "active-buy governance counts are incomplete",
        errors,
    )
    _require(buy_governance.get("validated_ladder_count") == 0, "active-buy governance must report zero validated ladders", errors)
    _require(len(buy_governance.get("relative_child_cap_defects", [])) == 3, "relative-child cap defects must remain explicit", errors)
    forward_kpi = controls.get("forward_kpi", {})
    _require(
        forward_kpi.get("artifact") == "PORTFOLIO_FORWARD_KPI_COVERAGE_AUDIT",
        "forward KPI coverage link is missing",
        errors,
    )
    _require(
        forward_kpi.get("status") == "INCOMPLETE_OUTCOME_EVIDENCE"
        and forward_kpi.get("scorecard_measure_count") == 12
        and forward_kpi.get("completed_forward_scorecard_measures") == 0
        and forward_kpi.get("forward_outcome_proven") is False,
        "forward KPI outcome proof must remain explicitly incomplete",
        errors,
    )
    _require(
        forward_kpi.get("hard_churn_brake_active") is True
        and forward_kpi.get("freshness", {}).get("live_refresh_verified") is False
        and forward_kpi.get("freshness", {}).get("requires_new_scoped_live_refresh_before_action") is True,
        "forward KPI coverage must remain refresh-gated with hard churn active",
        errors,
    )
    reconciliation = controls.get("artifact_reconciliation", {})
    _require(
        reconciliation.get("artifact") == "PORTFOLIO_CONTROL_ARTIFACT_RECONCILIATION",
        "cross-artifact reconciliation link is missing",
        errors,
    )
    _require(
        reconciliation.get("status") == "BLOCKED_STALE_ARTIFACT_CONTRADICTION",
        "stale active-row contradiction must remain explicitly blocked",
        errors,
    )
    _require(
        reconciliation.get("live_reconciliation_counts") == {"active_rows": 62, "buy_rows": 48, "sell_rows": 14}
        and reconciliation.get("pending_order_counts") == {"active_rows": 54, "buy_rows": 46, "sell_rows": 8}
        and reconciliation.get("count_delta_live_minus_pending") == {"active_rows": 8, "buy_rows": 2, "sell_rows": 6},
        "stale active-row contradiction counts are not preserved",
        errors,
    )

    transaction = payload.get("transaction_coverage", {})
    _require(
        transaction.get("artifact") == "PORTFOLIO_TRANSACTION_COVERAGE_AUDIT",
        "transaction coverage link is missing",
        errors,
    )
    _require(
        transaction.get("status") in {
            "HISTORICAL_SUMMARY_RECONCILED_RECENT_LIVE_READBACK_REQUIRED",
            "LIVE_SCOPED_REFRESH_RAW_SOURCE_GAP",
            "EXACT_ACCOUNT_RAW_SOURCE_VERIFIED",
        },
        "transaction coverage status is not a recognized fail-closed state",
        errors,
    )
    _require(
        transaction.get("historical_account_position_rows") == 107,
        "transaction coverage must report 107 historical account-position rows",
        errors,
    )
    transaction_live = transaction.get("status") == "LIVE_SCOPED_REFRESH_RAW_SOURCE_GAP"
    transaction_raw_verified = transaction.get("status") == "EXACT_ACCOUNT_RAW_SOURCE_VERIFIED"
    _require(
        transaction.get("same_day_buy_fill_attribution")
        == ("PROVEN_SCOPED_RECONCILIATION" if transaction_live or transaction_raw_verified else "REQUIRES_NEW_SCOPED_LIVE_REFRESH"),
        "same-day transaction attribution state is inconsistent",
        errors,
    )
    _require(
        transaction.get("source_raw_rows_available") is transaction_raw_verified,
        "transaction raw-source availability must match the verified evidence state",
        errors,
    )
    _require(
        transaction.get("same_day_buy_fill_review_status")
        == ("PROVEN_SCOPED_RECONCILIATION" if transaction_live or transaction_raw_verified else "NOT_PROVABLE_FROM_STAMPED_SUMMARY"),
        "same-day transaction review status is inconsistent",
        errors,
    )
    if transaction_raw_verified:
        expected_scopes = {("personal", "5227886"), ("darkcell", "7616265")}
        raw_accounts = transaction.get("raw_account_coverage", [])
        actual_scopes = {
            (str(row.get("tenant_session_id")), str(row.get("account_id")))
            for row in raw_accounts
            if isinstance(row, dict)
        }
        _require(
            actual_scopes == expected_scopes
            and transaction.get("raw_row_shape_verified") is True
            and all(
                isinstance(row, dict)
                and row.get("exact_account_scope") is True
                and row.get("truncation_risk") is False
                and int(row.get("raw_rows", -1)) == int(row.get("returned_rows", -2))
                for row in raw_accounts
            ),
            "exact-account raw transaction coverage is incomplete or truncated",
            errors,
        )
    actual_manual_exits = {
        (row.get("tenant_session_id"), row.get("account_id"), row.get("ticker")): row.get("quantity")
        for row in transaction.get("manual_exit_rows", [])
    }
    _require(actual_manual_exits == EXPECTED_MANUAL_EXITS, "central audit manual-exit identities or quantities are incomplete", errors)
    refresh_required = any(
        payload.get(section, {}).get("requires_new_scoped_live_refresh_before_action") is True
        for section in ("transaction_coverage", "scheduler_coverage", "catalyst_coverage")
    )
    if refresh_required:
        _require(
            control.get("live_refresh_required_before_action") is True,
            "live refresh requirement must be explicit",
            errors,
        )
        current_scoped_refresh = (
            control.get("live_state_current") is True
            and control.get("live_checkpoint_status") == "CURRENT_SCOPED_LIVE_REFRESH_BUT_OTHER_GATES_OPEN"
        )
        stale_scoped_refresh = (
            control.get("live_state_current") is False
            and control.get("live_checkpoint_status") == "STAMPED_SNAPSHOT_REQUIRES_NEW_SCOPED_REFRESH"
        )
        _require(
            current_scoped_refresh or stale_scoped_refresh,
            "live checkpoint status must accurately describe current or unavailable refresh state",
            errors,
        )

    scheduler = payload.get("scheduler_coverage", {})
    _require(
        scheduler.get("artifact") == "PORTFOLIO_SCHEDULER_COVERAGE_AUDIT",
        "scheduler coverage link is missing",
        errors,
    )
    _require(
        scheduler.get("canonical_approval_c_rows") == 18,
        "scheduler coverage must report the canonical 18 Approval C rows",
        errors,
    )
    _require(
        scheduler.get("requires_new_scoped_live_refresh_before_action") is True,
        "scheduler coverage must remain pending live refresh",
        errors,
    )
    if int(scheduler.get("terminal_rows_in_active_section", 0) or 0) > 0:
        archive = scheduler.get("archive_proposal", {})
        _require(
            archive.get("status") == "AWAITING_USER_SCHEDULER_AUTHORITY",
            "scheduler archive proposal must remain pending explicit authority",
            errors,
        )
        _require(
            archive.get("destination") == "Completed Archive"
            and archive.get("preserve_planned_action_semantics") is True,
            "scheduler archive proposal must preserve destination and action semantics",
            errors,
        )
        _require(
            len(archive.get("row_ids", [])) == int(scheduler.get("terminal_rows_in_active_section", 0) or 0),
            "scheduler archive proposal must cover every terminal active row",
            errors,
        )

    catalyst = payload.get("catalyst_coverage", {})
    _require(
        catalyst.get("artifact") == "PORTFOLIO_CATALYST_COVERAGE_AUDIT",
        "catalyst coverage link is missing",
        errors,
    )
    _require(
        isinstance(catalyst.get("verified_upcoming_rows"), int)
        and catalyst.get("verified_upcoming_rows") >= 0
        and isinstance(catalyst.get("unverified_upcoming_rows"), int)
        and catalyst.get("unverified_upcoming_rows") >= 0,
        "catalyst coverage counts are invalid",
        errors,
    )
    _require(
        catalyst.get("stale_unverified_rows") == 0
        and catalyst.get("publication_state_current") is True,
        "catalyst publication state retains a stale unverified contradiction",
        errors,
    )
    _require(
        catalyst.get("requires_new_scoped_live_refresh_before_action") is True,
        "catalyst coverage must remain pending live refresh",
        errors,
    )

    blockers = payload.get("completion_blockers", [])
    _require(isinstance(blockers, list) and bool(blockers), "completion blockers must remain explicit", errors)
    for blocker in blockers if isinstance(blockers, list) else []:
        _require(bool(blocker.get("id")), "completion blocker id missing", errors)
        _require(bool(blocker.get("condition_to_close")), "completion blocker close condition missing", errors)
    has_b4 = any(str(blocker.get("id")) == "B4" for blocker in blockers if isinstance(blocker, dict))
    if transaction_raw_verified:
        _require(not has_b4, "transaction evidence blocker B4 must close after exact raw verification", errors)
        _require(
            any(
                str(blocker.get("id")) == "B4"
                for blocker in payload.get("closed_blockers", [])
                if isinstance(blocker, dict)
            ),
            "closed transaction blocker B4 must retain its raw-recapture evidence",
            errors,
        )
    else:
        _require(has_b4, "transaction evidence blocker B4 must remain explicit", errors)
    _require(
        any(str(blocker.get("id")) == "B6" for blocker in blockers if isinstance(blocker, dict)),
        "per-account strategy audit blocker B6 must remain explicit",
        errors,
    )
    if _sold_marker_has_open_work(payload):
        _require(
            any(str(blocker.get("id")) == "B11" for blocker in blockers if isinstance(blocker, dict)),
            "complete-path sold-marker blocker B11 must remain explicit",
            errors,
        )
    if int(scheduler.get("terminal_rows_in_active_section", 0) or 0) > 0:
        _require(
            any(str(blocker.get("id")) == "B5" for blocker in blockers if isinstance(blocker, dict)),
            "scheduler archive blocker B5 must remain explicit",
            errors,
        )

    checks = payload.get("next_required_checks", [])
    _require(isinstance(checks, list) and bool(checks), "next required checks must remain explicit", errors)
    for check in checks if isinstance(checks, list) else []:
        _require(bool(check.get("date")), "next required check date missing", errors)
        _require(bool(check.get("purpose")), "next required check purpose missing", errors)

    requirements = payload.get("requirements", [])
    ids = {row.get("id") for row in requirements if isinstance(row, dict)}
    _require(ids == REQUIRED_REQUIREMENTS, "requirement coverage must be exactly R1-R8", errors)
    for row in requirements if isinstance(requirements, list) else []:
        _require(bool(row.get("requirement")), f"requirement text missing for {row.get('id')}", errors)
        _require(bool(row.get("status")), f"requirement status missing for {row.get('id')}", errors)
        _require(bool(row.get("remaining_proof")), f"remaining proof missing for {row.get('id')}", errors)
        _require(
            str(row.get("status", "")).upper() not in TERMINAL_STATUSES,
            f"requirement {row.get('id')} must not be marked terminal",
            errors,
        )
    return errors


def validate(payload: dict[str, Any]) -> list[str]:
    """Dispatch to the strict incomplete or positive completion contract."""

    if payload.get("complete") is True or payload.get("overall_status") == "COMPLETE":
        return _validate_completed(payload)
    return _validate_incomplete(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=latest_audit_path())
    args = parser.parse_args()
    path = args.audit if args.audit.is_absolute() else ROOT / args.audit
    if not path.exists():
        print(f"[goal-audit] missing: {path}")
        return 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate(payload)
    if errors:
        for error in errors:
            print(f"[goal-audit] FAIL: {error}")
        return 1
    if payload.get("complete") is True:
        print("[goal-audit] PASS: objective completion contract is proven")
    else:
        print("[goal-audit] PASS: objective remains explicitly incomplete and fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
