"""Fail-closed reachability checks for active BUY stop-loss rows."""

from __future__ import annotations

from collections import defaultdict
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
            elif distance_percent <= max_fixed_distance_percent:
                classification = "REACHABLE_FIXED_REVIEW"
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
        "max_reversal_trigger_percent": float(max_reversal_trigger_percent),
        "trade_authority": False,
    }


def audit_buy_reachability(
    rows: Iterable[dict[str, Any]],
    *,
    quotes_by_orderbook: dict[str, float | None],
    max_fixed_distance_percent: float = 15.0,
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
            "Distance thresholds are fail-closed review limits, not recommended entries. "
            "A deep row cannot be the sole live recovery path; event, thesis, technical, "
            "risk, capacity, and full-friction gates still determine any replacement."
        ),
        "broker_mutation": False,
        "trade_authority": False,
    }
