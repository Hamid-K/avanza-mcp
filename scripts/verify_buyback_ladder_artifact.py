#!/usr/bin/env python3
"""Validate the local buyback-ladder presentation contract.

This is deliberately read-only. It checks that the rendered table cannot
silently promote broker inventory or stale templates into active ladders.
"""

from __future__ import annotations

import json
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
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
SOLD_MARKER_FULL_PATH_GLOB = "PORTFOLIO_SOLD_MARKER_FULL_PATH_AUDIT_[0-9]*.json"
R17_MIGRATION_WORKLIST_GLOB = "PORTFOLIO_R17_MULTI_SALE_MIGRATION_WORKLIST_[0-9]*.json"
R17_OPEN_PATH_EVIDENCE_GLOB = "PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_[0-9]*.json"

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
NO_REENTRY_MAX_VALIDITY = timedelta(days=14)


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


def latest_sold_marker_full_path_path() -> Path | None:
    """Return the newest account-scoped sold-marker full-path universe."""

    paths = sorted((ROOT / "output").glob(SOLD_MARKER_FULL_PATH_GLOB))
    return paths[-1] if paths else None


def latest_r17_migration_worklist_path() -> Path | None:
    """Return the newest raw-chronology R17 migration worklist."""

    paths = sorted((ROOT / "output").glob(R17_MIGRATION_WORKLIST_GLOB))
    return paths[-1] if paths else None


def latest_r17_open_path_evidence_path() -> Path | None:
    """Return the newest exact open-sale complete-path evidence artifact."""

    paths = sorted((ROOT / "output").glob(R17_OPEN_PATH_EVIDENCE_GLOB))
    return paths[-1] if paths else None


def _artifact_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _artifact_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _latest_time(*values: Any) -> datetime | None:
    parsed = [_artifact_time(value) for value in values]
    timestamps = [value for value in parsed if value is not None]
    if not timestamps:
        return None
    try:
        return max(timestamps)
    except TypeError:
        return None


def _is_positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _no_reentry_decision_gaps(
    identity_row: dict[str, Any],
    decision: Any,
    reference_time: datetime | None,
) -> list[str]:
    """Validate a time-bounded exact-sale decision that closes recovery."""

    if not isinstance(decision, dict):
        return ["structured no-reentry decision is missing"]

    gaps: list[str] = []
    required_text = (
        "decision_id",
        "decision_basis",
        "thesis_evidence",
        "event_evidence",
        "technical_evidence",
        "path_evidence",
    )
    for field in required_text:
        if not str(decision.get(field) or "").strip():
            gaps.append(f"{field} is missing")

    for field in ("tenant_session_id", "account_id", "orderbook_id", "sale_date"):
        expected = str(identity_row.get(field) or "")
        actual = str(decision.get(field) or "")
        if not expected or actual != expected:
            gaps.append(f"{field} does not match the exact sold slice")

    for field in ("sale_lot_id", "sale_transaction_id", "sale_timestamp"):
        expected = str(identity_row.get(field) or "")
        actual = str(decision.get(field) or "")
        if expected and actual != expected:
            gaps.append(f"{field} does not match the exact sale lot")

    sold_quantity = identity_row.get("sold_quantity")
    decision_sold_quantity = decision.get("sold_quantity")
    closed_quantity = decision.get("closed_quantity")
    if not _is_positive_integer(sold_quantity):
        gaps.append("sold quantity is not a positive exact integer")
    if decision_sold_quantity != sold_quantity:
        gaps.append("decision sold quantity does not match the exact sold slice")
    if closed_quantity != sold_quantity:
        gaps.append("closed quantity does not equal the exact sold quantity")
    if identity_row.get("remaining_open_quantity") != 0:
        gaps.append("remaining open quantity is not zero")

    original_sold_quantity = identity_row.get("original_sold_quantity")
    recovered_before_decision = identity_row.get("recovered_before_decision_quantity")
    if original_sold_quantity is not None:
        if decision.get("original_sold_quantity") != original_sold_quantity:
            gaps.append("original sold quantity does not match the immutable sale lot")
        if decision.get("recovered_before_decision_quantity") != recovered_before_decision:
            gaps.append("recovered-before-decision quantity does not match prior allocations")
        if (
            _is_positive_integer(original_sold_quantity)
            and _is_nonnegative_integer(recovered_before_decision)
            and _is_positive_integer(sold_quantity)
            and original_sold_quantity != recovered_before_decision + sold_quantity
        ):
            gaps.append("terminal decision slice does not reconcile to the original sale lot")

    sale_date = _artifact_date(identity_row.get("sale_date"))
    if sale_date is None:
        gaps.append("sale date is invalid")

    decision_at = _artifact_time(decision.get("decision_at"))
    revalidated_at = _artifact_time(decision.get("last_revalidated_at"))
    expires_at = _artifact_time(decision.get("expires_at"))
    if decision_at is None:
        gaps.append("decision_at is invalid")
    if revalidated_at is None:
        gaps.append("last_revalidated_at is invalid")
    if expires_at is None:
        gaps.append("expires_at is invalid")
    if reference_time is None:
        gaps.append("artifact reference time is invalid")

    timestamps = [
        value
        for value in (decision_at, revalidated_at, expires_at, reference_time)
        if value is not None
    ]
    timezone_shapes = {
        value.utcoffset() is not None
        for value in timestamps
    }
    if len(timezone_shapes) > 1:
        gaps.append("decision timestamps and artifact reference time use incompatible timezone forms")
    elif decision_at is not None and revalidated_at is not None and expires_at is not None:
        if sale_date is not None and decision_at.date() < sale_date:
            gaps.append("decision predates the exact sale")
        if revalidated_at < decision_at:
            gaps.append("last revalidation predates the decision")
        if expires_at <= revalidated_at:
            gaps.append("expiry does not follow the last revalidation")
        elif expires_at - revalidated_at > NO_REENTRY_MAX_VALIDITY:
            gaps.append("expiry exceeds the 14-day no-reentry validity ceiling")
        if reference_time is not None:
            if revalidated_at > reference_time:
                gaps.append("last revalidation is later than the artifact review")
            if expires_at <= reference_time:
                gaps.append("no-reentry decision is expired")

    if decision.get("newer_evidence_reviewed") is not True:
        gaps.append("newer evidence was not explicitly reviewed")
    if decision.get("contradiction_status") != "NONE":
        gaps.append("newer evidence contradicts or has not cleared the no-reentry decision")
    return list(dict.fromkeys(gaps))


def _is_terminal_no_reentry_dynamic_row(row: dict[str, Any]) -> bool:
    reason = str(row.get("coverage_reason") or "").lower()
    return (
        isinstance(row.get("no_reentry_decision"), dict)
        or "no-reentry" in reason
        or "no re-entry" in reason
        or (
            row.get("low_exposure_decision") == "EXIT_OR_NO_REENTRY_REVIEW"
            and row.get("buyback_coverage_state") == "LEDGER_ONLY"
            and row.get("stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET"
        )
    )


def _remediation_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("tenant_session_id") or ""),
        str(row.get("account_id") or ""),
        str(row.get("orderbook_id") or ""),
    )


def _path_evidence_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return _remediation_key(row)


def _expected_path_state(*, crossed: bool, named: bool) -> str:
    if crossed and named:
        return "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
    if crossed:
        return "MISSED_PATH_REPAIR_REQUIRED"
    return "LADDER_GAP_PERCENTAGE_NOT_SET"


