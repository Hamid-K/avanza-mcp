#!/usr/bin/env python3
"""Validate that the portfolio objective audit cannot claim completion early."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


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


def latest_audit_path() -> Path:
    """Select the newest dated private audit without trusting a stale filename."""

    candidates = sorted((ROOT / "output").glob(AUDIT_GLOB))
    return candidates[-1] if candidates else DEFAULT_AUDIT_PATH


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


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
    _require(buyback.get("validated_ladder_count", 0) >= 0, "buyback validation count must be explicit", errors)

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

    control = payload.get("current_control_state", {})
    _require(control.get("broker_mutation") is False, "current broker mutation flag must be false", errors)
    _require(control.get("paper_mutation") is False, "current paper mutation flag must be false", errors)
    _require(control.get("trade_authority") is False, "current trade authority flag must be false", errors)
    _require(
        control.get("live_authorization") == {"personal": False, "darkcell": False},
        "current live authorization must be off for both tenants",
        errors,
    )

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
            current_live = freshness.get("status") in {"CURRENT_LIVE_REFRESH", "LIVE_REFRESH_VERIFIED"}
            if current_live:
                _require(
                    freshness.get("live_state_current") is True
                    and freshness.get("live_refresh_verified") is True
                    and freshness.get("requires_new_scoped_live_refresh_before_action") is False,
                    f"{label} current refresh must be verified without a refresh gate",
                    errors,
                )
            else:
                _require(
                    freshness.get("status") == "STAMPED_REVIEW_SNAPSHOT",
                    f"{label} control freshness must be stamped review snapshot",
                    errors,
                )
                _require(
                    freshness.get("live_refresh_verified") is False
                    and freshness.get("requires_new_scoped_live_refresh_before_action") is True,
                    f"{label} control freshness must remain review-only and refresh-gated",
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
    _require(buyback.get("candidate_rows") == 44, "buyback coverage must report 44 candidate rows", errors)
    _require(
        buyback.get("personal_rows") == 18 and buyback.get("darkcell_rows") == 26,
        "buyback coverage account rows must be 18/26",
        errors,
    )
    _require(buyback.get("one_share_rows") == 42, "buyback coverage must report 42 one-share rows", errors)
    _require(buyback.get("low_sek_rows") == 43, "buyback coverage must report 43 low-SEK rows", errors)
    _require(buyback.get("without_active_buy_rows") == 14, "buyback coverage must report 14 rows without active BUY", errors)
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
    _require(
        transaction.get("same_day_buy_fill_attribution")
        == ("PROVEN_SCOPED_RECONCILIATION" if transaction_live else "REQUIRES_NEW_SCOPED_LIVE_REFRESH"),
        "same-day transaction attribution state is inconsistent",
        errors,
    )
    _require(transaction.get("source_raw_rows_available") is False, "transaction raw-source availability must remain explicit", errors)
    _require(
        transaction.get("same_day_buy_fill_review_status")
        == ("PROVEN_SCOPED_RECONCILIATION" if transaction_live else "NOT_PROVABLE_FROM_STAMPED_SUMMARY"),
        "same-day transaction review status is inconsistent",
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
        catalyst.get("verified_upcoming_rows") == 21 and catalyst.get("unverified_upcoming_rows") == 1,
        "catalyst coverage counts are incomplete",
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
    _require(
        any(str(blocker.get("id")) == "B4" for blocker in blockers if isinstance(blocker, dict)),
        "transaction evidence blocker B4 must remain explicit",
        errors,
    )
    _require(
        any(str(blocker.get("id")) == "B6" for blocker in blockers if isinstance(blocker, dict)),
        "per-account strategy audit blocker B6 must remain explicit",
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
