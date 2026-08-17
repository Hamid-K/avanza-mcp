"""Fail-closed reachability checks for active BUY stop-loss rows."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable

from avanza_mcp.utils import scalar_number


def _number(value: Any) -> float | None:
    parsed = scalar_number(value)
    return float(parsed) if parsed is not None else None


def classify_buy_reachability(
    row: dict[str, Any],
    *,
    last_price: float | None,
    max_fixed_distance_percent: float = 15.0,
    max_practical_fixed_distance_percent: float = 8.0,
    max_reversal_trigger_percent: float = 4.0,
) -> dict[str, Any]:
    """Classify one active BUY row without treating distance as trade advice."""

    trigger_type = str(row.get("trigger_type") or "").strip().upper()
    trigger_value_type = str(row.get("trigger_value_type") or "").strip().upper()
    trigger_value = _number(row.get("trigger_value"))
    last = _number(last_price)
    distance_percent: float | None = None
    issue: str | None = None

    if trigger_type == "LESS_OR_EQUAL" and trigger_value_type == "MONETARY":
        if last is None or last <= 0 or trigger_value is None or trigger_value <= 0:
            classification = "QUOTE_OR_TRIGGER_UNAVAILABLE"
            issue = classification
        else:
            distance_percent = ((last - trigger_value) / last) * 100.0
            if distance_percent < 0:
                classification = "AT_OR_ABOVE_MARK"
                issue = classification
            elif distance_percent <= max_practical_fixed_distance_percent:
                classification = "REACHABLE_FIXED_REVIEW"
            elif distance_percent <= max_fixed_distance_percent:
                classification = "SECONDARY_FIXED_REVIEW"
            else:
                classification = "DEEP_FIXED_REVIEW"
                issue = classification
    elif trigger_type == "FOLLOW_DOWNWARDS" and trigger_value_type == "PERCENTAGE":
        if trigger_value is None or trigger_value <= 0:
            classification = "TRIGGER_UNAVAILABLE"
            issue = classification
        elif trigger_value <= max_reversal_trigger_percent:
            classification = "REACHABLE_REVERSAL_REVIEW"
        else:
            classification = "WIDE_REVERSAL_REVIEW"
            issue = classification
    else:
        classification = "UNSUPPORTED_TRIGGER_SHAPE"
        issue = classification

    return {
        **row,
        "last_price": last,
        "distance_to_trigger_percent": (
            round(distance_percent, 4) if distance_percent is not None else None
        ),
        "reachability_classification": classification,
        "reachability_issue": issue,
        "max_fixed_distance_percent": float(max_fixed_distance_percent),
        "max_practical_fixed_distance_percent": float(max_practical_fixed_distance_percent),
        "max_reversal_trigger_percent": float(max_reversal_trigger_percent),
        "trade_authority": False,
    }


def audit_buy_reachability(
    rows: Iterable[dict[str, Any]],
    *,
    quotes_by_orderbook: dict[str, float | None],
    max_fixed_distance_percent: float = 15.0,
    max_practical_fixed_distance_percent: float = 8.0,
    max_reversal_trigger_percent: float = 4.0,
) -> dict[str, Any]:
    """Audit all active BUY rows and reject deep-only recovery designs."""

    classified: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        if str(source.get("side") or "").strip().upper() != "BUY":
            continue
        if str(source.get("status") or "ACTIVE").strip().upper() != "ACTIVE":
            continue
        orderbook_id = str(source.get("orderbook_id") or "").strip()
        row = classify_buy_reachability(
            source,
            last_price=quotes_by_orderbook.get(orderbook_id),
            max_fixed_distance_percent=max_fixed_distance_percent,
            max_practical_fixed_distance_percent=max_practical_fixed_distance_percent,
            max_reversal_trigger_percent=max_reversal_trigger_percent,
        )
        classified.append(row)
        groups[orderbook_id].append(row)

    group_rows: list[dict[str, Any]] = []
    issue_count = 0
    for orderbook_id, items in groups.items():
        reachable = [
            row
            for row in items
            if row["reachability_classification"]
            in {"REACHABLE_FIXED_REVIEW", "REACHABLE_REVERSAL_REVIEW"}
        ]
        deep = [
            row
            for row in items
            if row["reachability_classification"] == "DEEP_FIXED_REVIEW"
        ]
        secondary = [
            row
            for row in items
            if row["reachability_classification"] == "SECONDARY_FIXED_REVIEW"
        ]
        wide = [
            row
            for row in items
            if row["reachability_classification"] == "WIDE_REVERSAL_REVIEW"
        ]
        unavailable = [
            row
            for row in items
            if row["reachability_classification"]
            in {
                "AT_OR_ABOVE_MARK",
                "QUOTE_OR_TRIGGER_UNAVAILABLE",
                "TRIGGER_UNAVAILABLE",
                "UNSUPPORTED_TRIGGER_SHAPE",
            }
        ]
        issues: list[str] = []
        if deep and not reachable:
            issues.append("DEEP_ONLY_RECOVERY")
        elif secondary and not reachable:
            issues.append("SECONDARY_ONLY_RECOVERY")
        if wide:
            issues.append("WIDE_REVERSAL_ROW")
        if unavailable:
            issues.append("UNCLASSIFIABLE_ACTIVE_BUY")
        residual_without_participation = any(
            str(row.get("strategy_intent") or "").strip().upper()
            == "DEEP_RESIDUAL"
            for row in deep
        ) and not reachable
        if residual_without_participation:
            issues.append("DEEP_RESIDUAL_WITHOUT_PARTICIPATION")
        issue_count += len(issues)
        first = items[0]
        group_rows.append(
            {
                "account_id": first.get("account_id"),
                "orderbook_id": orderbook_id,
                "stock": first.get("stock"),
                "active_buy_count": len(items),
                "active_buy_volume": sum(_number(row.get("volume")) or 0.0 for row in items),
                "reachable_count": len(reachable),
                "practical_count": len(reachable),
                "secondary_count": len(secondary),
                "deep_count": len(deep),
                "wide_reversal_count": len(wide),
                "issues": issues,
                "clean": not issues,
                "next_action": (
                    "REVIEW_REPLACE_OR_MARK_DORMANT"
                    if issues
                    else "KEEP_SUBJECT_TO_INSTRUMENT_GATES"
                ),
                "trade_authority": False,
            }
        )

    return {
        "complete": issue_count == 0,
        "review_required": issue_count > 0,
        "active_buy_count": len(classified),
        "instrument_count": len(group_rows),
        "issue_count": issue_count,
        "rows": classified,
        "instruments": sorted(
            group_rows,
            key=lambda row: (str(row.get("stock") or ""), str(row.get("orderbook_id") or "")),
        ),
        "policy_note": (
            "The practical fixed-price band is 8%; 8-15% is secondary review only and "
            "over 15% is deep review. These are fail-closed review limits, not recommended "
            "entries. A deep or secondary row cannot be the sole live recovery path; event, "
            "thesis, technical, risk, capacity, and full-friction gates still determine any replacement."
        ),
        "broker_mutation": False,
        "trade_authority": False,
    }


_EXPLAINABLE_RAW_ISSUES = frozenset(
    {
        "DEEP_ONLY_RECOVERY",
        "SECONDARY_ONLY_RECOVERY",
        "WIDE_REVERSAL_ROW",
        "DEEP_RESIDUAL_WITHOUT_PARTICIPATION",
    }
)


def _plan_token(plan: dict[str, Any], key: str) -> str:
    return str(plan.get(key) or "").strip().upper()


def _plan_text(plan: dict[str, Any]) -> str:
    return " ".join(
        str(plan.get(key) or "").strip()
        for key in (
            "audit_status",
            "bucket",
            "protection_classification",
            "protection_reason",
            "gate",
            "stance",
            "recommendation",
            "next_gate",
        )
    ).upper()


def classify_recovery_governance(
    instrument: dict[str, Any],
    *,
    position_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """Reconcile raw BUY reachability with an explicit reviewed position plan."""

    row = dict(instrument)
    raw_issues = [str(issue) for issue in row.get("issues") or []]
    row.update(
        {
            "position_audit_status": None,
            "position_bucket": None,
            "position_protection_classification": None,
            "position_gate": None,
            "position_next_gate": None,
            "governance_classification": "MISSING_OR_CONTRADICTORY_PLAN",
            "explained_issues": [],
            "unresolved_governance_issues": [],
            "governance_clean": False,
            "governance_reason": None,
        }
    )

    if not position_plan:
        row["unresolved_governance_issues"] = ["POSITION_PLAN_MISSING"]
        row["governance_reason"] = (
            "Raw reachability cannot be governed without a current exact-account position plan."
        )
        return row

    audit_status = _plan_token(position_plan, "audit_status")
    bucket = _plan_token(position_plan, "bucket")
    protection = _plan_token(position_plan, "protection_classification")
    next_gate = str(position_plan.get("next_gate") or "").strip()
    plan_text = _plan_text(position_plan)
    row.update(
        {
            "position_audit_status": position_plan.get("audit_status"),
            "position_bucket": position_plan.get("bucket"),
            "position_protection_classification": position_plan.get(
                "protection_classification"
            ),
            "position_gate": position_plan.get("gate"),
            "position_next_gate": position_plan.get("next_gate"),
        }
    )

    plan_issues: list[str] = []
    expected_account_id = str(position_plan.get("account_id") or "").strip()
    expected_orderbook_id = str(position_plan.get("orderbook_id") or "").strip()
    expected_buy_volume = _number(position_plan.get("active_buy_volume"))
    expected_buy_count = _number(position_plan.get("active_buy_count"))
    live_account_id = str(row.get("account_id") or "").strip()
    live_orderbook_id = str(row.get("orderbook_id") or "").strip()
    live_buy_volume = _number(row.get("active_buy_volume"))
    live_buy_count = _number(row.get("active_buy_count"))
    if (
        not expected_account_id
        or not expected_orderbook_id
        or expected_buy_volume is None
        or expected_buy_count is None
    ):
        plan_issues.append("POSITION_PLAN_FINGERPRINT_MISSING")
    elif (
        expected_account_id != live_account_id
        or expected_orderbook_id != live_orderbook_id
        or expected_buy_volume != live_buy_volume
        or expected_buy_count != live_buy_count
    ):
        plan_issues.append("POSITION_PLAN_ACTIVE_BUY_DRIFT")
    if not audit_status or not bucket:
        plan_issues.append("POSITION_PLAN_SEMANTICS_MISSING")
    if not protection:
        plan_issues.append("POSITION_PROTECTION_CLASSIFICATION_MISSING")
    if "REPAIR_REQUIRED" in {audit_status, bucket, protection}:
        plan_issues.append("POSITION_PLAN_REPAIR_REQUIRED")
    if raw_issues and not next_gate:
        plan_issues.append("POSITION_NEXT_GATE_MISSING")
    if raw_issues and (
        audit_status == "VALID_REACHABLE_PARTICIPATION"
        or bucket == "VALID_REACHABLE_PARTICIPATION"
    ):
        plan_issues.append("POSITION_PLAN_REACHABILITY_CONTRADICTION")
    if "UNCLASSIFIABLE_ACTIVE_BUY" in raw_issues:
        plan_issues.append("MECHANICAL_REACHABILITY_UNCLASSIFIABLE")

    cleanup_required = any(
        phrase in plan_text
        for phrase in (
            "APPROVAL ONLY FOR DELETING",
            "APPROVAL ONLY TO DELETE",
            "DELETE-ONLY CLEANUP",
            "REPLACE THE NON-PRACTICAL ROW",
        )
    )
    if "APPROVAL_REQUIRED" in audit_status or cleanup_required:
        plan_issues.append("POSITION_PLAN_REDESIGN_UNRESOLVED")

    if plan_issues:
        row["governance_classification"] = (
            "UNRESOLVED_REPAIR"
            if any(
                issue
                in {
                    "POSITION_PLAN_REPAIR_REQUIRED",
                    "POSITION_PLAN_REDESIGN_UNRESOLVED",
                }
                for issue in plan_issues
            )
            else "MISSING_OR_CONTRADICTORY_PLAN"
        )
        row["unresolved_governance_issues"] = list(dict.fromkeys(plan_issues))
        row["governance_reason"] = (
            "The current plan is missing, contradictory, or still requires reachability repair."
        )
        return row

    if not raw_issues:
        row.update(
            {
                "governance_classification": "PRACTICAL_RECOVERY",
                "governance_clean": True,
                "governance_reason": (
                    "The raw active BUY design is practical under the configured review limits "
                    "and has a current governed position plan."
                ),
            }
        )
        return row

    raw_issue_set = set(raw_issues)
    named_exception = protection == "NAMED_EXCEPTION"
    locked_residual = raw_issue_set <= {
        "DEEP_ONLY_RECOVERY",
        "DEEP_RESIDUAL_WITHOUT_PARTICIPATION",
    } and ("LOCKED" in audit_status or "LOCKED" in bucket)
    secondary_review = raw_issue_set <= {"SECONDARY_ONLY_RECOVERY"} and (
        "SECONDARY" in audit_status or "SECONDARY" in bucket
    )
    dormant_review = (
        raw_issue_set <= _EXPLAINABLE_RAW_ISSUES and "DORMANT" in plan_text
    )

    if named_exception:
        governance_classification = "EXPLAINED_NAMED_EXCEPTION"
    elif locked_residual:
        governance_classification = "EXPLAINED_LOCKED_RESIDUAL"
    elif secondary_review:
        governance_classification = "EXPLAINED_SECONDARY_REVIEW"
    elif dormant_review:
        governance_classification = "EXPLAINED_DORMANT_REVIEW"
    else:
        row["unresolved_governance_issues"] = [
            "RAW_REACHABILITY_ISSUE_UNEXPLAINED"
        ]
        row["governance_reason"] = (
            "The raw reachability issue is not covered by an explicit named, locked, "
            "secondary, or dormant position-plan classification."
        )
        return row

    row.update(
        {
            "governance_classification": governance_classification,
            "explained_issues": raw_issues,
            "governance_clean": True,
            "governance_reason": (
                "Raw reachability remains visible, but the current exact-account plan "
                "explicitly classifies the row as review inventory rather than practical coverage."
            ),
        }
    )
    return row


def govern_recovery_reachability(
    audit: dict[str, Any],
    *,
    plans_by_orderbook: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    """Add plan-aware governance without weakening the raw reachability audit."""

    report = deepcopy(audit)
    governed: list[dict[str, Any]] = []
    for instrument in report.get("instruments") or []:
        orderbook_id = str(instrument.get("orderbook_id") or "").strip()
        governed.append(
            classify_recovery_governance(
                instrument,
                position_plan=plans_by_orderbook.get(orderbook_id),
            )
        )

    unresolved_issue_count = sum(
        len(row["unresolved_governance_issues"]) for row in governed
    )
    explained_issue_count = sum(len(row["explained_issues"]) for row in governed)
    report.update(
        {
            "instruments": governed,
            "governance_complete": unresolved_issue_count == 0,
            "governance_review_required": unresolved_issue_count > 0,
            "unresolved_issue_count": unresolved_issue_count,
            "explained_issue_count": explained_issue_count,
            "governance_policy_note": (
                "Raw complete/review_required/issue_count fields remain mechanical and are never "
                "suppressed. Governance is complete only when every active BUY instrument has a "
                "current non-contradictory plan and each raw issue is explicitly named, locked, "
                "secondary, or dormant; missing, unclassifiable, cleanup-required, and "
                "REPAIR_REQUIRED states remain blocked."
            ),
        }
    )
    return report