def _validate_embedded_path_evidence(
    evidence: Any,
    *,
    key: tuple[str, str, str],
    expected_quantity: Any,
) -> list[str]:
    """Validate the percentage-only path link embedded in a governed row."""

    errors: list[str] = []
    if not isinstance(evidence, dict):
        return [f"complete-path evidence is missing for {key}"]
    required = {
        "source",
        "source_generated_at",
        "chart_from",
        "chart_to",
        "chart_point_count",
        "remaining_open_quantity",
        "remaining_open_lot_count",
        "open_sale_transaction_ids",
        "active_buy_quantity",
        "sale_attributed_active_buy_quantity",
        "maximum_open_lot_drop_percent",
        "current_drop_below_weighted_marker_percent",
        "open_lots_crossing_8pct_alarm",
        "crossed_8pct_review_alarm",
        "technical",
        "rsi",
        "atr20_percent_of_current_close",
        "named_exception",
        "path_state",
    }
    _require(required.issubset(evidence), f"complete-path evidence fields are missing for {key}", errors)
    _require(
        "PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_" in str(evidence.get("source") or ""),
        f"complete-path source is invalid for {key}",
        errors,
    )
    _require(
        _artifact_time(evidence.get("source_generated_at")) is not None,
        f"complete-path source timestamp is invalid for {key}",
        errors,
    )
    _require(
        evidence.get("remaining_open_quantity") == expected_quantity
        and _is_positive_integer(evidence.get("remaining_open_quantity")),
        f"complete-path quantity does not match the governed open quantity for {key}",
        errors,
    )
    lot_count = evidence.get("remaining_open_lot_count")
    transaction_ids = evidence.get("open_sale_transaction_ids")
    _require(_is_positive_integer(lot_count), f"complete-path open-lot count is invalid for {key}", errors)
    _require(
        isinstance(transaction_ids, list)
        and len(transaction_ids) == lot_count
        and all(bool(str(value or "")) for value in transaction_ids)
        and len(transaction_ids) == len(set(transaction_ids)),
        f"complete-path sale transaction ids are invalid for {key}",
        errors,
    )
    active_buy = evidence.get("active_buy_quantity")
    attributed_buy = evidence.get("sale_attributed_active_buy_quantity")
    _require(_is_nonnegative_integer(active_buy), f"complete-path active BUY quantity is invalid for {key}", errors)
    _require(
        _is_nonnegative_integer(attributed_buy)
        and (not _is_nonnegative_integer(active_buy) or attributed_buy <= active_buy),
        f"complete-path sale-attributed BUY quantity is invalid for {key}",
        errors,
    )
    _require(
        _is_positive_integer(evidence.get("chart_point_count")),
        f"complete-path chart point count is invalid for {key}",
        errors,
    )
    maximum = evidence.get("maximum_open_lot_drop_percent")
    current = evidence.get("current_drop_below_weighted_marker_percent")
    _require(
        isinstance(maximum, (int, float)) and not isinstance(maximum, bool),
        f"complete-path maximum drop is invalid for {key}",
        errors,
    )
    _require(
        isinstance(current, (int, float)) and not isinstance(current, bool),
        f"complete-path current marker drop is invalid for {key}",
        errors,
    )
    crossing_count = evidence.get("open_lots_crossing_8pct_alarm")
    _require(
        _is_nonnegative_integer(crossing_count) and (not _is_positive_integer(lot_count) or crossing_count <= lot_count),
        f"complete-path crossing count is invalid for {key}",
        errors,
    )
    crossed = evidence.get("crossed_8pct_review_alarm")
    named = evidence.get("named_exception")
    _require(isinstance(crossed, bool), f"complete-path crossing flag is invalid for {key}", errors)
    _require(isinstance(named, bool), f"complete-path named flag is invalid for {key}", errors)
    if isinstance(crossed, bool) and _is_nonnegative_integer(crossing_count):
        _require(
            crossed == (crossing_count > 0),
            f"complete-path crossing flag does not match its lot count for {key}",
            errors,
        )
    if isinstance(crossed, bool) and isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
        _require(
            crossed == (float(maximum) >= 8.0),
            f"complete-path 8 percent alarm does not match maximum drawdown for {key}",
            errors,
        )
    if isinstance(crossed, bool) and isinstance(named, bool):
        _require(
            evidence.get("path_state") == _expected_path_state(crossed=crossed, named=named),
            f"complete-path state is inconsistent for {key}",
            errors,
        )
    return errors


def validate_r17_open_path_evidence(payload: dict[str, Any]) -> list[str]:
    """Validate the authenticated open-lot price-path source itself."""

    errors: list[str] = []
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    _require(
        payload.get("artifact") == "PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE",
        "R17 open-sale path artifact id missing",
        errors,
    )
    _require(payload.get("schema_version") == 1, "R17 open-sale path schema version must be 1", errors)
    _require(payload.get("authority") == "ANALYSIS_ONLY", "R17 open-sale path must remain analysis-only", errors)
    _require(payload.get("broker_mutation") is False, "R17 open-sale path broker mutation must be false", errors)
    _require(_artifact_time(payload.get("generated_at")) is not None, "R17 open-sale path generated_at invalid", errors)
    _require(isinstance(rows, list) and bool(rows), "R17 open-sale path rows must be non-empty", errors)
    if not isinstance(rows, list):
        return errors

    keys: list[tuple[str, str, str]] = []
    total_lots = 0
    total_quantity = 0
    crossed_rows = 0
    crossed_lots = 0
    named_crossed_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            errors.append("R17 open-sale path contains a non-object row")
            continue
        key = _path_evidence_key(row)
        keys.append(key)
        _require(key[:2] in EXPECTED_DYNAMIC_SCOPES and bool(key[2]), f"R17 open-sale path scope invalid for {key}", errors)
        remaining = row.get("remaining_open_quantity")
        lot_count = row.get("remaining_open_lot_count")
        lots = row.get("exact_lots")
        _require(_is_positive_integer(remaining), f"R17 open-sale path quantity invalid for {key}", errors)
        _require(_is_positive_integer(lot_count), f"R17 open-sale path lot count invalid for {key}", errors)
        _require(isinstance(lots, list) and len(lots) == lot_count, f"R17 open-sale path lots mismatch for {key}", errors)
        if not isinstance(lots, list):
            continue
        lot_quantity = 0
        lot_crossings = 0
        transaction_ids: list[str] = []
        maximums: list[float] = []
        for lot in lots:
            if not isinstance(lot, dict):
                errors.append(f"R17 open-sale path contains a non-object lot for {key}")
                continue
            quantity = lot.get("remaining_open_quantity")
            maximum = lot.get("maximum_drop_below_marker_percent")
            transaction_ids.append(str(lot.get("sale_transaction_id") or ""))
            if _is_positive_integer(quantity):
                lot_quantity += quantity
            else:
                errors.append(f"R17 open-sale path lot quantity invalid for {key}")
            if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
                maximums.append(float(maximum))
            else:
                errors.append(f"R17 open-sale path lot maximum drop invalid for {key}")
            lot_crossings += lot.get("crossed_8pct_review_alarm") is True
        _require(
            bool(transaction_ids)
            and all(transaction_ids)
            and len(transaction_ids) == len(set(transaction_ids)),
            f"R17 open-sale path transaction ids invalid for {key}",
            errors,
        )
        _require(lot_quantity == remaining, f"R17 open-sale path lot quantity mismatch for {key}", errors)
        _require(
            row.get("open_lots_crossing_8pct_alarm") == lot_crossings,
            f"R17 open-sale path crossing count mismatch for {key}",
            errors,
        )
        active_buy = row.get("active_buy_quantity")
        attributed_buy = row.get("sale_attributed_active_buy_quantity")
        _require(_is_nonnegative_integer(active_buy), f"R17 open-sale path active BUY invalid for {key}", errors)
        _require(
            _is_nonnegative_integer(attributed_buy)
            and (not _is_nonnegative_integer(active_buy) or attributed_buy <= active_buy),
            f"R17 open-sale path sale-attributed BUY invalid for {key}",
            errors,
        )
        crossed = row.get("crossed_8pct_review_alarm") is True
        named = row.get("named_exception") is True
        _require(crossed == (lot_crossings > 0), f"R17 open-sale path crossing state mismatch for {key}", errors)
        maximum = row.get("maximum_open_lot_drop_percent")
        if maximums and isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            _require(abs(float(maximum) - max(maximums)) <= 0.0001, f"R17 open-sale path maximum mismatch for {key}", errors)
            _require(crossed == (float(maximum) >= 8.0), f"R17 open-sale path alarm mismatch for {key}", errors)
        else:
            errors.append(f"R17 open-sale path row maximum drop invalid for {key}")
        _require(
            row.get("path_state") == _expected_path_state(crossed=crossed, named=named),
            f"R17 open-sale path state mismatch for {key}",
            errors,
        )
        total_lots += int(lot_count or 0)
        total_quantity += int(remaining or 0)
        crossed_rows += crossed
        crossed_lots += lot_crossings
        named_crossed_rows += crossed and named

    _require(len(keys) == len(set(keys)), "R17 open-sale path contains duplicate exact rows", errors)
    expected_summary = {
        "exact_account_rows": len(rows),
        "unique_orderbooks": len({key[2] for key in keys}),
        "exact_open_sale_lots": total_lots,
        "remaining_open_quantity": total_quantity,
        "rows_crossing_8pct_review_alarm": crossed_rows,
        "lots_crossing_8pct_review_alarm": crossed_lots,
        "named_exception_rows_with_crossing": named_crossed_rows,
        "path_or_marker_errors": 0,
    }
    for field, expected in expected_summary.items():
        _require(summary.get(field) == expected, f"R17 open-sale path summary {field} mismatch", errors)
    return errors


def _allocation_source_totals(
    allocations: Any,
    *,
    lot_ids: set[str],
    source_id_field: str,
    label: str,
    key: tuple[str, str, str],
    errors: list[str],
    require_normalization: bool = False,
) -> dict[str, int]:
    """Validate exact source-to-lot allocations and return per-lot totals."""

    if not isinstance(allocations, list):
        errors.append(f"{label} must be a list for {key}")
        return {}

    allocation_ids: set[str] = set()
    source_quantities: dict[str, int] = {}
    allocated_by_source: dict[str, int] = defaultdict(int)
    allocated_by_lot: dict[str, int] = defaultdict(int)
    source_lot_pairs: set[tuple[str, str]] = set()
    for allocation in allocations:
        if not isinstance(allocation, dict):
            errors.append(f"{label} contains a non-object row for {key}")
            continue
        allocation_id = str(allocation.get("allocation_id") or "")
        source_id = str(allocation.get(source_id_field) or "")
        lot_id = str(allocation.get("sale_lot_id") or "")
        quantity = allocation.get("quantity")
        source_quantity = allocation.get("source_quantity")
        _require(bool(allocation_id), f"{label} allocation id missing for {key}", errors)
        _require(bool(source_id), f"{label} source id missing for {key}", errors)
        _require(lot_id in lot_ids, f"{label} references an unknown sale lot for {key}", errors)
        _require(_is_positive_integer(quantity), f"{label} quantity must be a positive integer for {key}", errors)
        _require(
            _is_positive_integer(source_quantity),
            f"{label} source quantity must be a positive integer for {key}",
            errors,
        )
        if require_normalization:
            raw_source_quantity = allocation.get("raw_source_quantity")
            normalization_factor = allocation.get("quantity_normalization_factor")
            _require(
                _is_positive_integer(raw_source_quantity),
                f"{label} raw source quantity must be a positive integer for {key}",
                errors,
            )
            _require(
                _is_positive_number(normalization_factor),
                f"{label} normalization factor must be positive for {key}",
                errors,
            )
            if _is_positive_integer(raw_source_quantity) and _is_positive_number(normalization_factor):
                _require(
                    raw_source_quantity * normalization_factor == source_quantity,
                    f"{label} normalized source quantity is inconsistent for {key}",
                    errors,
                )
        _require(allocation_id not in allocation_ids, f"{label} allocation id is duplicated for {key}", errors)
        pair = (source_id, lot_id)
        _require(pair not in source_lot_pairs, f"{label} source-to-lot allocation is duplicated for {key}", errors)
        allocation_ids.add(allocation_id)
        source_lot_pairs.add(pair)
        if source_id in source_quantities:
            _require(
                source_quantities[source_id] == source_quantity,
                f"{label} source quantity changes between allocations for {key}",
                errors,
            )
        elif _is_positive_integer(source_quantity):
            source_quantities[source_id] = source_quantity
        if _is_positive_integer(quantity):
            allocated_by_source[source_id] += quantity
            allocated_by_lot[lot_id] += quantity

    for source_id, quantity in allocated_by_source.items():
        _require(
            quantity <= source_quantities.get(source_id, 0),
            f"{label} source {source_id} is overallocated for {key}",
            errors,
        )
    return dict(allocated_by_lot)


def _inventory_totals(
    inventory: Any,
    *,
    source_id_field: str,
    label: str,
    key: tuple[str, str, str],
    errors: list[str],
) -> tuple[int, set[str]]:
    """Validate explicitly unattributed inventory without treating it as recovery."""

    if not isinstance(inventory, list):
        errors.append(f"{label} must be a list for {key}")
        return 0, set()
    total = 0
    source_ids: set[str] = set()
    for item in inventory:
        if not isinstance(item, dict):
            errors.append(f"{label} contains a non-object row for {key}")
            continue
        source_id = str(item.get(source_id_field) or "")
        quantity = item.get("quantity")
        _require(bool(source_id), f"{label} source id missing for {key}", errors)
        _require(_is_positive_integer(quantity), f"{label} quantity must be a positive integer for {key}", errors)
        _require(source_id not in source_ids, f"{label} source id is duplicated for {key}", errors)
        source_ids.add(source_id)
        if _is_positive_integer(quantity):
            total += quantity
    return total, source_ids


def _validate_recovery_cycle(
    row: dict[str, Any],
    reference_time: datetime | None,
    schema_version: int = 2,
) -> list[str]:
    """Validate a complete multi-sale recovery cycle for one account/instrument."""

    errors: list[str] = []
    key = _remediation_key(row)
    cycle_id = str(row.get("recovery_cycle_id") or "")
    boundary = row.get("cycle_boundary_evidence")
    state = str(row.get("state") or "")
    unproven_envelope = False
    _require(bool(cycle_id), f"recovery cycle id missing for {key}", errors)
    _require(isinstance(boundary, dict), f"cycle boundary evidence missing for {key}", errors)
    if isinstance(boundary, dict):
        boundary_status = str(boundary.get("boundary_status") or "")
        unproven_envelope = boundary_status == "UNPROVEN_CONSERVATIVE_ENVELOPE"
        _require(boundary.get("exact_account_scope") is True, f"cycle account scope is not exact for {key}", errors)
        if unproven_envelope:
            _require(
                state.startswith("REPAIR_REQUIRED"),
                f"an unproven recovery envelope must remain REPAIR_REQUIRED for {key}",
                errors,
            )
            _require(
                boundary.get("truncation_risk") is True,
                f"an unproven recovery envelope must preserve truncation risk for {key}",
                errors,
            )
            _require(
                boundary.get("all_sale_transactions_in_cycle_included") is False,
                f"an unproven recovery envelope must not claim a complete cycle for {key}",
                errors,
            )
            _require(
                boundary.get("all_selected_sale_transactions_in_envelope_included") is True,
                f"an unproven recovery envelope does not include every selected sale for {key}",
                errors,
            )
        else:
            _require(boundary.get("truncation_risk") is False, f"cycle transaction history is truncated for {key}", errors)
            _require(
                boundary.get("all_sale_transactions_in_cycle_included") is True,
                f"cycle does not prove that every sale transaction is included for {key}",
                errors,
            )
        for field in ("source", "cycle_start", "cycle_end", "boundary_basis"):
            _require(bool(str(boundary.get(field) or "").strip()), f"cycle boundary {field} missing for {key}", errors)

    lots = row.get("sale_lots")
    _require(isinstance(lots, list) and bool(lots), f"sale lots must be a non-empty list for {key}", errors)
    if not isinstance(lots, list) or not lots:
        return errors

    lot_ids: set[str] = set()
    transaction_ids: set[str] = set()
    lot_by_id: dict[str, dict[str, Any]] = {}
    raw_sold_total = 0
    for lot in lots:
        if not isinstance(lot, dict):
            errors.append(f"sale lots contain a non-object row for {key}")
            continue
        lot_id = str(lot.get("sale_lot_id") or "")
        transaction_id = str(lot.get("sale_transaction_id") or "")
        timestamp = str(lot.get("sale_timestamp") or "")
        _require(bool(lot_id), f"sale lot id missing for {key}", errors)
        _require(bool(transaction_id), f"sale transaction id missing for {key}", errors)
        _require(_artifact_time(timestamp) is not None, f"sale timestamp invalid for {key}", errors)
        _require(_is_positive_integer(lot.get("sold_quantity")), f"sale lot quantity invalid for {key}", errors)
        if schema_version >= 3:
            raw_sold_quantity = lot.get("raw_sold_quantity")
            normalization_factor = lot.get("quantity_normalization_factor")
            _require(
                _is_positive_integer(raw_sold_quantity),
                f"sale lot raw quantity invalid for {key}",
                errors,
            )
            _require(
                _is_positive_number(normalization_factor),
                f"sale lot normalization factor invalid for {key}",
                errors,
            )
            if _is_positive_integer(raw_sold_quantity) and _is_positive_number(normalization_factor):
                _require(
                    raw_sold_quantity * normalization_factor == lot.get("sold_quantity"),
                    f"sale lot normalized quantity is inconsistent for {key}",
                    errors,
                )
                raw_sold_total += raw_sold_quantity
        _require(lot_id not in lot_ids, f"sale lot id is duplicated for {key}", errors)
        _require(transaction_id not in transaction_ids, f"sale transaction id is duplicated for {key}", errors)
        lot_ids.add(lot_id)
        transaction_ids.add(transaction_id)
        lot_by_id[lot_id] = lot

    fill_allocations = row.get("qualifying_fill_allocations")
    active_allocations = row.get("active_recovery_allocations")
    fill_by_lot = _allocation_source_totals(
        fill_allocations,
        lot_ids=lot_ids,
        source_id_field="buy_transaction_id",
        label="qualifying fill allocations",
        key=key,
        errors=errors,
        require_normalization=schema_version >= 3,
    )
    active_by_lot = _allocation_source_totals(
        active_allocations,
        lot_ids=lot_ids,
        source_id_field="stop_loss_id",
        label="active recovery allocations",
        key=key,
        errors=errors,
    )
    pre_sale_total, pre_sale_ids = _inventory_totals(
        row.get("pre_sale_active_buy_inventory"),
        source_id_field="stop_loss_id",
        label="pre-sale active BUY inventory",
        key=key,
        errors=errors,
    )
    unattributed_active_total, unattributed_active_ids = _inventory_totals(
        row.get("unattributed_active_buy_inventory"),
        source_id_field="stop_loss_id",
        label="unattributed active BUY inventory",
        key=key,
        errors=errors,
    )
    unattributed_fill_total, unattributed_fill_ids = _inventory_totals(
        row.get("unattributed_later_buy_inventory"),
        source_id_field="buy_transaction_id",
        label="unattributed later BUY inventory",
        key=key,
        errors=errors,
    )
    non_recovery_buy_total = 0
    if schema_version >= 3:
        non_recovery_buy_total, _ = _inventory_totals(
            row.get("non_recovery_buy_inventory"),
            source_id_field="buy_transaction_id",
            label="non-recovery BUY inventory",
            key=key,
            errors=errors,
        )
    allocated_stop_ids = {
        str(item.get("stop_loss_id") or "")
        for item in active_allocations or []
        if isinstance(item, dict)
    }
    allocated_buy_ids = {
        str(item.get("buy_transaction_id") or "")
        for item in fill_allocations or []
        if isinstance(item, dict)
    }
    _require(
        not (allocated_stop_ids & (pre_sale_ids | unattributed_active_ids)),
        f"active BUY source is both sale-attributed and unattributed for {key}",
        errors,
    )
    _require(
        not (pre_sale_ids & unattributed_active_ids),
        f"active BUY source is classified in two non-recovery inventories for {key}",
        errors,
    )
    _require(
        not (allocated_buy_ids & unattributed_fill_ids),
        f"BUY fill is both sale-attributed and unattributed for {key}",
        errors,
    )

    sold_total = 0
    fill_total = 0
    active_total = 0
    closed_total = 0
    remaining_total = 0
    for lot_id, lot in lot_by_id.items():
        sold = lot.get("sold_quantity")
        filled = fill_by_lot.get(lot_id, 0)
        active = active_by_lot.get(lot_id, 0)
        decision = lot.get("no_reentry_decision")
        closed = decision.get("closed_quantity", 0) if isinstance(decision, dict) else 0
        remaining = lot.get("remaining_open_quantity")
        for field in (
            "qualifying_filled_quantity",
            "active_recovery_quantity",
            "closed_no_reentry_quantity",
            "remaining_open_quantity",
        ):
            _require(_is_nonnegative_integer(lot.get(field)), f"sale lot {field} is invalid for {key}", errors)
        _require(lot.get("qualifying_filled_quantity") == filled, f"sale lot fill allocation mismatch for {key}", errors)
        _require(lot.get("active_recovery_quantity") == active, f"sale lot active allocation mismatch for {key}", errors)
        _require(lot.get("closed_no_reentry_quantity") == closed, f"sale lot no-reentry quantity mismatch for {key}", errors)
        if all(_is_nonnegative_integer(value) for value in (filled, active, closed, remaining)) and _is_positive_integer(sold):
            _require(sold == filled + active + closed + remaining, f"sale lot quantity parity failed for {key}", errors)
        if isinstance(decision, dict):
            recovered_before_decision = filled + active
            identity = {
                "tenant_session_id": key[0],
                "account_id": key[1],
                "orderbook_id": key[2],
                "sale_lot_id": lot_id,
                "sale_transaction_id": lot.get("sale_transaction_id"),
                "sale_timestamp": lot.get("sale_timestamp"),
                "sale_date": str(lot.get("sale_timestamp") or "")[:10],
                "sold_quantity": closed,
                "remaining_open_quantity": remaining,
                "original_sold_quantity": sold,
                "recovered_before_decision_quantity": recovered_before_decision,
            }
            for gap in _no_reentry_decision_gaps(identity, decision, reference_time):
                errors.append(f"sale-lot no-reentry decision is invalid for {key}/{lot_id}: {gap}")
        sold_total += int(sold or 0)
        fill_total += filled
        active_total += active
        closed_total += int(closed or 0)
        remaining_total += int(remaining or 0)

    aggregate_fields = {
        "sold_quantity": sold_total,
        "later_filled_quantity": fill_total,
        "sale_attributed_active_buy_quantity": active_total,
        "closed_no_reentry_quantity": closed_total,
        "remaining_open_quantity": remaining_total,
        "pre_sale_active_buy_quantity": pre_sale_total,
        "unattributed_active_buy_quantity": unattributed_active_total,
        "unattributed_later_buy_quantity": unattributed_fill_total,
    }
    if schema_version >= 3:
        aggregate_fields["non_recovery_buy_quantity"] = non_recovery_buy_total
    for field, expected in aggregate_fields.items():
        _require(row.get(field) == expected, f"recovery cycle aggregate {field} mismatch for {key}", errors)
    if unproven_envelope:
        _require(
            fill_total == 0 and active_total == 0 and closed_total == 0,
            f"an unproven recovery envelope cannot receive recovery or terminal credit for {key}",
            errors,
        )
        _require(
            remaining_total == sold_total,
            f"an unproven recovery envelope must preserve every selected sold share as open for {key}",
            errors,
        )
    _require(row.get("raw_sale_transaction_count") == len(lot_by_id), f"raw sale transaction count mismatch for {key}", errors)
    if schema_version >= 3:
        _require(
            row.get("raw_sale_quantity_total") == raw_sold_total,
            f"raw sale quantity total mismatch for {key}",
            errors,
        )
        _require(
            row.get("normalized_sale_quantity_total") == sold_total,
            f"normalized sale quantity total mismatch for {key}",
            errors,
        )
    else:
        _require(row.get("raw_sale_quantity_total") == sold_total, f"raw sale quantity total mismatch for {key}", errors)
    latest_timestamp = max((str(lot.get("sale_timestamp")) for lot in lot_by_id.values()), default="")
    _require(row.get("sale_date") == latest_timestamp[:10], f"latest sale date does not match the cycle for {key}", errors)
    return errors


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
    schema_version = payload.get("schema_version")
    _require(schema_version in {2, 3, 4}, "sold-marker remediation schema version must be 2, 3 or 4", errors)
    _require(bool(str(payload.get("generated_at") or "").strip()), "sold-marker remediation generated_at missing", errors)
    remediation_reference_time = _latest_time(
        payload.get("generated_at"),
        payload.get("verified_at"),
        payload.get("path_snapshot_at"),
    )
    _require(remediation_reference_time is not None, "sold-marker remediation reference time is invalid", errors)
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
        any(
            "PORTFOLIO_RAW_TRANSACTION_RECOVERY_" in str(source)
            or "PORTFOLIO_SOLD_MARKER_FULL_PATH_AUDIT_" in str(source)
            for source in payload.get("sources", [])
        ),
        "sold-marker remediation must cite authenticated transaction chronology",
        errors,
    )

    cycle_ids: list[str] = []
    total_sale_lots = 0
    multi_sale_cycles = 0
    raw_transaction_ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        errors.extend(_validate_recovery_cycle(row, remediation_reference_time, int(schema_version or 2)))
        if int(schema_version or 2) >= 4 and _is_positive_integer(row.get("remaining_open_quantity")):
            errors.extend(
                _validate_embedded_path_evidence(
                    row.get("full_path_evidence"),
                    key=_remediation_key(row),
                    expected_quantity=row.get("remaining_open_quantity"),
                )
            )
        cycle_ids.append(str(row.get("recovery_cycle_id") or ""))
        lots = row.get("sale_lots") if isinstance(row.get("sale_lots"), list) else []
        total_sale_lots += len(lots)
        multi_sale_cycles += len(lots) > 1
        raw_transaction_ids.extend(
            str(lot.get("sale_transaction_id") or "")
            for lot in lots
            if isinstance(lot, dict)
        )
    _require(len(cycle_ids) == len(set(cycle_ids)), "sold-marker remediation contains duplicate recovery cycle ids", errors)
    _require(
        len(raw_transaction_ids) == len(set(raw_transaction_ids)),
        "sold-marker remediation reuses a raw sale transaction across recovery cycles",
        errors,
    )

    repair_rows = [row for row in rows if str(row.get("state") or "").startswith("REPAIR_REQUIRED")]
    percentage_gap_rows = [row for row in rows if row.get("state") == "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET"]
    partial_rows = [
        row for row in rows
        if str(row.get("state") or "").startswith("PARTIAL_SOLD_SLICE_RECOVERY")
    ]
    no_reentry_rows = [row for row in rows if row.get("state") == "EXPLICIT_NO_REENTRY_CURRENT_THESIS"]
    named_path_rows = [row for row in rows if row.get("state") == "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"]
    open_rows = [row for row in rows if int(row.get("remaining_open_quantity", 0) or 0) > 0]
    open_percentage_not_set_rows = [
        row
        for row in open_rows
        if row.get("recorded_stage_percentages_below_marker") is None
        or row.get("recorded_stage_percentages_below_marker") == "PERCENTAGE_NOT_SET"
    ]
    remaining_quantity = sum(int(row.get("remaining_open_quantity", 0) or 0) for row in rows)

    _require(
        summary.get("repair_required_missed_path_rows") == len(repair_rows),
        "sold-marker remediation repair count mismatch",
        errors,
    )
    expected_percentage_not_set_rows = (
        len(open_percentage_not_set_rows)
        if int(schema_version or 2) >= 4
        else len(percentage_gap_rows)
    )
    _require(
        summary.get("percentage_not_set_open_rows") == expected_percentage_not_set_rows,
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
    if int(schema_version or 2) >= 4:
        crossed_path_rows = [
            row
            for row in open_rows
            if isinstance(row.get("full_path_evidence"), dict)
            and row["full_path_evidence"].get("crossed_8pct_review_alarm") is True
        ]
        _require(
            summary.get("full_path_evidence_rows") == len(open_rows),
            "sold-marker remediation full-path evidence count mismatch",
            errors,
        )
        _require(
            summary.get("rows_crossing_8pct_review_alarm") == len(crossed_path_rows),
            "sold-marker remediation crossed-path count mismatch",
            errors,
        )
        _require(
            summary.get("sold_cycle_repair_required_rows") == len(repair_rows),
            "sold-marker remediation sold-cycle repair count mismatch",
            errors,
        )
        _require(
            summary.get("named_exception_path_review_rows") == len(named_path_rows),
            "sold-marker remediation named path-review count mismatch",
            errors,
        )
        _require(
            summary.get("material_path_open_rows") == len(percentage_gap_rows),
            "sold-marker remediation material open-path count mismatch",
            errors,
        )
        _require(
            summary.get("path_evidence_missing_rows") == 0,
            "sold-marker remediation retains missing path evidence",
            errors,
        )
        for row in open_rows:
            evidence = row.get("full_path_evidence")
            if not isinstance(evidence, dict):
                continue
            key = _remediation_key(row)
            crossed = evidence.get("crossed_8pct_review_alarm") is True
            named = evidence.get("named_exception") is True
            expected_state = (
                "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
                if crossed and named
                else "REPAIR_REQUIRED_MISSED_PATH"
                if crossed
                else "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET"
            )
            _require(
                row.get("state") == expected_state,
                f"sold-marker remediation path state is inconsistent for {key}",
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
    unmodeled = summary.get("unmodeled_prior_sale_identity_count")
    _require(_is_nonnegative_integer(unmodeled), "unmodeled prior-sale identity count is invalid", errors)
    if _is_nonnegative_integer(unmodeled):
        _require(
            summary.get("exact_account_rows_with_prior_same_account_sales") == len(rows) + unmodeled,
            "modeled and unmodeled prior-sale identities do not reconcile",
            errors,
        )
        _require(
            summary.get("multi_sale_governance_complete") == (unmodeled == 0),
            "multi-sale governance completeness flag is inconsistent",
            errors,
        )
    _require(summary.get("modeled_recovery_cycle_rows") == len(rows), "modeled recovery-cycle count mismatch", errors)
    _require(summary.get("modeled_sale_lots") == total_sale_lots, "modeled sale-lot count mismatch", errors)
    _require(summary.get("multi_sale_recovery_cycle_rows") == multi_sale_cycles, "multi-sale cycle count mismatch", errors)
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

    for row in no_reentry_rows:
        key = _remediation_key(row)
        for gap in _no_reentry_decision_gaps(
            {
                **row,
                "sold_quantity": row.get("closed_no_reentry_quantity"),
            },
            row.get("no_reentry_decision"),
            remediation_reference_time,
        ):
            errors.append(f"sold-marker no-reentry decision is invalid for {key}: {gap}")

    control_text = " ".join(str(control).lower() for control in controls if isinstance(control, str))
    required_control_phrases = {
        "complete authenticated price path": "complete-path control missing",
        "rebound never erases": "rebound-persistence control missing",
        "durable metadata identifies the exact account": "exact-attribution control missing",
        "percentage_not_set is fail-closed": "PERCENTAGE_NOT_SET fail-closed control missing",
        "8 percent sold-marker drawdown is a mandatory review alarm": "8 percent review-alarm control missing",
        "do not chase a rebound": "no-rebound-chasing control missing",
        "no-reentry decisions expire": "no-reentry expiry control missing",
        "every unresolved sale lot": "multi-sale lot persistence control missing",
        "pre-sale and unattributed buy inventory": "unattributed inventory separation control missing",
        "duplicate or overallocated": "allocation uniqueness control missing",
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
        sold_cycle_repair_ids = set(
            str(value) for value in proof.get("sold_cycle_repair_orderbook_ids", [])
        )
        position_repair_ids = set(
            str(value) for value in proof.get("position_repair_required_orderbook_ids", [])
        )
        _require(
            sold_cycle_repair_ids == set(repair_ids_by_tenant[tenant]),
            f"sold-marker {tenant} sold-cycle repair identities are incomplete",
            errors,
        )
        if int(schema_version or 2) >= 4:
            named_path_ids = {
                str(value) for value in proof.get("named_path_review_orderbook_ids", [])
            }
            expected_named_path_ids = {
                str(row.get("orderbook_id"))
                for row in named_path_rows
                if row.get("tenant_session_id") == tenant
            }
            _require(
                named_path_ids == expected_named_path_ids,
                f"sold-marker {tenant} named path-review identities are incomplete",
                errors,
            )
        _require(
            len(position_repair_ids) == len(proof.get("position_repair_required_orderbook_ids", [])),
            f"sold-marker {tenant} position repair identities contain duplicates",
            errors,
        )
    return errors


def validate_sold_marker_universe_against_full_path(
    remediation_payload: dict[str, Any],
    full_path_payload: dict[str, Any],
) -> list[str]:
    """Prove that modeled extras do not replace full-path source identities."""

    errors: list[str] = []
    remediation_rows = remediation_payload.get("rows", [])
    full_path_rows = full_path_payload.get("rows", [])
    remediation_summary = remediation_payload.get("summary", {})
    full_path_summary = full_path_payload.get("summary", {})
    _require(isinstance(remediation_rows, list), "sold-marker remediation rows must be a list", errors)
    _require(isinstance(full_path_rows, list), "sold-marker full-path rows must be a list", errors)
    if not isinstance(remediation_rows, list) or not isinstance(full_path_rows, list):
        return errors

    remediation_keys = {
        _remediation_key(row)
        for row in remediation_rows
        if isinstance(row, dict)
    }
    full_path_keys = {
        _remediation_key(row)
        for row in full_path_rows
        if isinstance(row, dict)
    }
    _require(
        len(full_path_keys) == len(full_path_rows),
        "sold-marker full-path source contains duplicate or invalid identities",
        errors,
    )
    _require(
        full_path_summary.get("exact_account_rows") == len(full_path_keys),
        "sold-marker full-path source summary count mismatch",
        errors,
    )

    combined_keys = full_path_keys | remediation_keys
    modeled_outside_full_path = remediation_keys - full_path_keys
    unmodeled_full_path = full_path_keys - remediation_keys
    source_universe = remediation_payload.get("source_universe", {})
    _require(
        remediation_summary.get("exact_account_rows_with_prior_same_account_sales") == len(combined_keys),
        "sold-marker remediation omits full-path or modeled-outside-source identities from its universe count",
        errors,
    )
    _require(
        remediation_summary.get("unmodeled_prior_sale_identity_count") == len(unmodeled_full_path),
        "sold-marker remediation unmodeled count does not match the full-path identity set",
        errors,
    )
    _require(
        source_universe.get("full_path_identity_count") == len(full_path_keys),
        "sold-marker source-universe full-path count mismatch",
        errors,
    )
    _require(
        source_universe.get("modeled_outside_full_path_identity_count") == len(modeled_outside_full_path),
        "sold-marker source-universe modeled-outside-full-path count mismatch",
        errors,
    )
    _require(
        source_universe.get("combined_prior_sale_identity_count") == len(combined_keys),
        "sold-marker source-universe combined count mismatch",
        errors,
    )
    return errors


def validate_sold_marker_remediation_against_worklist(
    remediation_payload: dict[str, Any],
    worklist_payload: dict[str, Any],
) -> list[str]:
    """Require exact raw sale and later-BUY parity with the rebuilt R17 universe."""

    errors: list[str] = []
    remediation_rows = remediation_payload.get("rows", [])
    worklist_rows = worklist_payload.get("rows", [])
    worklist_summary = worklist_payload.get("summary", {})
    worklist_schema = int(worklist_payload.get("schema_version", 2) or 2)
    _require(
        worklist_payload.get("artifact") == "PORTFOLIO_R17_MULTI_SALE_MIGRATION_WORKLIST",
        "R17 migration worklist artifact id missing",
        errors,
    )
    _require(isinstance(remediation_rows, list), "sold-marker remediation rows must be a list", errors)
    _require(isinstance(worklist_rows, list), "R17 migration worklist rows must be a list", errors)
    if not isinstance(remediation_rows, list) or not isinstance(worklist_rows, list):
        return errors

    remediation_by_key = {
        _remediation_key(row): row
        for row in remediation_rows
        if isinstance(row, dict)
    }
    worklist_by_key = {
        _remediation_key(row): row
        for row in worklist_rows
        if isinstance(row, dict)
    }
    _require(
        len(worklist_by_key) == len(worklist_rows),
        "R17 migration worklist contains duplicate or invalid identities",
        errors,
    )
    _require(
        set(remediation_by_key) == set(worklist_by_key),
        "sold-marker remediation identity set differs from the rebuilt R17 worklist",
        errors,
    )

    selected_sale_ids: list[str] = []
    selected_buy_ids: list[str] = []
    for key, worklist_row in worklist_by_key.items():
        remediation = remediation_by_key.get(key)
        if remediation is None:
            continue
        worklist_lots = worklist_row.get("sale_lots", [])
        remediation_lots = remediation.get("sale_lots", [])
        sale_fields = (
            "sale_transaction_id",
            "sale_timestamp",
            "raw_sold_quantity",
            "sold_quantity",
            "quantity_normalization_factor",
        ) if worklist_schema >= 3 else (
            "sale_transaction_id",
            "sale_timestamp",
            "sold_quantity",
        )
        expected_sales = [
            tuple(
                str(lot.get(field) or "")
                if field in {"sale_transaction_id", "sale_timestamp"}
                else lot.get(field)
                for field in sale_fields
            )
            for lot in worklist_lots
            if isinstance(lot, dict)
        ]
        actual_sales = [
            tuple(
                str(lot.get(field) or "")
                if field in {"sale_transaction_id", "sale_timestamp"}
                else lot.get(field)
                for field in sale_fields
            )
            for lot in remediation_lots
            if isinstance(lot, dict)
        ]
        _require(
            actual_sales == expected_sales,
            f"sold-marker remediation changes or reorders raw sale lots for {key}",
            errors,
        )
        selected_sale_ids.extend(item[0] for item in expected_sales)

        candidate_buys = worklist_row.get(
            "buy_sources" if worklist_schema >= 3 else "candidate_later_buy_sources",
            [],
        )
        expected_buy_quantities = {
            str(item.get("buy_transaction_id") or ""): item
            for item in candidate_buys
            if isinstance(item, dict)
        }
        allocated_quantities: dict[str, int] = defaultdict(int)
        allocation_source_quantities: dict[str, int] = {}
        for allocation in remediation.get("qualifying_fill_allocations", []):
            if not isinstance(allocation, dict):
                continue
            source_id = str(allocation.get("buy_transaction_id") or "")
            allocated_quantities[source_id] += int(allocation.get("quantity", 0) or 0)
            allocation_source_quantities[source_id] = int(allocation.get("source_quantity", 0) or 0)
        unattributed_quantities = {
            str(item.get("buy_transaction_id") or ""): item.get("quantity")
            for item in remediation.get("unattributed_later_buy_inventory", [])
            if isinstance(item, dict)
        }
        non_recovery_quantities = {
            str(item.get("buy_transaction_id") or ""): int(item.get("quantity", 0) or 0)
            for item in remediation.get("non_recovery_buy_inventory", [])
            if isinstance(item, dict)
        }
        represented_ids = set(allocated_quantities) | set(unattributed_quantities) | set(non_recovery_quantities)
        _require(
            represented_ids == set(expected_buy_quantities),
            f"sold-marker remediation drops or invents later-BUY sources for {key}",
            errors,
        )
        for source_id, source in expected_buy_quantities.items():
            source_quantity = source.get("bought_quantity")
            if worklist_schema >= 3:
                _require(
                    allocated_quantities.get(source_id, 0)
                    == int(source.get("recovery_allocated_quantity", 0) or 0),
                    f"sold-marker remediation changes FIFO recovery allocation for {key}/{source_id}",
                    errors,
                )
                _require(
                    non_recovery_quantities.get(source_id, 0)
                    == int(source.get("non_recovery_quantity", 0) or 0),
                    f"sold-marker remediation changes non-recovery BUY quantity for {key}/{source_id}",
                    errors,
                )
                if allocated_quantities.get(source_id, 0):
                    _require(
                        allocation_source_quantities.get(source_id) == source_quantity,
                        f"sold-marker remediation changes normalized BUY source quantity for {key}/{source_id}",
                        errors,
                    )
                _require(
                    int(source.get("recovery_allocated_quantity", 0) or 0)
                    + int(source.get("non_recovery_quantity", 0) or 0)
                    == int(source_quantity or 0),
                    f"R17 worklist BUY source parity fails for {key}/{source_id}",
                    errors,
                )
            elif source_id in allocated_quantities:
                _require(
                    allocation_source_quantities.get(source_id) == source_quantity
                    and allocated_quantities[source_id] <= int(source_quantity or 0),
                    f"sold-marker remediation changes allocated BUY quantity for {key}/{source_id}",
                    errors,
                )
            else:
                _require(
                    unattributed_quantities.get(source_id) == source_quantity,
                    f"sold-marker remediation changes unattributed BUY quantity for {key}/{source_id}",
                    errors,
                )
        selected_buy_ids.extend(expected_buy_quantities)

    _require(
        len(selected_sale_ids) == len(set(selected_sale_ids)),
        "R17 migration worklist repeats a raw sale transaction",
        errors,
    )
    _require(
        len(selected_buy_ids) == len(set(selected_buy_ids)),
        "R17 migration worklist repeats a later-BUY transaction",
        errors,
    )
    _require(
        worklist_summary.get("combined_prior_sale_identity_count") == len(worklist_rows),
        "R17 migration worklist identity count mismatch",
        errors,
    )
    _require(
        worklist_summary.get("selected_sale_lot_count") == len(selected_sale_ids),
        "R17 migration worklist sale-lot count mismatch",
        errors,
    )
    _require(
        worklist_summary.get("candidate_later_buy_source_count") == len(selected_buy_ids),
        "R17 migration worklist later-BUY count mismatch",
        errors,
    )
    if worklist_schema >= 3:
        _require(
            worklist_summary.get("replay_exact_identity_count") == len(worklist_rows),
            "R17 full-history replay exact-identity count mismatch",
            errors,
        )
        _require(
            worklist_summary.get("unmodeled_boundary_or_allocation_identity_count") == 0,
            "R17 full-history worklist retains boundary or allocation gaps",
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
        sale_lot_ids = [
            str(lot.get("sale_lot_id") or "")
            for lot in recovery.get("sale_lots", [])
            if isinstance(lot, dict)
        ]
        result.append({
            "tenant_session_id": key[0],
            "account_id": key[1],
            "orderbook_id": key[2],
            "instrument": recovery.get("instrument"),
            "sale_date": recovery.get("sale_date"),
            "sold_quantity": recovery.get("sold_quantity"),
            "closed_no_reentry_quantity": recovery.get("closed_no_reentry_quantity"),
            "remaining_open_quantity": recovery.get("remaining_open_quantity"),
            "sale_attributed_active_buy_quantity": recovery.get("sale_attributed_active_buy_quantity"),
            "pre_sale_active_buy_quantity": recovery.get("pre_sale_active_buy_quantity"),
            "unattributed_active_buy_quantity": recovery.get("unattributed_active_buy_quantity"),
            "unattributed_later_buy_quantity": recovery.get("unattributed_later_buy_quantity"),
            "recovery_cycle_id": recovery.get("recovery_cycle_id"),
            "sale_lot_ids": sale_lot_ids,
            "recovery_state": recovery.get("state"),
            "recovery_artifact_generated_at": remediation_payload.get("generated_at"),
            "recovery_artifact_verified_at": remediation_payload.get("verified_at"),
            "recovery_no_reentry_decision": recovery.get("no_reentry_decision"),
            "recovery_recorded_stage_percentages_below_marker": recovery.get(
                "recorded_stage_percentages_below_marker"
            ),
            "recovery_recorded_stage_quantities": recovery.get("recorded_stage_quantities"),
            "dynamic_row_found": bool(dynamic),
            "dynamic_buyback_coverage_state": dynamic.get("buyback_coverage_state"),
            "dynamic_low_exposure_decision": dynamic.get("low_exposure_decision"),
            "dynamic_protection_classification": dynamic.get("current_protection_classification"),
            "dynamic_active_buy_volume": dynamic.get("sale_attributed_active_buy_quantity"),
            "dynamic_broker_active_buy_volume": dynamic.get("active_buy_volume"),
            "dynamic_sale_attributed_active_buy_quantity": dynamic.get(
                "sale_attributed_active_buy_quantity"
            ),
            "dynamic_pre_sale_active_buy_quantity": dynamic.get("pre_sale_active_buy_quantity"),
            "dynamic_unattributed_active_buy_quantity": dynamic.get(
                "unattributed_active_buy_quantity"
            ),
            "dynamic_unattributed_later_buy_quantity": dynamic.get(
                "unattributed_later_buy_quantity"
            ),
            "dynamic_recovery_cycle_id": dynamic.get("recovery_cycle_id"),
            "dynamic_sale_lot_ids": dynamic.get("sale_lot_ids"),
            "dynamic_target_rebuild_quantity": dynamic.get("target_rebuild_quantity"),
            "dynamic_latest_recent_sale_date": dynamic.get("latest_recent_sale_date"),
            "dynamic_stages_percent_below_sold_marker": dynamic.get("stages_percent_below_sold_marker"),
            "dynamic_stage_quantities": dynamic.get("stage_quantities"),
            "dynamic_coverage_reason": dynamic.get("coverage_reason"),
            "dynamic_artifact_generated_at": dynamic_payload.get("generated_at"),
            "dynamic_no_reentry_decision": dynamic.get("no_reentry_decision"),
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
        dynamic_attributed = row.get(
            "dynamic_sale_attributed_active_buy_quantity",
            row.get("dynamic_active_buy_volume"),
        )
        reasons: list[str] = []
        if row.get("dynamic_row_found") is not True:
            reasons.append("dynamic buyback row is missing")
        if (
            dynamic_attributed != row.get("sale_attributed_active_buy_quantity")
        ):
            reasons.append("sale-attributed active BUY quantity does not match the recovery cycle")
        if (
            row.get("dynamic_broker_active_buy_volume", row.get("dynamic_active_buy_volume"))
            != sum(
                int(row.get(field, 0) or 0)
                for field in (
                    "dynamic_sale_attributed_active_buy_quantity",
                    "dynamic_pre_sale_active_buy_quantity",
                    "dynamic_unattributed_active_buy_quantity",
                )
            )
        ):
            reasons.append("broker active BUY total is not separated into governed inventory classes")
        if row.get("dynamic_recovery_cycle_id") != row.get("recovery_cycle_id"):
            reasons.append("dynamic recovery cycle id does not match the remediation source")
        if row.get("dynamic_sale_lot_ids") != row.get("sale_lot_ids"):
            reasons.append("dynamic coverage drops or reorders sale lots from the recovery cycle")
        if (
            row.get("dynamic_low_exposure_decision") == "EXIT_OR_NO_REENTRY_REVIEW"
            and _is_positive_number(dynamic_attributed)
        ):
            reasons.append("exit/no-reentry classification is contradicted by active recovery inventory")

        if state.startswith("REPAIR_REQUIRED"):
            reasons.append("sold-marker path remains REPAIR_REQUIRED")
        elif state == "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET":
            reasons.append("material sold-marker path remains PERCENTAGE_NOT_SET")
        elif state == "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED":
            reasons.append("named sold-marker path remains blocked for fresh named review")
        elif state.startswith("PARTIAL_SOLD_SLICE_RECOVERY") and remaining > 0:
            reasons.append("partial recovery retains an uncovered sold-slice remainder")
        elif state == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED":
            reasons.extend(_dormant_ladder_governance_gaps(row))
        elif state == "EXPLICIT_NO_REENTRY_CURRENT_THESIS":
            reason = str(row.get("dynamic_coverage_reason") or "").lower()
            reference_time = _latest_time(
                row.get("recovery_artifact_generated_at"),
                row.get("recovery_artifact_verified_at"),
                row.get("dynamic_artifact_generated_at"),
            )
            reasons.extend(
                _no_reentry_decision_gaps(
                    {
                        **row,
                        "sold_quantity": row.get("closed_no_reentry_quantity"),
                    },
                    row.get("recovery_no_reentry_decision"),
                    reference_time,
                )
            )
            if row.get("dynamic_no_reentry_decision") != row.get("recovery_no_reentry_decision"):
                reasons.append("dynamic no-reentry decision does not exactly match the remediation source")
            if (
                remaining != 0
                or row.get("dynamic_buyback_coverage_state") != "LEDGER_ONLY"
                or row.get("dynamic_low_exposure_decision") != "EXIT_OR_NO_REENTRY_REVIEW"
                or dynamic_attributed != 0
                or row.get("dynamic_stages_percent_below_sold_marker") != "PERCENTAGE_NOT_SET"
                or row.get("dynamic_target_rebuild_quantity") is not None
                or row.get("dynamic_latest_recent_sale_date") != row.get("sale_date")
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
    try:
        dynamic_schema = int(dynamic_payload.get("schema_version", 1) or 1)
    except (TypeError, ValueError):
        dynamic_schema = 1
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
            row.get("dynamic_sale_attributed_active_buy_quantity")
            == row.get("sale_attributed_active_buy_quantity"),
            f"dynamic sale-attributed active BUY mismatch for sold-marker recovery {key}",
            errors,
        )
        _require(
            row.get("dynamic_pre_sale_active_buy_quantity") == row.get("pre_sale_active_buy_quantity")
            and row.get("dynamic_unattributed_active_buy_quantity")
            == row.get("unattributed_active_buy_quantity")
            and row.get("dynamic_unattributed_later_buy_quantity")
            == row.get("unattributed_later_buy_quantity"),
            f"dynamic unattributed BUY inventories mismatch for sold-marker recovery {key}",
            errors,
        )
        _require(
            row.get("dynamic_broker_active_buy_volume", row.get("dynamic_active_buy_volume"))
            == sum(
                int(row.get(field, 0) or 0)
                for field in (
                    "dynamic_sale_attributed_active_buy_quantity",
                    "dynamic_pre_sale_active_buy_quantity",
                    "dynamic_unattributed_active_buy_quantity",
                )
            ),
            f"dynamic broker active BUY total is not fully classified for sold-marker recovery {key}",
            errors,
        )
        _require(
            row.get("dynamic_recovery_cycle_id") == row.get("recovery_cycle_id"),
            f"dynamic recovery cycle id mismatch for sold-marker recovery {key}",
            errors,
        )
        _require(
            row.get("dynamic_sale_lot_ids") == row.get("sale_lot_ids"),
            f"dynamic sale-lot set mismatch for sold-marker recovery {key}",
            errors,
        )
        reason = str(row.get("dynamic_coverage_reason") or "").lower()
        if state.startswith("REPAIR_REQUIRED"):
            _require(
                row.get("dynamic_buyback_coverage_state") == "REPAIR_REQUIRED"
                and row.get("dynamic_low_exposure_decision") == "REPAIR_REQUIRED"
                and (
                    dynamic_schema >= 5
                    or row.get("dynamic_protection_classification") == "REPAIR_REQUIRED"
                ),
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
        elif state == "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED":
            _require(
                row.get("dynamic_buyback_coverage_state") == "LADDER_GAP"
                and row.get("dynamic_low_exposure_decision") == "NAMED_EXCEPTION"
                and row.get("dynamic_protection_classification") == "NAMED_EXCEPTION"
                and row.get("dynamic_stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET",
                f"named sold-marker path is not separately review-blocked for {key}",
                errors,
            )
            _require(
                row.get("dynamic_target_rebuild_quantity") == row.get("remaining_open_quantity"),
                f"named path target does not preserve exact open sold quantity for {key}",
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
                "sale-attributed" in reason
                and bool(row.get("dynamic_recovery_cycle_id"))
                and bool(row.get("dynamic_sale_lot_ids")),
                f"partial sold-slice provenance is missing from dynamic coverage for {key}",
                errors,
            )
        elif state == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED":
            for gap in _dormant_ladder_governance_gaps(row):
                errors.append(f"dormant sold-marker ladder is not fully governed for {key}: {gap}")
        elif state == "EXPLICIT_NO_REENTRY_CURRENT_THESIS":
            _require(
                row.get("dynamic_buyback_coverage_state") == "LEDGER_ONLY"
                and row.get("dynamic_low_exposure_decision") == "EXIT_OR_NO_REENTRY_REVIEW"
                and row.get("dynamic_sale_attributed_active_buy_quantity") == 0
                and row.get("dynamic_target_rebuild_quantity") is None
                and row.get("dynamic_stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET",
                f"explicit no-reentry row is contradicted by dynamic coverage for {key}",
                errors,
            )
            _require(
                row.get("dynamic_latest_recent_sale_date") == row.get("sale_date"),
                f"explicit no-reentry sale date is missing or mismatched in dynamic coverage for {key}",
                errors,
            )
            _require(
                row.get("dynamic_no_reentry_decision") == row.get("recovery_no_reentry_decision"),
                f"explicit no-reentry decision differs between remediation and dynamic coverage for {key}",
                errors,
            )
            _require(
                "no-reentry" in reason or "no re-entry" in reason,
                f"explicit no-reentry reason is missing from dynamic coverage for {key}",
                errors,
            )
    return errors


def validate_r17_path_links(
    dynamic_payload: dict[str, Any],
    remediation_payload: dict[str, Any],
    path_payload: dict[str, Any],
) -> list[str]:
    """Prove both current ledgers link the exact latest open-lot path source."""

    errors = validate_r17_open_path_evidence(path_payload)
    try:
        dynamic_schema = int(dynamic_payload.get("schema_version", 1) or 1)
        remediation_schema = int(remediation_payload.get("schema_version", 1) or 1)
    except (TypeError, ValueError):
        dynamic_schema = remediation_schema = 1
    _require(dynamic_schema >= 5, "dynamic buyback schema does not require complete-path evidence", errors)
    _require(remediation_schema >= 4, "sold-marker remediation schema does not require complete-path evidence", errors)

    path_rows = {
        _path_evidence_key(row): row
        for row in path_payload.get("rows", [])
        if isinstance(row, dict)
    }
    dynamic_rows = {
        _remediation_key(row): row
        for row in dynamic_payload.get("rows", [])
        if isinstance(row, dict) and _is_positive_number(row.get("target_rebuild_quantity"))
    }
    remediation_rows = {
        _remediation_key(row): row
        for row in remediation_payload.get("rows", [])
        if isinstance(row, dict) and _is_positive_integer(row.get("remaining_open_quantity"))
    }
    _require(set(dynamic_rows) == set(path_rows), "dynamic open-row set does not match R17 path evidence", errors)
    _require(set(remediation_rows) == set(path_rows), "remediation open-row set does not match R17 path evidence", errors)

    path_generated = _artifact_time(path_payload.get("generated_at"))
    dynamic_generated = _artifact_time(dynamic_payload.get("generated_at"))
    remediation_generated = _artifact_time(remediation_payload.get("generated_at"))
    if path_generated is not None and dynamic_generated is not None:
        _require(dynamic_generated >= path_generated, "dynamic coverage predates R17 path evidence", errors)
    if path_generated is not None and remediation_generated is not None:
        _require(remediation_generated >= path_generated, "remediation coverage predates R17 path evidence", errors)

    for key, source in path_rows.items():
        dynamic = dynamic_rows.get(key)
        remediation = remediation_rows.get(key)
        if dynamic is None or remediation is None:
            continue
        dynamic_evidence = dynamic.get("full_path_evidence")
        remediation_evidence = remediation.get("full_path_evidence")
        _require(
            dynamic_evidence == remediation_evidence,
            f"dynamic and remediation path evidence differ for {key}",
            errors,
        )
        if not isinstance(dynamic_evidence, dict):
            continue
        exact_lots = source.get("exact_lots") if isinstance(source.get("exact_lots"), list) else []
        expected_fields = {
            "source_generated_at": path_payload.get("generated_at"),
            "chart_from": source.get("chart_from"),
            "chart_to": source.get("chart_to"),
            "chart_point_count": source.get("chart_point_count"),
            "remaining_open_quantity": source.get("remaining_open_quantity"),
            "remaining_open_lot_count": source.get("remaining_open_lot_count"),
            "open_sale_transaction_ids": [
                str(lot.get("sale_transaction_id") or "")
                for lot in exact_lots
                if isinstance(lot, dict)
            ],
            "active_buy_quantity": source.get("active_buy_quantity"),
            "sale_attributed_active_buy_quantity": source.get(
                "sale_attributed_active_buy_quantity"
            ),
            "maximum_open_lot_drop_percent": source.get("maximum_open_lot_drop_percent"),
            "current_drop_below_weighted_marker_percent": source.get(
                "current_drop_below_weighted_marker_percent"
            ),
            "open_lots_crossing_8pct_alarm": source.get("open_lots_crossing_8pct_alarm"),
            "crossed_8pct_review_alarm": source.get("crossed_8pct_review_alarm"),
            "technical": source.get("technical"),
            "rsi": source.get("rsi"),
            "atr20_percent_of_current_close": source.get("atr20_percent_of_current_close"),
            "named_exception": source.get("named_exception"),
            "path_state": source.get("path_state"),
        }
        for field, expected in expected_fields.items():
            _require(
                dynamic_evidence.get(field) == expected,
                f"embedded R17 path field {field} differs from source for {key}",
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
    try:
        schema_version = int(payload.get("schema_version", 1) or 1)
    except (TypeError, ValueError):
        schema_version = 1
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
    dynamic_reference_time = _latest_time(payload.get("generated_at"), payload.get("live_state_as_of"))
    _require(dynamic_reference_time is not None, "dynamic buyback reference time is invalid", errors)
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
    full_path_evidence_count = 0
    crossed_path_count = 0
    missed_path_repair_count = 0
    named_path_review_count = 0
    vectors_by_instrument: dict[tuple[float, ...], set[str]] = defaultdict(set)
    required_fields = {
        "tenant_session_id",
        "account_id",
        "instrument",
        "orderbook_id",
        "live_holding",
        "market_value_band",
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
    if schema_version >= 3:
        required_fields.add("live_market_value_sek")
    if schema_version >= 4:
        required_fields.add("economic_resolution")
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
        if schema_version >= 3:
            live_holding = row.get("live_holding")
            market_value = row.get("live_market_value_sek")
            _require(
                isinstance(market_value, (int, float)) and market_value >= 0,
                f"dynamic live market value invalid for {key}",
                errors,
            )
            if isinstance(live_holding, (int, float)) and isinstance(market_value, (int, float)):
                expected_band = (
                    "ZERO_POSITION"
                    if live_holding == 0
                    else "BELOW_20000_SEK"
                    if market_value < 20000
                    else "AT_OR_ABOVE_20000_SEK"
                )
                _require(
                    row.get("market_value_band") == expected_band,
                    f"dynamic market-value band contradicts live SEK value for {key}",
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
        if schema_version >= 3 and isinstance(reasons, list):
            _require(
                ("BELOW_20000_SEK" in reasons)
                == (row.get("market_value_band") == "BELOW_20000_SEK"),
                f"dynamic low-exposure selection reason contradicts market-value band for {key}",
                errors,
            )
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

        if schema_version >= 4:
            resolution = row.get("economic_resolution")
            _require(isinstance(resolution, dict), f"dynamic economic resolution missing for {key}", errors)
            if isinstance(resolution, dict):
                _require(
                    resolution.get("state") == row.get("low_exposure_decision"),
                    f"dynamic economic resolution state mismatch for {key}",
                    errors,
                )
                for field in ("source", "reason", "next_review"):
                    _require(
                        bool(str(resolution.get(field) or "").strip()),
                        f"dynamic economic resolution {field} missing for {key}",
                        errors,
                    )
            if row.get("low_exposure_decision") == "INTENTIONAL_MARKER_OR_CORE_HOLD":
                _require(
                    row.get("buyback_coverage_state") == "LEDGER_ONLY"
                    and row.get("current_protection_classification")
                    in {"CORE_HOLD_EXCEPTION", "MARKER_EXCEPTION"}
                    and row.get("target_rebuild_quantity") is None
                    and row.get("stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET"
                    and row.get("active_buy_volume", 0) == 0
                    and row.get("sale_attributed_active_buy_quantity", 0) == 0,
                    f"dynamic intentional hold is contradicted by open recovery work for {key}",
                    errors,
                )
            if row.get("low_exposure_decision") == "BUILD_REVIEW":
                _require(
                    row.get("buyback_coverage_state") in {"LADDER_ACTIVE", "LADDER_DORMANT"}
                    and isinstance(row.get("stages_percent_below_sold_marker"), list)
                    and isinstance(row.get("stage_quantities"), list),
                    f"dynamic BUILD_REVIEW lacks a quantified stock-specific ladder for {key}",
                    errors,
                )

        if schema_version >= 5 and _is_positive_number(row.get("target_rebuild_quantity")):
            evidence = row.get("full_path_evidence")
            path_errors = _validate_embedded_path_evidence(
                evidence,
                key=key,
                expected_quantity=row.get("target_rebuild_quantity"),
            )
            errors.extend(path_errors)
            if isinstance(evidence, dict):
                full_path_evidence_count += 1
                crossed = evidence.get("crossed_8pct_review_alarm") is True
                named = evidence.get("named_exception") is True
                crossed_path_count += crossed
                missed_path_repair_count += crossed and not named
                named_path_review_count += crossed and named
                _require(
                    named == (row.get("current_protection_classification") == "NAMED_EXCEPTION"),
                    f"dynamic named-exception path flag mismatch for {key}",
                    errors,
                )
                _require(
                    evidence.get("active_buy_quantity") == row.get("active_buy_volume"),
                    f"dynamic active BUY quantity differs from complete-path evidence for {key}",
                    errors,
                )
                _require(
                    evidence.get("sale_attributed_active_buy_quantity")
                    == row.get("sale_attributed_active_buy_quantity", 0),
                    f"dynamic sale-attributed BUY quantity differs from complete-path evidence for {key}",
                    errors,
                )
                if crossed and not named:
                    _require(
                        row.get("buyback_coverage_state") == "REPAIR_REQUIRED"
                        and row.get("low_exposure_decision") == "REPAIR_REQUIRED",
                        f"crossed ordinary complete path is not REPAIR_REQUIRED for {key}",
                        errors,
                    )
                elif crossed:
                    _require(
                        row.get("buyback_coverage_state") == "LADDER_GAP"
                        and row.get("low_exposure_decision") == "NAMED_EXCEPTION"
                        and row.get("current_protection_classification") == "NAMED_EXCEPTION",
                        f"crossed named complete path is not separately review-blocked for {key}",
                        errors,
                    )
        elif schema_version >= 5:
            _require(
                row.get("full_path_evidence") is None,
                f"closed sold-cycle row carries unexpected open-lot path evidence for {key}",
                errors,
            )

        if (
            row.get("low_exposure_decision") == "EXIT_OR_NO_REENTRY_REVIEW"
            and _is_positive_number(
                row.get("sale_attributed_active_buy_quantity", row.get("active_buy_volume"))
            )
        ):
            errors.append(f"dynamic exit/no-reentry row is contradicted by active same-sale BUY inventory for {key}")

        if _is_terminal_no_reentry_dynamic_row(row):
            decision = row.get("no_reentry_decision")
            decision_sold_quantity = decision.get("sold_quantity") if isinstance(decision, dict) else None
            identity = {
                "tenant_session_id": tenant,
                "account_id": account,
                "orderbook_id": orderbook,
                "sale_date": row.get("latest_recent_sale_date"),
                "sold_quantity": decision_sold_quantity,
                "remaining_open_quantity": 0,
            }
            for gap in _no_reentry_decision_gaps(identity, decision, dynamic_reference_time):
                errors.append(f"dynamic no-reentry decision is invalid for {key}: {gap}")
            _require(
                row.get("low_exposure_decision") == "EXIT_OR_NO_REENTRY_REVIEW"
                and row.get("buyback_coverage_state") == "LEDGER_ONLY"
                and row.get("sale_attributed_active_buy_quantity", row.get("active_buy_volume")) == 0
                and row.get("target_rebuild_quantity") is None
                and row.get("stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET",
                f"dynamic terminal no-reentry state is incomplete or contradicted for {key}",
                errors,
            )

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
    if schema_version >= 3:
        at_or_above_rows = sum(
            row.get("market_value_band") == "AT_OR_ABOVE_20000_SEK"
            for row in rows
            if isinstance(row, dict)
        )
        _require(
            summary.get("at_or_above_20000_sek_rows") == at_or_above_rows,
            "dynamic summary at-or-above-20k count mismatch",
            errors,
        )
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
    if schema_version >= 5:
        open_target_rows = sum(
            _is_positive_number(row.get("target_rebuild_quantity"))
            for row in rows
            if isinstance(row, dict)
        )
        _require(
            summary.get("full_path_evidence_rows") == full_path_evidence_count == open_target_rows,
            "dynamic full-path evidence count mismatch",
            errors,
        )
        _require(
            summary.get("rows_crossing_8pct_review_alarm") == crossed_path_count,
            "dynamic crossed-path count mismatch",
            errors,
        )
        _require(
            summary.get("repair_required_missed_path_rows") == missed_path_repair_count,
            "dynamic missed-path repair count mismatch",
            errors,
        )
        _require(
            summary.get("sold_cycle_repair_required_rows") == missed_path_repair_count,
            "dynamic sold-cycle repair count mismatch",
            errors,
        )
        _require(
            summary.get("named_exception_path_review_rows") == named_path_review_count,
            "dynamic named path-review count mismatch",
            errors,
        )
        _require(
            summary.get("rows_without_crossing_8pct_review_alarm")
            == full_path_evidence_count - crossed_path_count,
            "dynamic noncrossed path count mismatch",
            errors,
        )
        _require(
            summary.get("path_evidence_missing_rows") == 0,
            "dynamic path-evidence missing count must be zero",
            errors,
        )
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
                open_path_path = latest_r17_open_path_evidence_path()
                if open_path_path is None:
                    errors.append(
                        f"R17 open-sale path evidence missing for glob: {R17_OPEN_PATH_EVIDENCE_GLOB}"
                    )
                else:
                    open_path_payload = json.loads(open_path_path.read_text(encoding="utf-8"))
                    errors.extend(
                        validate_r17_path_links(
                            dynamic_payload,
                            remediation_payload,
                            open_path_payload,
                        )
                    )
                full_path_path = latest_sold_marker_full_path_path()
                if full_path_path is None:
                    errors.append(
                        f"sold-marker full-path source missing for glob: {SOLD_MARKER_FULL_PATH_GLOB}"
                    )
                else:
                    full_path_payload = json.loads(full_path_path.read_text(encoding="utf-8"))
                    errors.extend(
                        validate_sold_marker_universe_against_full_path(
                            remediation_payload,
                            full_path_payload,
                        )
                    )
                worklist_path = latest_r17_migration_worklist_path()
                if worklist_path is None:
                    errors.append(
                        f"R17 migration worklist missing for glob: {R17_MIGRATION_WORKLIST_GLOB}"
                    )
                else:
                    worklist_payload = json.loads(worklist_path.read_text(encoding="utf-8"))
                    errors.extend(
                        validate_sold_marker_remediation_against_worklist(
                            remediation_payload,
                            worklist_payload,
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
