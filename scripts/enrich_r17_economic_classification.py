#!/usr/bin/env python3
"""Attach current economic exposure decisions to an R17 buyback artifact.

The full-history replay answers which sale lots remain unmatched. This script
answers a different question: whether the current account-position is an
intentional hold, a named/non-stop exception, or still requires repair. It is
strictly local and never accesses or mutates broker state.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / ".avanza_position_strategy.json"
HOLD_PROTECTION_CLASSES = {"CORE_HOLD_EXCEPTION", "MARKER_EXCEPTION"}
FULL_EXIT_PROTECTION_CLASSES = {"FULL_EXIT_REVIEW", "NON_STOP_ELIGIBLE_FULL_EXIT"}
REGISTRY_PROTECTION_CLASSES = {
    "CALIBRATED_STOP_PROFIT_LADDER",
    "CORE_HOLD_EXCEPTION",
    "MARKER_EXCEPTION",
    "NAMED_EXCEPTION",
    "NON_STOP_ELIGIBLE",
    "REPAIR_REQUIRED",
}
NAMED_PATH_STATE = "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
MISSED_PATH_STATE = "MISSED_PATH_REPAIR_REQUIRED"
OPEN_PATH_STATE = "LADDER_GAP_PERCENTAGE_NOT_SET"
TERMINAL_DECISION_ARTIFACT = "PORTFOLIO_R17_TERMINAL_DECISIONS"
TERMINAL_DECISION_MAX_VALIDITY = timedelta(days=14)
SMALL_HOLD_MAX_REGULAR_SESSIONS = 5
SMALL_HOLD_CALENDAR_BASIS = "CONSERVATIVE_WEEKDAY_CEILING"
STOCKHOLM = ZoneInfo("Europe/Stockholm")
PATH_CONTEXT_FIELD = "instrument_specific_path_context"
PATH_RECONCILIATION_MARKER = " Complete authenticated path evidence records a maximum "


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exact_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid exact quantity: {value!r}") from exc


def _exact_text(value: Any) -> str:
    decimal = _exact_decimal(value)
    text = format(decimal, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _relative_source(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _source_rows(source_paths: dict[str, Path] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for role, path in (source_paths or {}).items():
        rows.append(
            {
                "path": _relative_source(path),
                "sha256": _file_sha256(path),
                "role": role,
            }
        )
    return rows


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _registry_position(
    registry: dict[str, Any], account_id: str, orderbook_id: str
) -> dict[str, Any] | None:
    position = (
        registry.get("accounts", {})
        .get(account_id, {})
        .get("positions", {})
        .get(orderbook_id)
    )
    return position if isinstance(position, dict) else None


def _row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("tenant_session_id") or ""),
        str(row.get("account_id") or ""),
        str(row.get("orderbook_id") or ""),
    )


def _is_positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _clear_instrument_specific_path_context(row: dict[str, Any]) -> None:
    """Discard path context whenever the exact residual lot set changes."""

    context = row.get(PATH_CONTEXT_FIELD)
    if isinstance(context, dict) and str(context.get("coverage_reason") or "").strip():
        row["coverage_reason"] = str(context["coverage_reason"]).strip()
    row.pop(PATH_CONTEXT_FIELD, None)
    resolution = row.get("economic_resolution")
    if isinstance(resolution, dict) and PATH_CONTEXT_FIELD in resolution:
        resolution = dict(resolution)
        resolution.pop(PATH_CONTEXT_FIELD, None)
        row["economic_resolution"] = resolution


def _base_path_coverage_reason(value: Any) -> str:
    """Return the stock-specific reason without a prior derived path suffix."""

    reason = str(value or "").strip()
    if PATH_RECONCILIATION_MARKER in reason:
        reason = reason.split(PATH_RECONCILIATION_MARKER, 1)[0].rstrip()
    return reason


def _quantified_ladder_is_valid(row: dict[str, Any]) -> bool:
    """Prove that one current active/dormant ladder covers its exact target."""

    target = row.get("target_rebuild_quantity")
    stages = row.get("stages_percent_below_sold_marker")
    quantities = row.get("stage_quantities")
    quantified = (
        _is_positive_integer(target)
        and isinstance(stages, list)
        and 1 <= len(stages) <= 3
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) > 0
            for value in stages
        )
        and all(float(left) < float(right) for left, right in zip(stages, stages[1:]))
        and isinstance(quantities, list)
        and len(quantities) == len(stages)
        and all(_is_positive_integer(value) for value in quantities)
        and sum(quantities) == target
    )
    if not quantified:
        return False
    if row.get("buyback_coverage_state") in {"LADDER_ACTIVE", "LADDER_DORMANT"}:
        return True
    return (
        row.get("buyback_coverage_state") == "REPAIR_REQUIRED"
        and _independent_dormant_ladder_decision_is_valid(
            row,
            target=target,
            stages=stages,
            quantities=quantities,
        )
    )


def _independent_dormant_ladder_decision_is_valid(
    row: dict[str, Any],
    *,
    target: Any,
    stages: Any,
    quantities: Any,
) -> bool:
    """Validate future rebuild intent independently from historical path repair."""

    decision = row.get("dormant_ladder_decision")
    lot_ids = decision.get("exact_open_sale_lot_ids") if isinstance(decision, dict) else None
    return (
        isinstance(decision, dict)
        and decision.get("authority") == "LOCAL_REVIEW_ONLY"
        and decision.get("broker_mutation") is False
        and decision.get("trade_authority") is False
        and decision.get("state") == "LADDER_DORMANT"
        and decision.get("economic_state") == "BUILD_REVIEW"
        and str(decision.get("tenant_session_id") or "")
        == str(row.get("tenant_session_id") or "")
        and str(decision.get("account_id") or "") == str(row.get("account_id") or "")
        and str(decision.get("orderbook_id") or "") == str(row.get("orderbook_id") or "")
        and str(decision.get("recovery_cycle_id") or "")
        == str(row.get("recovery_cycle_id") or "")
        and decision.get("target_rebuild_quantity") == target
        and decision.get("stages_percent_below_sold_marker") == stages
        and decision.get("stage_quantities") == quantities
        and isinstance(lot_ids, list)
        and bool(lot_ids)
        and len(lot_ids) == len(set(str(value) for value in lot_ids))
        and all(str(value or "") for value in lot_ids)
        and all(
            bool(str(decision.get(field) or "").strip())
            for field in (
                "decision_id",
                "calibration_evidence",
                "promotion_evidence",
                "rejection_evidence",
                "next_review",
                "expires_at",
            )
        )
    )


def _independent_dormant_ladder_semantics(row: dict[str, Any]) -> tuple[str, str]:
    """Return the canonical build rationale without erasing path-repair state."""

    decision = row.get("dormant_ladder_decision")
    if not isinstance(decision, dict):
        raise ValueError(f"independent dormant ladder decision is missing for {_row_key(row)}")
    instrument = str(row.get("instrument") or decision.get("instrument") or "").strip()
    calibration = str(decision.get("calibration_evidence") or "").strip()
    next_review = str(decision.get("next_review") or "").strip()
    if not instrument or not calibration or not next_review:
        raise ValueError(f"independent dormant ladder semantics are incomplete for {_row_key(row)}")
    return f"{instrument}: {calibration}", next_review


def _remediation_dormant_ladder_is_valid(row: dict[str, Any]) -> bool:
    """Prove that the sale-lot ledger carries a complete dormant review ladder."""

    stages = row.get("recorded_stage_percentages_below_marker")
    quantities = row.get("recorded_stage_quantities")
    remaining = row.get("remaining_open_quantity")
    quantified = (
        _is_positive_integer(remaining)
        and isinstance(stages, list)
        and 1 <= len(stages) <= 3
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) > 0
            for value in stages
        )
        and all(float(left) < float(right) for left, right in zip(stages, stages[1:]))
        and isinstance(quantities, list)
        and len(quantities) == len(stages)
        and all(_is_positive_integer(value) for value in quantities)
        and sum(quantities) == remaining
    )
    return quantified and (
        row.get("state") == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
        or _independent_dormant_ladder_decision_is_valid(
            row,
            target=remaining,
            stages=stages,
            quantities=quantities,
        )
    )


def _instrument_specific_path_context(
    row: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Preserve the stock-specific decision that complete-path evidence refines."""

    instrument = str(row.get("instrument") or "").strip()
    context = row.get(PATH_CONTEXT_FIELD)
    context = context if isinstance(context, dict) else {}
    reason = _base_path_coverage_reason(
        context.get("coverage_reason") or row.get("coverage_reason")
    )
    next_gate = str(row.get("exact_next_gate") or "").strip()
    if not instrument or not reason or not next_gate:
        raise ValueError(f"R17 complete-path row lacks stock-specific decision evidence for {_row_key(row)}")
    if instrument.casefold() not in reason.casefold():
        raise ValueError(f"R17 complete-path reason is not instrument-specific for {_row_key(row)}")

    resolution = row.get("economic_resolution")
    resolution = resolution if isinstance(resolution, dict) else {}
    return {
        "instrument": instrument,
        "source": resolution.get("source"),
        "registry_updated_at": resolution.get("registry_updated_at"),
        "coverage_reason": reason,
        "exact_next_gate": next_gate,
        "remaining_open_quantity": evidence.get("remaining_open_quantity"),
        "remaining_open_lot_count": evidence.get("remaining_open_lot_count"),
        "maximum_open_lot_drop_percent": evidence.get("maximum_open_lot_drop_percent"),
        "current_drop_below_weighted_marker_percent": evidence.get(
            "current_drop_below_weighted_marker_percent"
        ),
    }


def _path_reconciled_reason(context: dict[str, Any], *, named: bool) -> str:
    maximum = context.get("maximum_open_lot_drop_percent")
    maximum_text = f"{float(maximum):.2f}%" if isinstance(maximum, (int, float)) else "an authenticated amount"
    suffix = (
        f" Complete authenticated path evidence records a maximum {maximum_text} drop below the "
        "applicable sold markers and an unserved 8% review-alarm crossing. A later rebound does not "
        "erase that crossing."
    )
    if named:
        suffix += " The named-instrument restrictions remain binding."
    return f"{context['coverage_reason']}{suffix}"


def _remediation_summary(
    rows: list[dict[str, Any]],
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = dict(existing or {})
    repair_rows = [row for row in rows if str(row.get("state") or "").startswith("REPAIR_REQUIRED")]
    percentage_gap_rows = [
        row for row in rows if row.get("state") == "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET"
    ]
    named_path_rows = [
        row for row in rows if row.get("state") == "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
    ]
    dormant_ladder_rows = [
        row
        for row in rows
        if row.get("state") == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
    ]
    no_reentry_rows = [
        row for row in rows if row.get("state") == "EXPLICIT_NO_REENTRY_CURRENT_THESIS"
    ]
    partial_rows = [
        row for row in rows if str(row.get("state") or "").startswith("PARTIAL_SOLD_SLICE_RECOVERY")
    ]
    open_rows = [row for row in rows if _is_positive_integer(row.get("remaining_open_quantity"))]
    percentage_not_set_rows = [
        row
        for row in open_rows
        if row.get("recorded_stage_percentages_below_marker") in (None, "PERCENTAGE_NOT_SET")
    ]
    path_rows = [row for row in open_rows if isinstance(row.get("full_path_evidence"), dict)]
    crossed_rows = [
        row
        for row in path_rows
        if row["full_path_evidence"].get("crossed_8pct_review_alarm") is True
    ]

    summary.update(
        {
            "repair_required_missed_path_rows": len(repair_rows),
            "sold_cycle_repair_required_rows": len(repair_rows),
            "percentage_not_set_open_rows": len(percentage_not_set_rows),
            "material_path_open_rows": len(percentage_gap_rows),
            "named_exception_path_review_rows": len(named_path_rows),
            "dormant_stock_specific_review_ladder_rows": len(dormant_ladder_rows),
            "explicit_no_reentry_rows": len(no_reentry_rows),
            "partial_sale_attributed_active_rows": len(partial_rows),
            "open_material_rows": len(open_rows),
            "remaining_open_quantity_across_material_rows": sum(
                int(row.get("remaining_open_quantity", 0) or 0) for row in open_rows
            ),
            "full_path_evidence_rows": len(path_rows),
            "rows_crossing_8pct_review_alarm": len(crossed_rows),
            "path_evidence_missing_rows": len(open_rows) - len(path_rows),
        }
    )
    return summary


def _remediation_conclusion(summary: dict[str, Any]) -> str:
    identities = int(summary.get("exact_account_rows_with_prior_same_account_sales", 0) or 0)
    lots = int(summary.get("modeled_sale_lots", 0) or 0)
    filled = int(summary.get("qualifying_filled_quantity_total", 0) or 0)
    remaining = int(summary.get("remaining_open_quantity_across_material_rows", 0) or 0)
    return (
        f"R17 B9/B11 boundary and allocation gaps are cleared for all {identities:,} governed "
        f"prior-sale identities. The ledger preserves {lots:,} raw sale lots, split-normalized "
        f"parity, {filled:,} filled recovery shares and {remaining:,} still-open shares. Open "
        "percentage design and economic position decisions remain fail-closed; no broker or paper "
        "mutation occurred."
    )


def _artifact_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _terminal_decision_rows(
    payload: dict[str, Any],
    *,
    reference_time: str,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Validate a review-only overlay that closes exact residual sale-lot slices."""

    if payload.get("artifact") != TERMINAL_DECISION_ARTIFACT:
        raise ValueError("terminal decision input is not an R17 terminal-decision artifact")
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2, 3}:
        raise ValueError("R17 terminal-decision schema version must be 1, 2 or 3")
    if payload.get("authority") != "LOCAL_REVIEW_ONLY":
        raise ValueError("R17 terminal decisions must remain LOCAL_REVIEW_ONLY")
    if payload.get("broker_mutation") is not False or payload.get("trade_authority") is not False:
        raise ValueError("R17 terminal decisions must record zero broker/trade authority")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("R17 terminal decisions must contain rows")

    reference = _artifact_time(reference_time)
    if reference is None:
        raise ValueError("R17 terminal-decision reference time is invalid")
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    reallocation_ids: set[str] = set()
    active_allocation_ids: set[str] = set()
    closure_ids: set[str] = set()
    renewal_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("R17 terminal decisions contain a non-object row")
        key = _row_key(row)
        if not all(key) or key in result:
            raise ValueError(f"R17 terminal-decision key is invalid or duplicated: {key}")
        for field in (
            "instrument",
            "recovery_cycle_id",
            "decision_id",
            "decision_basis",
            "thesis_evidence",
            "event_evidence",
            "technical_evidence",
            "path_evidence",
        ):
            if not str(row.get(field) or "").strip():
                raise ValueError(f"R17 terminal-decision {field} is missing for {key}")
        decision_at = _artifact_time(row.get("decision_at"))
        revalidated_at = _artifact_time(row.get("last_revalidated_at"))
        expires_at = _artifact_time(row.get("expires_at"))
        if None in (decision_at, revalidated_at, expires_at):
            raise ValueError(f"R17 terminal-decision timestamps are invalid for {key}")
        assert decision_at is not None and revalidated_at is not None and expires_at is not None
        try:
            if revalidated_at < decision_at or revalidated_at > reference:
                raise ValueError(f"R17 terminal-decision revalidation ordering is invalid for {key}")
            if expires_at <= revalidated_at or expires_at - revalidated_at > TERMINAL_DECISION_MAX_VALIDITY:
                raise ValueError(f"R17 terminal-decision expiry is invalid for {key}")
        except TypeError as exc:
            raise ValueError(f"R17 terminal-decision timezone forms are incompatible for {key}") from exc
        if row.get("newer_evidence_reviewed") is not True or row.get("contradiction_status") != "NONE":
            raise ValueError(f"R17 terminal-decision evidence is not current and contradiction-free for {key}")
        if not _is_nonnegative_integer(row.get("current_holding")):
            raise ValueError(f"R17 terminal-decision current holding is invalid for {key}")
        closures = row.get("sale_lot_closures", [])
        reallocations = row.get("qualifying_fill_reallocations", [])
        active_allocations = row.get("active_recovery_allocations", [])
        renewal = row.get("terminal_decision_renewal")
        for field, value in (
            ("sale_lot_closures", closures),
            ("qualifying_fill_reallocations", reallocations),
            ("active_recovery_allocations", active_allocations),
        ):
            if not isinstance(value, list):
                raise ValueError(f"R17 terminal-decision {field} must be a list for {key}")
        if schema_version == 1 and not closures:
            raise ValueError(f"R17 terminal-decision sale-lot closures are missing for {key}")
        if schema_version in {2, 3} and not (
            closures or reallocations or active_allocations or isinstance(renewal, dict)
        ):
            raise ValueError(f"R19/R31 mixed-lot decision has no governed operation for {key}")
        if renewal is not None:
            if schema_version != 3 or not isinstance(renewal, dict):
                raise ValueError(f"R31 terminal renewal is invalid for {key}")
            if closures or reallocations or active_allocations:
                raise ValueError(f"R31 terminal renewal cannot mix with allocation or closure operations for {key}")
            renewal_id = str(renewal.get("renewal_id") or "")
            prior_decision_id = str(renewal.get("prior_decision_id") or "")
            lot_decision_ids = renewal.get("sale_lot_decision_ids")
            if (
                not renewal_id
                or renewal_id in renewal_ids
                or not prior_decision_id
                or prior_decision_id == str(row.get("decision_id") or "")
                or not _is_positive_integer(renewal.get("closed_quantity"))
                or not isinstance(lot_decision_ids, list)
                or not lot_decision_ids
                or any(not str(value or "") for value in lot_decision_ids)
                or len(lot_decision_ids) != len(set(str(value) for value in lot_decision_ids))
            ):
                raise ValueError(f"R31 terminal renewal identity or quantity is invalid for {key}")
            renewal_ids.add(renewal_id)

        for reallocation in reallocations:
            if not isinstance(reallocation, dict):
                raise ValueError(f"R19 fill reallocations contain a non-object row for {key}")
            reallocation_id = str(reallocation.get("reallocation_id") or "")
            source_lot_id = str(reallocation.get("source_sale_lot_id") or "")
            target_lot_id = str(reallocation.get("target_sale_lot_id") or "")
            if not all(
                str(reallocation.get(field) or "")
                for field in ("buy_transaction_id", "source_allocation_id")
            ):
                raise ValueError(f"R19 fill reallocation source identity is missing for {key}")
            if not reallocation_id or reallocation_id in reallocation_ids:
                raise ValueError(f"R19 fill reallocation id is invalid or duplicated for {key}")
            if not source_lot_id or not target_lot_id or source_lot_id == target_lot_id:
                raise ValueError(f"R19 fill reallocation lot identity is invalid for {key}")
            if not _is_positive_integer(reallocation.get("quantity")):
                raise ValueError(f"R19 fill reallocation quantity is invalid for {key}")
            if schema_version == 3:
                target_allocation_id = str(reallocation.get("target_allocation_id") or "")
                before = reallocation.get("target_quantity_before")
                after = reallocation.get("target_quantity_after")
                if not target_allocation_id or target_allocation_id == str(
                    reallocation.get("source_allocation_id") or ""
                ):
                    raise ValueError(f"R31 target allocation identity is invalid for {key}")
                if not _is_positive_integer(before) or not _is_positive_integer(after):
                    raise ValueError(f"R31 target allocation quantity is invalid for {key}")
                if int(after) - int(before) != int(reallocation.get("quantity") or 0):
                    raise ValueError(f"R31 target allocation delta does not match the reallocation for {key}")
            reallocation_ids.add(reallocation_id)

        active_source_quantities: dict[str, int] = {}
        active_allocated_quantities: Counter[str] = Counter()
        for allocation in active_allocations:
            if not isinstance(allocation, dict):
                raise ValueError(f"R19 active allocations contain a non-object row for {key}")
            allocation_id = str(allocation.get("allocation_id") or "")
            stop_loss_id = str(allocation.get("stop_loss_id") or "")
            lot_id = str(allocation.get("sale_lot_id") or "")
            source_quantity = allocation.get("source_quantity")
            quantity = allocation.get("quantity")
            if not allocation_id or allocation_id in active_allocation_ids:
                raise ValueError(f"R19 active allocation id is invalid or duplicated for {key}")
            if not stop_loss_id or not lot_id:
                raise ValueError(f"R19 active allocation source or lot is missing for {key}")
            if not _is_positive_integer(source_quantity) or not _is_positive_integer(quantity):
                raise ValueError(f"R19 active allocation quantity is invalid for {key}")
            if stop_loss_id in active_source_quantities and active_source_quantities[stop_loss_id] != source_quantity:
                raise ValueError(f"R19 active source quantity changes between allocations for {key}")
            active_source_quantities[stop_loss_id] = int(source_quantity)
            active_allocated_quantities[stop_loss_id] += int(quantity)
            active_allocation_ids.add(allocation_id)
        for stop_loss_id, quantity in active_allocated_quantities.items():
            if quantity != active_source_quantities[stop_loss_id]:
                raise ValueError(f"R19 active stop source is not fully attributed for {key}/{stop_loss_id}")

        lot_ids: list[str] = []
        for closure in closures:
            if not isinstance(closure, dict):
                raise ValueError(f"R17 terminal-decision contains a non-object lot for {key}")
            lot_id = str(closure.get("sale_lot_id") or "")
            if not lot_id or not str(closure.get("sale_transaction_id") or ""):
                raise ValueError(f"R17 terminal-decision lot identity is missing for {key}")
            if not str(closure.get("sale_timestamp") or "") or not _is_positive_integer(
                closure.get("remaining_open_quantity_to_close")
            ):
                raise ValueError(f"R17 terminal-decision lot quantity is invalid for {key}")
            if schema_version in {2, 3}:
                closure_id = str(closure.get("closure_id") or "")
                if not closure_id or closure_id in closure_ids:
                    raise ValueError(f"R19 closure id is invalid or duplicated for {key}")
                closure_ids.add(closure_id)
            lot_ids.append(lot_id)
        if len(lot_ids) != len(set(lot_ids)):
            raise ValueError(f"R17 terminal-decision reuses a sale lot for {key}")
        result[key] = row
    return result


def _recompute_recovery_row(row: dict[str, Any]) -> None:
    """Rebuild exact lot and cycle parity after an R19 reviewed allocation."""

    lots = row.get("sale_lots")
    fill_allocations = row.get("qualifying_fill_allocations")
    active_allocations = row.get("active_recovery_allocations")
    if not isinstance(lots, list) or not isinstance(fill_allocations, list) or not isinstance(
        active_allocations, list
    ):
        raise ValueError(f"R19 recovery structures are missing for {_row_key(row)}")

    fill_by_lot: Counter[str] = Counter()
    active_by_lot: Counter[str] = Counter()
    for allocation in fill_allocations:
        if isinstance(allocation, dict):
            fill_by_lot[str(allocation.get("sale_lot_id") or "")] += int(
                allocation.get("quantity", 0) or 0
            )
    for allocation in active_allocations:
        if isinstance(allocation, dict):
            active_by_lot[str(allocation.get("sale_lot_id") or "")] += int(
                allocation.get("quantity", 0) or 0
            )

    for lot in lots:
        if not isinstance(lot, dict):
            raise ValueError(f"R19 recovery contains a non-object lot for {_row_key(row)}")
        lot_id = str(lot.get("sale_lot_id") or "")
        sold = int(lot.get("sold_quantity", 0) or 0)
        filled = fill_by_lot.get(lot_id, 0)
        active = active_by_lot.get(lot_id, 0)
        closed = int(lot.get("closed_no_reentry_quantity", 0) or 0)
        remaining = sold - filled - active - closed
        if sold <= 0 or min(filled, active, closed, remaining) < 0:
            raise ValueError(f"R19 sale-lot parity is invalid for {_row_key(row)}/{lot_id}")
        lot["qualifying_filled_quantity"] = filled
        lot["active_recovery_quantity"] = active
        lot["remaining_open_quantity"] = remaining
        if remaining == 0 and closed > 0:
            lot["state"] = "TERMINAL_DECISION_CLOSED"
        elif remaining == 0:
            lot["state"] = "RECOVERY_ALLOCATED"
        elif filled or active or closed:
            lot["state"] = "PARTIALLY_RECOVERED_OPEN"
        else:
            lot["state"] = "OPEN_UNRECOVERED"

    row["later_filled_quantity"] = sum(
        int(lot.get("qualifying_filled_quantity", 0) or 0)
        for lot in lots
        if isinstance(lot, dict)
    )
    row["sale_attributed_active_buy_quantity"] = sum(
        int(lot.get("active_recovery_quantity", 0) or 0)
        for lot in lots
        if isinstance(lot, dict)
    )
    row["closed_no_reentry_quantity"] = sum(
        int(lot.get("closed_no_reentry_quantity", 0) or 0)
        for lot in lots
        if isinstance(lot, dict)
    )
    row["remaining_open_quantity"] = sum(
        int(lot.get("remaining_open_quantity", 0) or 0)
        for lot in lots
        if isinstance(lot, dict)
    )
    row["pre_sale_active_buy_quantity"] = sum(
        int(item.get("quantity", 0) or 0)
        for item in row.get("pre_sale_active_buy_inventory", [])
        if isinstance(item, dict)
    )
    row["unattributed_active_buy_quantity"] = sum(
        int(item.get("quantity", 0) or 0)
        for item in row.get("unattributed_active_buy_inventory", [])
        if isinstance(item, dict)
    )


def _r19_decision_already_applied(
    row: dict[str, Any],
    decision: dict[str, Any],
    *,
    key: tuple[str, str, str],
    schema_version: int,
) -> bool:
    """Return True only when every operation from one decision is already exact."""

    mixed = row.get("mixed_lot_resolution")
    decision_id = str(decision.get("decision_id") or "")
    if not isinstance(mixed, dict) or str(mixed.get("decision_id") or "") != decision_id:
        return False

    problems: list[str] = []
    if mixed.get("schema_version") != schema_version or mixed.get("broker_mutation") is not False:
        problems.append("mixed-lot resolution metadata differs")

    fill_allocations = row.get("qualifying_fill_allocations")
    active_allocations = row.get("active_recovery_allocations")
    events = row.get("qualifying_fill_reallocation_events", [])
    lots = row.get("sale_lots")
    if not all(isinstance(value, list) for value in (fill_allocations, active_allocations, events, lots)):
        problems.append("governed recovery structures are missing")
    else:
        assert isinstance(fill_allocations, list)
        assert isinstance(active_allocations, list)
        assert isinstance(events, list)
        assert isinstance(lots, list)
        lot_by_id = {
            str(lot.get("sale_lot_id") or ""): lot
            for lot in lots
            if isinstance(lot, dict)
        }

        for operation in decision.get("qualifying_fill_reallocations", []):
            operation_id = str(operation.get("reallocation_id") or "")
            matches = [
                event
                for event in events
                if isinstance(event, dict)
                and str(event.get("reallocation_id") or "") == operation_id
            ]
            if len(matches) != 1 or any(
                matches[0].get(field) != operation.get(field)
                for field in operation
            ):
                problems.append(f"reallocation {operation_id} differs")
                continue
            target_allocation_id = (
                str(operation.get("target_allocation_id") or "")
                if schema_version == 3
                else operation_id
            )
            target_quantity = (
                operation.get("target_quantity_after")
                if schema_version == 3
                else operation.get("quantity")
            )
            targets = [
                allocation
                for allocation in fill_allocations
                if isinstance(allocation, dict)
                and str(allocation.get("allocation_id") or "") == target_allocation_id
                and str(allocation.get("buy_transaction_id") or "")
                == str(operation.get("buy_transaction_id") or "")
                and str(allocation.get("sale_lot_id") or "")
                == str(operation.get("target_sale_lot_id") or "")
                and allocation.get("quantity") == target_quantity
            ]
            if len(targets) != 1:
                problems.append(f"reallocation target {target_allocation_id} differs")

        for operation in decision.get("active_recovery_allocations", []):
            allocation_id = str(operation.get("allocation_id") or "")
            matches = [
                allocation
                for allocation in active_allocations
                if isinstance(allocation, dict)
                and str(allocation.get("allocation_id") or "") == allocation_id
                and allocation.get("decision_id") == decision.get("decision_id")
                and all(allocation.get(field) == operation.get(field) for field in operation)
            ]
            if len(matches) != 1:
                problems.append(f"active allocation {allocation_id} differs")

        for operation in decision.get("sale_lot_closures", []):
            lot_id = str(operation.get("sale_lot_id") or "")
            lot = lot_by_id.get(lot_id)
            closure_id = str(operation.get("closure_id") or "")
            closures = lot.get("terminal_closure_decisions", []) if isinstance(lot, dict) else []
            matches = [
                closure
                for closure in closures
                if isinstance(closure, dict)
                and str(closure.get("decision_id") or "") == closure_id
                and closure.get("closed_quantity")
                == operation.get("remaining_open_quantity_to_close")
                and str(closure.get("sale_transaction_id") or "")
                == str(operation.get("sale_transaction_id") or "")
                and str(closure.get("sale_timestamp") or "")
                == str(operation.get("sale_timestamp") or "")
            ]
            if len(matches) != 1:
                problems.append(f"terminal closure {closure_id} differs")

    cumulative_terminal_closure_count = (
        sum(
            len(lot.get("terminal_closure_decisions", []))
            for lot in lots
            if isinstance(lot, dict)
            and isinstance(lot.get("terminal_closure_decisions", []), list)
        )
        if isinstance(lots, list)
        else 0
    )
    expected_counts = {
        "fill_reallocation_count": len(decision.get("qualifying_fill_reallocations", [])),
        "active_allocation_count": len(decision.get("active_recovery_allocations", [])),
        "terminal_closure_count": cumulative_terminal_closure_count,
        "remaining_open_quantity": int(row.get("remaining_open_quantity", 0) or 0),
    }
    for field, expected in expected_counts.items():
        if mixed.get(field) != expected:
            problems.append(f"{field} differs")

    if problems:
        raise ValueError(
            f"R19/R31 decision {decision_id} is only partially applied for {key}: "
            + "; ".join(dict.fromkeys(problems))
        )
    return True


def _apply_r31_terminal_renewal(
    row: dict[str, Any],
    decision: dict[str, Any],
    *,
    key: tuple[str, str, str],
) -> None:
    """Renew an exact already-closed terminal decision without changing lot parity."""

    renewal = decision.get("terminal_decision_renewal")
    if not isinstance(renewal, dict):
        raise ValueError(f"R31 terminal renewal is missing for {key}")
    existing = row.get("no_reentry_decision")
    lots = row.get("sale_lots")
    if not isinstance(existing, dict) or not isinstance(lots, list):
        raise ValueError(f"R31 terminal renewal source is missing for {key}")
    if (
        row.get("state") != "EXPLICIT_NO_REENTRY_CURRENT_THESIS"
        or int(row.get("remaining_open_quantity", 0) or 0) != 0
        or int(row.get("sale_attributed_active_buy_quantity", 0) or 0) != 0
    ):
        raise ValueError(f"R31 terminal renewal source is not fully closed for {key}")

    lot_decisions: list[dict[str, Any]] = []
    for lot in lots:
        if not isinstance(lot, dict):
            raise ValueError(f"R31 terminal renewal contains a non-object lot for {key}")
        legacy = lot.get("no_reentry_decision")
        closures = lot.get("terminal_closure_decisions", [])
        if legacy is not None and closures:
            raise ValueError(f"R31 terminal renewal source mixes closure models for {key}")
        if isinstance(legacy, dict):
            lot_decisions.append(legacy)
        elif isinstance(closures, list):
            lot_decisions.extend(item for item in closures if isinstance(item, dict))
        else:
            raise ValueError(f"R31 terminal renewal closure list is invalid for {key}")

    actual_ids = [str(item.get("decision_id") or "") for item in lot_decisions]
    expected_ids = [str(value) for value in renewal.get("sale_lot_decision_ids", [])]
    closed_total = int(row.get("closed_no_reentry_quantity", 0) or 0)
    if (
        not actual_ids
        or len(actual_ids) != len(set(actual_ids))
        or actual_ids != expected_ids
        or int(renewal.get("closed_quantity", 0) or 0) != closed_total
        or int(existing.get("closed_quantity", 0) or 0) != closed_total
    ):
        raise ValueError(f"R31 terminal renewal does not match exact closed inventory for {key}")

    renewal_id = str(renewal.get("renewal_id") or "")
    prior_decision_id = str(renewal.get("prior_decision_id") or "")
    decision_id = str(decision.get("decision_id") or "")
    evidence_fields = (
        "decision_at",
        "last_revalidated_at",
        "expires_at",
        "decision_basis",
        "thesis_evidence",
        "event_evidence",
        "technical_evidence",
        "path_evidence",
        "newer_evidence_reviewed",
        "contradiction_status",
    )

    if str(existing.get("decision_id") or "") == decision_id:
        problems: list[str] = []
        if (
            existing.get("renewal_id") != renewal_id
            or existing.get("prior_decision_id") != prior_decision_id
            or existing.get("sale_lot_decision_ids") != actual_ids
        ):
            problems.append("aggregate renewal identity differs")
        if any(existing.get(field) != decision.get(field) for field in evidence_fields):
            problems.append("aggregate renewal evidence differs")
        for lot_decision in lot_decisions:
            if (
                lot_decision.get("parent_decision_id") != decision_id
                or lot_decision.get("renewal_id") != renewal_id
                or any(lot_decision.get(field) != decision.get(field) for field in evidence_fields)
            ):
                problems.append(f"sale-lot renewal {lot_decision.get('decision_id')} differs")
        mixed = row.get("mixed_lot_resolution")
        r19_closures = sum(
            len(lot.get("terminal_closure_decisions", []))
            for lot in lots
            if isinstance(lot, dict) and isinstance(lot.get("terminal_closure_decisions", []), list)
        )
        expected_mixed = {
            "schema_version": 3,
            "decision_id": decision_id,
            "fill_reallocation_count": 0,
            "active_allocation_count": 0,
            "terminal_closure_count": r19_closures,
            "terminal_renewal_count": 1,
            "remaining_open_quantity": 0,
            "broker_mutation": False,
        }
        if mixed != expected_mixed:
            problems.append("mixed-lot renewal metadata differs")
        if problems:
            raise ValueError(
                f"R31 terminal renewal {renewal_id} is only partially applied for {key}: "
                + "; ".join(dict.fromkeys(problems))
            )
        return

    if str(existing.get("decision_id") or "") != prior_decision_id:
        raise ValueError(f"R31 terminal renewal prior decision mismatch for {key}")

    prior_event = {
        "renewal_id": renewal_id,
        "prior_decision_id": prior_decision_id,
        "prior_last_revalidated_at": existing.get("last_revalidated_at"),
        "prior_expires_at": existing.get("expires_at"),
        "renewed_at": decision.get("last_revalidated_at"),
    }

    def deduplicate_history(items: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        renewal_positions: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            renewal_key = str(item.get("renewal_id") or "")
            if renewal_key and renewal_key in renewal_positions:
                result[renewal_positions[renewal_key]] = item
                continue
            if renewal_key:
                renewal_positions[renewal_key] = len(result)
            result.append(item)
        return result

    for lot_decision in lot_decisions:
        history = list(lot_decision.get("renewal_history", []))
        history.append(copy.deepcopy(prior_event))
        lot_decision.update({field: decision.get(field) for field in evidence_fields})
        lot_decision.update({
            "parent_decision_id": decision_id,
            "renewal_id": renewal_id,
            "prior_parent_decision_id": prior_decision_id,
            "renewal_history": deduplicate_history(history),
        })

    aggregate_history = list(existing.get("renewal_history", []))
    aggregate_history.append(copy.deepcopy(prior_event))
    renewed = dict(existing)
    renewed.update({field: decision.get(field) for field in evidence_fields})
    renewed.update({
        "decision_id": decision_id,
        "prior_decision_id": prior_decision_id,
        "renewal_id": renewal_id,
        "sale_lot_decision_ids": actual_ids,
        "current_holding": decision.get("current_holding"),
        "renewal_history": deduplicate_history(aggregate_history),
    })
    row["no_reentry_decision"] = renewed
    row["partial_terminal_decisions"] = []
    row["recorded_stage_percentages_below_marker"] = "PERCENTAGE_NOT_SET"
    row["recorded_stage_quantities"] = None
    r19_closures = sum(
        len(lot.get("terminal_closure_decisions", []))
        for lot in lots
        if isinstance(lot, dict) and isinstance(lot.get("terminal_closure_decisions", []), list)
    )
    row["mixed_lot_resolution"] = {
        "schema_version": 3,
        "decision_id": decision_id,
        "fill_reallocation_count": 0,
        "active_allocation_count": 0,
        "terminal_closure_count": r19_closures,
        "terminal_renewal_count": 1,
        "remaining_open_quantity": 0,
        "broker_mutation": False,
    }
    row.pop("full_path_evidence", None)


def _apply_r19_decision(
    row: dict[str, Any],
    decision: dict[str, Any],
    *,
    key: tuple[str, str, str],
    schema_version: int,
) -> None:
    """Apply one schema-v2/v3 review overlay without changing source totals."""

    if schema_version == 3 and isinstance(decision.get("terminal_decision_renewal"), dict):
        _apply_r31_terminal_renewal(row, decision, key=key)
        return

    if _r19_decision_already_applied(
        row,
        decision,
        key=key,
        schema_version=schema_version,
    ):
        return

    lots = row.get("sale_lots")
    fill_allocations = row.get("qualifying_fill_allocations")
    active_allocations = row.get("active_recovery_allocations")
    unattributed_active = row.get("unattributed_active_buy_inventory")
    if not all(isinstance(value, list) for value in (lots, fill_allocations, active_allocations, unattributed_active)):
        raise ValueError(f"R19 recovery structures are missing for {key}")
    assert isinstance(lots, list)
    assert isinstance(fill_allocations, list)
    assert isinstance(active_allocations, list)
    assert isinstance(unattributed_active, list)
    row["partial_terminal_decisions"] = []
    lot_by_id = {
        str(lot.get("sale_lot_id") or ""): lot
        for lot in lots
        if isinstance(lot, dict)
    }

    reallocation_events = list(row.get("qualifying_fill_reallocation_events", []))
    for reallocation in decision.get("qualifying_fill_reallocations", []):
        source_allocation_id = str(reallocation.get("source_allocation_id") or "")
        matches = [
            allocation
            for allocation in fill_allocations
            if isinstance(allocation, dict)
            and str(allocation.get("allocation_id") or "") == source_allocation_id
        ]
        if len(matches) != 1:
            raise ValueError(f"R19 fill source allocation is missing or duplicated for {key}")
        source = matches[0]
        source_lot_id = str(reallocation.get("source_sale_lot_id") or "")
        target_lot_id = str(reallocation.get("target_sale_lot_id") or "")
        buy_transaction_id = str(reallocation.get("buy_transaction_id") or "")
        quantity = int(reallocation.get("quantity") or 0)
        if source_lot_id not in lot_by_id or target_lot_id not in lot_by_id:
            raise ValueError(f"R19 fill reallocation references an unknown lot for {key}")
        if (
            str(source.get("sale_lot_id") or "") != source_lot_id
            or str(source.get("buy_transaction_id") or "") != buy_transaction_id
            or quantity > int(source.get("quantity", 0) or 0)
        ):
            raise ValueError(f"R19 fill reallocation source does not match exact inventory for {key}")
        buy_time = _artifact_time(source.get("buy_timestamp"))
        target_time = _artifact_time(lot_by_id[target_lot_id].get("sale_timestamp"))
        if buy_time is None or target_time is None or buy_time.date() < target_time.date():
            raise ValueError(f"R19 fill predates its target sale lot for {key}")

        target: dict[str, Any] | None = None
        if schema_version == 3:
            target_allocation_id = str(reallocation.get("target_allocation_id") or "")
            target_matches = [
                allocation
                for allocation in fill_allocations
                if isinstance(allocation, dict)
                and str(allocation.get("allocation_id") or "") == target_allocation_id
            ]
            if len(target_matches) != 1:
                raise ValueError(f"R31 target allocation is missing or duplicated for {key}")
            target = target_matches[0]
            before = int(reallocation.get("target_quantity_before") or 0)
            after = int(reallocation.get("target_quantity_after") or 0)
            if (
                target is source
                or str(target.get("sale_lot_id") or "") != target_lot_id
                or str(target.get("buy_transaction_id") or "") != buy_transaction_id
                or int(target.get("quantity", 0) or 0) != before
                or after - before != quantity
            ):
                raise ValueError(f"R31 target allocation does not match exact inventory for {key}")
        elif any(
            isinstance(allocation, dict)
            and str(allocation.get("buy_transaction_id") or "") == buy_transaction_id
            and str(allocation.get("sale_lot_id") or "") == target_lot_id
            for allocation in fill_allocations
        ):
            raise ValueError(f"R19 fill reallocation duplicates an existing source-to-lot pair for {key}")

        source_snapshot = copy.deepcopy(source)
        source["quantity"] = int(source.get("quantity") or 0) - quantity
        if source["quantity"] == 0:
            fill_allocations.remove(source)
        if schema_version == 3:
            assert target is not None
            target["quantity"] = int(reallocation.get("target_quantity_after") or 0)
            reviewed_ids = list(target.get("reviewed_reallocation_ids", []))
            reviewed_ids.append(str(reallocation.get("reallocation_id")))
            target["reviewed_reallocation_ids"] = list(dict.fromkeys(reviewed_ids))
        else:
            target = source_snapshot
            target.update(
                {
                    "allocation_id": str(reallocation.get("reallocation_id")),
                    "sale_lot_id": target_lot_id,
                    "quantity": quantity,
                    "allocation_method": "REVIEWED_EXACT_LOT_REALLOCATION_R19",
                    "reallocation_id": str(reallocation.get("reallocation_id")),
                    "source_allocation_id": source_allocation_id,
                    "source_sale_lot_id": source_lot_id,
                    "decision_id": decision.get("decision_id"),
                }
            )
            fill_allocations.append(target)
        reallocation_events.append(
            {**copy.deepcopy(reallocation), "decision_id": decision.get("decision_id")}
        )
    if reallocation_events:
        row["qualifying_fill_reallocation_events"] = reallocation_events

    unattributed_by_stop = {
        str(item.get("stop_loss_id") or ""): item
        for item in unattributed_active
        if isinstance(item, dict)
    }
    reviewed_active = decision.get("active_recovery_allocations", [])
    active_by_stop: dict[str, list[dict[str, Any]]] = {}
    for allocation in reviewed_active:
        stop_loss_id = str(allocation.get("stop_loss_id") or "")
        active_by_stop.setdefault(stop_loss_id, []).append(allocation)
    for stop_loss_id, allocations in active_by_stop.items():
        inventory = unattributed_by_stop.get(stop_loss_id)
        if inventory is None:
            raise ValueError(f"R19 active stop source is not exact unattributed inventory for {key}/{stop_loss_id}")
        if any(
            isinstance(item, dict) and str(item.get("stop_loss_id") or "") == stop_loss_id
            for item in active_allocations
        ):
            raise ValueError(f"R19 active stop source is already attributed for {key}/{stop_loss_id}")
        source_quantity = int(inventory.get("quantity", 0) or 0)
        if sum(int(item.get("quantity", 0) or 0) for item in allocations) != source_quantity:
            raise ValueError(f"R19 active stop source is not fully allocated for {key}/{stop_loss_id}")
        for allocation in allocations:
            lot_id = str(allocation.get("sale_lot_id") or "")
            if lot_id not in lot_by_id:
                raise ValueError(f"R19 active allocation references an unknown lot for {key}")
            active_allocations.append(
                {
                    **copy.deepcopy(allocation),
                    "source_quantity": source_quantity,
                    "allocation_method": "REVIEWED_EXACT_STOP_TO_LOT_R19",
                    "decision_id": decision.get("decision_id"),
                }
            )
        unattributed_active.remove(inventory)

    _recompute_recovery_row(row)

    closure_summaries: list[dict[str, Any]] = []
    for closure in decision.get("sale_lot_closures", []):
        lot_id = str(closure.get("sale_lot_id") or "")
        lot = lot_by_id.get(lot_id)
        if lot is None:
            raise ValueError(f"R19 terminal closure references an unknown lot for {key}")
        for field in ("sale_transaction_id", "sale_timestamp"):
            if str(closure.get(field) or "") != str(lot.get(field) or ""):
                raise ValueError(f"R19 terminal closure {field} mismatch for {key}/{lot_id}")
        quantity = int(closure.get("remaining_open_quantity_to_close") or 0)
        available = int(lot.get("remaining_open_quantity", 0) or 0)
        if quantity <= 0 or quantity > available:
            raise ValueError(f"R19 terminal closure overcloses the exact residual for {key}/{lot_id}")
        if isinstance(lot.get("no_reentry_decision"), dict):
            raise ValueError(f"R19 terminal closure reuses a previously closed lot for {key}/{lot_id}")
        prior_closures = lot.get("terminal_closure_decisions", [])
        if not isinstance(prior_closures, list):
            raise ValueError(f"R19 terminal closure history is invalid for {key}/{lot_id}")
        closure_id = str(closure.get("closure_id") or "")
        if any(
            isinstance(prior, dict)
            and str(prior.get("decision_id") or "") == closure_id
            for prior in prior_closures
        ):
            raise ValueError(f"R19 terminal closure id already exists for {key}/{lot_id}/{closure_id}")
        recovered_before = (
            int(lot.get("qualifying_filled_quantity", 0) or 0)
            + int(lot.get("active_recovery_quantity", 0) or 0)
            + int(lot.get("closed_no_reentry_quantity", 0) or 0)
        )
        lot_decision = {
            "decision_id": str(closure.get("closure_id")),
            "parent_decision_id": decision.get("decision_id"),
            "tenant_session_id": key[0],
            "account_id": key[1],
            "orderbook_id": key[2],
            "sale_date": str(lot.get("sale_timestamp") or "")[:10],
            "sale_lot_id": lot_id,
            "sale_transaction_id": lot.get("sale_transaction_id"),
            "sale_timestamp": lot.get("sale_timestamp"),
            "original_sold_quantity": int(lot.get("sold_quantity", 0) or 0),
            "recovered_before_decision_quantity": recovered_before,
            "sold_quantity": quantity,
            "closed_quantity": quantity,
            "remaining_after_decision_quantity": available - quantity,
            "decision_at": decision.get("decision_at"),
            "last_revalidated_at": decision.get("last_revalidated_at"),
            "expires_at": decision.get("expires_at"),
            "decision_basis": decision.get("decision_basis"),
            "thesis_evidence": decision.get("thesis_evidence"),
            "event_evidence": decision.get("event_evidence"),
            "technical_evidence": decision.get("technical_evidence"),
            "path_evidence": decision.get("path_evidence"),
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
        }
        lot["terminal_closure_decisions"] = [*prior_closures, lot_decision]
        lot["closed_no_reentry_quantity"] = int(lot.get("closed_no_reentry_quantity", 0) or 0) + quantity
        closure_summaries.append(lot_decision)

    _recompute_recovery_row(row)
    remaining = int(row.get("remaining_open_quantity", 0) or 0)
    active = int(row.get("sale_attributed_active_buy_quantity", 0) or 0)
    closed_total = int(row.get("closed_no_reentry_quantity", 0) or 0)
    cumulative_lot_decision_ids = [
        str(closure.get("decision_id") or "")
        for lot in lots
        if isinstance(lot, dict)
        for closure in lot.get("terminal_closure_decisions", [])
        if isinstance(closure, dict) and str(closure.get("decision_id") or "")
    ]
    cumulative_terminal_closure_count = len(cumulative_lot_decision_ids)
    if remaining == 0 and active == 0 and closed_total > 0:
        row["state"] = "EXPLICIT_NO_REENTRY_CURRENT_THESIS"
        row["partial_terminal_decisions"] = []
        row["no_reentry_decision"] = {
            "decision_id": decision.get("decision_id"),
            "tenant_session_id": key[0],
            "account_id": key[1],
            "orderbook_id": key[2],
            "sale_date": row.get("sale_date"),
            "sold_quantity": closed_total,
            "closed_quantity": closed_total,
            "decision_at": decision.get("decision_at"),
            "last_revalidated_at": decision.get("last_revalidated_at"),
            "expires_at": decision.get("expires_at"),
            "decision_basis": decision.get("decision_basis"),
            "thesis_evidence": decision.get("thesis_evidence"),
            "event_evidence": decision.get("event_evidence"),
            "technical_evidence": decision.get("technical_evidence"),
            "path_evidence": decision.get("path_evidence"),
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "recovery_cycle_id": row.get("recovery_cycle_id"),
            "sale_lot_decision_ids": cumulative_lot_decision_ids,
            "current_holding": decision.get("current_holding"),
        }
    elif remaining == 0:
        row["state"] = "FULL_SOLD_SLICE_RECOVERY_COVERED"
        row.pop("no_reentry_decision", None)
        row["partial_terminal_decisions"] = []
    else:
        row["state"] = "PARTIAL_SOLD_SLICE_RECOVERY_ATTRIBUTED"
        row.pop("no_reentry_decision", None)
        if closure_summaries:
            prior_partial_decisions = [
                item
                for item in row.get("partial_terminal_decisions", [])
                if isinstance(item, dict)
                and str(item.get("decision_id") or "") != str(decision.get("decision_id") or "")
            ]
            row["partial_terminal_decisions"] = [
                *prior_partial_decisions,
                {
                    "decision_id": decision.get("decision_id"),
                    "closure_ids": [item["decision_id"] for item in closure_summaries],
                    "closed_quantity": sum(int(item["closed_quantity"]) for item in closure_summaries),
                    "remaining_open_quantity": remaining,
                    "expires_at": decision.get("expires_at"),
                },
            ]
    row["recorded_stage_percentages_below_marker"] = "PERCENTAGE_NOT_SET"
    row["recorded_stage_quantities"] = None
    row["mixed_lot_resolution"] = {
        "schema_version": schema_version,
        "decision_id": decision.get("decision_id"),
        "fill_reallocation_count": len(decision.get("qualifying_fill_reallocations", [])),
        "active_allocation_count": len(decision.get("active_recovery_allocations", [])),
        "terminal_closure_count": cumulative_terminal_closure_count,
        "remaining_open_quantity": remaining,
        "broker_mutation": False,
    }
    row.pop("full_path_evidence", None)


def apply_terminal_decisions_to_remediation(
    payload: dict[str, Any],
    decisions: dict[str, Any],
    *,
    generated_at: str,
    decision_source_path: str,
) -> dict[str, Any]:
    """Close only the exact unresolved remainder of each selected immutable sale lot."""

    if payload.get("artifact") != "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE":
        raise ValueError("terminal decisions require a sold-marker remediation input")
    decision_rows = _terminal_decision_rows(decisions, reference_time=generated_at)
    result = copy.deepcopy(payload)
    rows = result.get("rows")
    if not isinstance(rows, list):
        raise ValueError("terminal-decision remediation rows must be a list")
    remediation_rows = {_row_key(row): row for row in rows if isinstance(row, dict)}

    if decisions.get("schema_version") in {2, 3}:
        decision_schema_version = int(decisions.get("schema_version") or 0)
        for key, decision in decision_rows.items():
            row = remediation_rows.get(key)
            if row is None:
                raise ValueError(f"R19 mixed-lot remediation row is missing for {key}")
            if row.get("recovery_cycle_id") != decision.get("recovery_cycle_id"):
                raise ValueError(f"R19 recovery cycle mismatch for {key}")
            if row.get("instrument") != decision.get("instrument"):
                raise ValueError(f"R19 instrument mismatch for {key}")
            _apply_r19_decision(
                row,
                decision,
                key=key,
                schema_version=decision_schema_version,
            )

        sources = list(result.get("sources", []))
        if decision_source_path not in sources:
            sources.append(decision_source_path)
        result["sources"] = sources
        existing_overlay = result.get("terminal_decision_overlay")
        overlay_sources = []
        if isinstance(existing_overlay, dict):
            overlay_sources.extend(existing_overlay.get("sources", []))
            if existing_overlay.get("source"):
                overlay_sources.append(existing_overlay["source"])
        overlay_sources.append(decision_source_path)
        result["terminal_decision_overlay"] = {
            "source": decision_source_path,
            "sources": list(dict.fromkeys(str(value) for value in overlay_sources if value)),
            "row_count": len(decision_rows),
            "schema_version": decision_schema_version,
            "broker_mutation": False,
        }
        result["schema_version"] = max(int(result.get("schema_version", 0) or 0), 5)
        result["summary"] = _remediation_summary(rows, result.get("summary"))
        result["conclusion"] = _remediation_conclusion(result["summary"])
        return result

    for key, decision in decision_rows.items():
        row = remediation_rows.get(key)
        if row is None:
            raise ValueError(f"R17 terminal-decision remediation row is missing for {key}")
        if row.get("recovery_cycle_id") != decision.get("recovery_cycle_id"):
            raise ValueError(f"R17 terminal-decision recovery cycle mismatch for {key}")
        if row.get("instrument") != decision.get("instrument"):
            raise ValueError(f"R17 terminal-decision instrument mismatch for {key}")
        if int(row.get("sale_attributed_active_buy_quantity", 0) or 0) != 0:
            raise ValueError(
                f"R17 terminal decision is contradicted by sale_attributed_active_buy_quantity for {key}"
            )

        lots = row.get("sale_lots")
        if not isinstance(lots, list):
            raise ValueError(f"R17 terminal-decision sale lots are missing for {key}")
        open_lots = {
            str(lot.get("sale_lot_id") or ""): lot
            for lot in lots
            if isinstance(lot, dict) and _is_positive_integer(lot.get("remaining_open_quantity"))
        }
        closures = {
            str(item.get("sale_lot_id") or ""): item
            for item in decision.get("sale_lot_closures", [])
            if isinstance(item, dict)
        }
        if set(closures) != set(open_lots):
            raise ValueError(f"R17 terminal decision must close every exact open lot for {key}")

        lot_decision_ids: list[str] = []
        closed_total = 0
        for lot_id, lot in open_lots.items():
            closure = closures[lot_id]
            for field in ("sale_transaction_id", "sale_timestamp"):
                if str(closure.get(field) or "") != str(lot.get(field) or ""):
                    raise ValueError(f"R17 terminal-decision {field} mismatch for {key}/{lot_id}")
            residual = int(lot.get("remaining_open_quantity") or 0)
            if closure.get("remaining_open_quantity_to_close") != residual:
                raise ValueError(f"R17 terminal-decision residual quantity mismatch for {key}/{lot_id}")
            sold = int(lot.get("sold_quantity") or 0)
            recovered_before = sold - residual
            if sold <= 0 or recovered_before < 0:
                raise ValueError(f"R17 terminal-decision lot parity is invalid for {key}/{lot_id}")
            lot_decision_id = f"{decision['decision_id']}::{lot_id}"
            lot_decision_ids.append(lot_decision_id)
            lot["no_reentry_decision"] = {
                "decision_id": lot_decision_id,
                "tenant_session_id": key[0],
                "account_id": key[1],
                "orderbook_id": key[2],
                "sale_date": str(lot.get("sale_timestamp") or "")[:10],
                "sale_lot_id": lot_id,
                "sale_transaction_id": lot.get("sale_transaction_id"),
                "sale_timestamp": lot.get("sale_timestamp"),
                "original_sold_quantity": sold,
                "recovered_before_decision_quantity": recovered_before,
                "sold_quantity": residual,
                "closed_quantity": residual,
                "decision_at": decision.get("decision_at"),
                "last_revalidated_at": decision.get("last_revalidated_at"),
                "expires_at": decision.get("expires_at"),
                "decision_basis": decision.get("decision_basis"),
                "thesis_evidence": decision.get("thesis_evidence"),
                "event_evidence": decision.get("event_evidence"),
                "technical_evidence": decision.get("technical_evidence"),
                "path_evidence": decision.get("path_evidence"),
                "newer_evidence_reviewed": True,
                "contradiction_status": "NONE",
            }
            lot["closed_no_reentry_quantity"] = int(lot.get("closed_no_reentry_quantity", 0) or 0) + residual
            lot["remaining_open_quantity"] = 0
            lot["state"] = "EXPLICIT_NO_REENTRY_CURRENT_THESIS"
            closed_total += residual

        row["closed_no_reentry_quantity"] = sum(
            int(lot.get("closed_no_reentry_quantity", 0) or 0)
            for lot in lots
            if isinstance(lot, dict)
        )
        row["remaining_open_quantity"] = sum(
            int(lot.get("remaining_open_quantity", 0) or 0)
            for lot in lots
            if isinstance(lot, dict)
        )
        if row["remaining_open_quantity"] != 0:
            raise ValueError(f"R17 terminal decision did not close the exact cycle remainder for {key}")
        row["state"] = "EXPLICIT_NO_REENTRY_CURRENT_THESIS"
        row["recorded_stage_percentages_below_marker"] = "PERCENTAGE_NOT_SET"
        row["recorded_stage_quantities"] = None
        row["no_reentry_decision"] = {
            "decision_id": decision.get("decision_id"),
            "tenant_session_id": key[0],
            "account_id": key[1],
            "orderbook_id": key[2],
            "sale_date": row.get("sale_date"),
            "sold_quantity": closed_total,
            "closed_quantity": closed_total,
            "decision_at": decision.get("decision_at"),
            "last_revalidated_at": decision.get("last_revalidated_at"),
            "expires_at": decision.get("expires_at"),
            "decision_basis": decision.get("decision_basis"),
            "thesis_evidence": decision.get("thesis_evidence"),
            "event_evidence": decision.get("event_evidence"),
            "technical_evidence": decision.get("technical_evidence"),
            "path_evidence": decision.get("path_evidence"),
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "recovery_cycle_id": row.get("recovery_cycle_id"),
            "sale_lot_decision_ids": lot_decision_ids,
            "current_holding": decision.get("current_holding"),
        }
        row.pop("full_path_evidence", None)

    sources = list(result.get("sources", []))
    if decision_source_path not in sources:
        sources.append(decision_source_path)
    result["sources"] = sources
    result["terminal_decision_overlay"] = {
        "source": decision_source_path,
        "row_count": len(decision_rows),
        "broker_mutation": False,
    }
    result["summary"] = _remediation_summary(rows, result.get("summary"))
    result["conclusion"] = _remediation_conclusion(result["summary"])
    return result


def filter_path_evidence_to_open_remediation(
    path_payload: dict[str, Any],
    remediation_payload: dict[str, Any],
    *,
    generated_at: str,
    source_path: str,
    decision_source_path: str,
) -> dict[str, Any]:
    """Create the current open-lot path source after dated terminal closures."""

    result = copy.deepcopy(path_payload)
    rows = result.get("rows")
    if not isinstance(rows, list):
        raise ValueError("R17 path evidence rows must be a list")
    open_remediation = {
        _row_key(row): row
        for row in remediation_payload.get("rows", [])
        if isinstance(row, dict) and _is_positive_integer(row.get("remaining_open_quantity"))
    }
    filtered: list[dict[str, Any]] = []
    for source_row in rows:
        if not isinstance(source_row, dict):
            continue
        key = _row_key(source_row)
        remediation = open_remediation.get(key)
        if remediation is None:
            continue
        if not isinstance(remediation.get("mixed_lot_resolution"), dict):
            filtered.append(copy.deepcopy(source_row))
            continue
        open_lots = {
            str(lot.get("sale_transaction_id") or ""): lot
            for lot in remediation.get("sale_lots", [])
            if isinstance(lot, dict) and _is_positive_integer(lot.get("remaining_open_quantity"))
        }
        exact_lots: list[dict[str, Any]] = []
        for source_lot in source_row.get("exact_lots", []):
            if not isinstance(source_lot, dict):
                continue
            transaction_id = str(source_lot.get("sale_transaction_id") or "")
            remediation_lot = open_lots.get(transaction_id)
            if remediation_lot is None:
                continue
            lot = copy.deepcopy(source_lot)
            lot["remaining_open_quantity"] = int(remediation_lot["remaining_open_quantity"])
            exact_lots.append(lot)
        if {str(lot.get("sale_transaction_id") or "") for lot in exact_lots} != set(open_lots):
            raise ValueError(f"R19 path evidence does not contain every exact residual lot for {key}")

        row = copy.deepcopy(source_row)
        row["exact_lots"] = exact_lots
        row["remaining_open_lot_count"] = len(exact_lots)
        row["remaining_open_quantity"] = sum(
            int(lot.get("remaining_open_quantity", 0) or 0) for lot in exact_lots
        )
        row["active_buy_quantity"] = sum(
            int(remediation.get(field, 0) or 0)
            for field in (
                "sale_attributed_active_buy_quantity",
                "pre_sale_active_buy_quantity",
                "unattributed_active_buy_quantity",
            )
        )
        row["sale_attributed_active_buy_quantity"] = int(
            remediation.get("sale_attributed_active_buy_quantity", 0) or 0
        )
        row["open_lots_crossing_8pct_alarm"] = sum(
            lot.get("crossed_8pct_review_alarm") is True for lot in exact_lots
        )
        row["crossed_8pct_review_alarm"] = row["open_lots_crossing_8pct_alarm"] > 0
        row["maximum_open_lot_drop_percent"] = max(
            float(lot.get("maximum_drop_below_marker_percent", 0) or 0)
            for lot in exact_lots
        )
        weighted_marker = sum(
            float(
                lot.get("comparable_sale_marker")
                or source_row.get("weighted_open_sale_marker")
                or 0
            )
            * int(lot.get("remaining_open_quantity", 0) or 0)
            for lot in exact_lots
        ) / row["remaining_open_quantity"]
        if weighted_marker <= 0:
            raise ValueError(f"R19 path evidence lacks a valid sold marker for {key}")
        row["weighted_open_sale_marker"] = round(weighted_marker, 6)
        current_close = float(row.get("current_close", 0) or 0)
        row["current_drop_below_weighted_marker_percent"] = round(
            (weighted_marker - current_close) / weighted_marker * 100,
            4,
        )
        named = row.get("named_exception") is True
        row["path_state"] = (
            NAMED_PATH_STATE
            if row["crossed_8pct_review_alarm"] and named
            else MISSED_PATH_STATE
            if row["crossed_8pct_review_alarm"]
            else OPEN_PATH_STATE
        )
        filtered.append(row)
    if {_row_key(row) for row in filtered} != set(open_remediation):
        raise ValueError("R17 filtered path rows do not match the post-decision open remediation rows")
    result["generated_at"] = generated_at
    result["path_observed_at"] = path_payload.get("generated_at")
    result["supersedes"] = source_path
    result["decision_source"] = decision_source_path
    result["rows"] = filtered
    exact_lots = [
        lot
        for row in filtered
        for lot in row.get("exact_lots", [])
        if isinstance(lot, dict)
    ]
    crossed_rows = [row for row in filtered if row.get("crossed_8pct_review_alarm") is True]
    result["summary"] = {
        "exact_account_rows": len(filtered),
        "unique_orderbooks": len({str(row.get("orderbook_id")) for row in filtered}),
        "exact_open_sale_lots": len(exact_lots),
        "remaining_open_quantity": sum(int(row.get("remaining_open_quantity", 0) or 0) for row in filtered),
        "rows_crossing_8pct_review_alarm": len(crossed_rows),
        "lots_crossing_8pct_review_alarm": sum(
            int(row.get("open_lots_crossing_8pct_alarm", 0) or 0) for row in filtered
        ),
        "named_exception_rows_with_crossing": sum(
            row.get("crossed_8pct_review_alarm") is True and row.get("named_exception") is True
            for row in filtered
        ),
        "path_or_marker_errors": 0,
    }
    return result


def _path_evidence_rows(
    path_payload: dict[str, Any],
    *,
    path_source_path: str,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Validate and normalize exact open-lot path evidence."""

    if path_payload.get("artifact") != "PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE":
        raise ValueError("path evidence is not an R17 open-sale path artifact")
    rows = path_payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("R17 open-sale path evidence rows must be non-empty")
    if path_payload.get("authority") != "ANALYSIS_ONLY":
        raise ValueError("R17 open-sale path evidence must remain ANALYSIS_ONLY")
    if path_payload.get("broker_mutation") is not False:
        raise ValueError("R17 open-sale path evidence must record zero broker mutation")

    generated_at = str(path_payload.get("generated_at") or "").strip()
    if not generated_at:
        raise ValueError("R17 open-sale path evidence generated_at is missing")

    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    total_lots = 0
    total_quantity = 0
    crossed_rows = 0
    crossed_lots = 0
    named_crossed_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("R17 open-sale path evidence contains a non-object row")
        key = _row_key(row)
        if not all(key) or key in result:
            raise ValueError(f"R17 open-sale path evidence key is invalid or duplicated: {key}")
        remaining = row.get("remaining_open_quantity")
        lot_count = row.get("remaining_open_lot_count")
        lots = row.get("exact_lots")
        if not _is_positive_integer(remaining):
            raise ValueError(f"R17 open-sale path remaining quantity is invalid for {key}")
        if not _is_positive_integer(lot_count) or not isinstance(lots, list) or len(lots) != lot_count:
            raise ValueError(f"R17 open-sale path lot count is invalid for {key}")

        transaction_ids: list[str] = []
        lot_quantity = 0
        lot_crossings = 0
        lot_maximums: list[float] = []
        for lot in lots:
            if not isinstance(lot, dict):
                raise ValueError(f"R17 open-sale path contains a non-object lot for {key}")
            transaction_id = str(lot.get("sale_transaction_id") or "")
            quantity = lot.get("remaining_open_quantity")
            maximum = lot.get("maximum_drop_below_marker_percent")
            if not transaction_id or not _is_positive_integer(quantity):
                raise ValueError(f"R17 open-sale path lot identity or quantity is invalid for {key}")
            if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
                raise ValueError(f"R17 open-sale path lot maximum drop is invalid for {key}")
            transaction_ids.append(transaction_id)
            lot_quantity += quantity
            lot_maximums.append(float(maximum))
            lot_crossings += lot.get("crossed_8pct_review_alarm") is True

        if len(transaction_ids) != len(set(transaction_ids)):
            raise ValueError(f"R17 open-sale path reuses a sale transaction for {key}")
        if lot_quantity != remaining:
            raise ValueError(f"R17 open-sale path lot quantity does not reconcile for {key}")
        if row.get("open_lots_crossing_8pct_alarm") != lot_crossings:
            raise ValueError(f"R17 open-sale path crossing count does not reconcile for {key}")
        crossed = row.get("crossed_8pct_review_alarm") is True
        if crossed != (lot_crossings > 0):
            raise ValueError(f"R17 open-sale path crossing state does not reconcile for {key}")
        maximum = row.get("maximum_open_lot_drop_percent")
        if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
            raise ValueError(f"R17 open-sale path maximum drop is invalid for {key}")
        if abs(float(maximum) - max(lot_maximums)) > 0.0001:
            raise ValueError(f"R17 open-sale path maximum drop does not reconcile for {key}")
        if crossed != (float(maximum) >= 8.0):
            raise ValueError(f"R17 open-sale path 8 percent alarm is inconsistent for {key}")

        named = row.get("named_exception") is True
        expected_path_state = (
            NAMED_PATH_STATE if crossed and named else MISSED_PATH_STATE if crossed else OPEN_PATH_STATE
        )
        if row.get("path_state") != expected_path_state:
            raise ValueError(f"R17 open-sale path state is inconsistent for {key}")
        for field in ("active_buy_quantity", "sale_attributed_active_buy_quantity"):
            value = row.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"R17 open-sale path {field} is invalid for {key}")
        if row.get("sale_attributed_active_buy_quantity", 0) > row.get("active_buy_quantity", 0):
            raise ValueError(f"R17 open-sale path active BUY attribution exceeds inventory for {key}")

        evidence = {
            "source": path_source_path,
            "source_generated_at": generated_at,
            "chart_from": row.get("chart_from"),
            "chart_to": row.get("chart_to"),
            "chart_point_count": row.get("chart_point_count"),
            "remaining_open_quantity": remaining,
            "remaining_open_lot_count": lot_count,
            "open_sale_transaction_ids": transaction_ids,
            "active_buy_quantity": row.get("active_buy_quantity"),
            "sale_attributed_active_buy_quantity": row.get(
                "sale_attributed_active_buy_quantity"
            ),
            "maximum_open_lot_drop_percent": maximum,
            "current_drop_below_weighted_marker_percent": row.get(
                "current_drop_below_weighted_marker_percent"
            ),
            "open_lots_crossing_8pct_alarm": lot_crossings,
            "crossed_8pct_review_alarm": crossed,
            "technical": row.get("technical"),
            "rsi": row.get("rsi"),
            "atr20_percent_of_current_close": row.get("atr20_percent_of_current_close"),
            "named_exception": named,
            "path_state": expected_path_state,
        }
        result[key] = evidence
        total_lots += lot_count
        total_quantity += remaining
        crossed_rows += crossed
        crossed_lots += lot_crossings
        named_crossed_rows += crossed and named

    summary = path_payload.get("summary") if isinstance(path_payload.get("summary"), dict) else {}
    expected_summary = {
        "exact_account_rows": len(result),
        "unique_orderbooks": len({key[2] for key in result}),
        "exact_open_sale_lots": total_lots,
        "remaining_open_quantity": total_quantity,
        "rows_crossing_8pct_review_alarm": crossed_rows,
        "lots_crossing_8pct_review_alarm": crossed_lots,
        "named_exception_rows_with_crossing": named_crossed_rows,
        "path_or_marker_errors": 0,
    }
    for field, expected in expected_summary.items():
        if summary.get(field) != expected:
            raise ValueError(f"R17 open-sale path summary {field} does not reconcile")
    return result


def _has_reviewable_hold_plan(position: dict[str, Any] | None, expected: str) -> bool:
    if not isinstance(position, dict):
        return False
    return (
        position.get("protection_classification") == expected
        and all(
            bool(str(position.get(field) or "").strip())
            for field in (
                "instrument",
                "strategy_class",
                "thesis",
                "audit_status",
                "stance",
                "protection_reason",
                "next_gate",
            )
        )
    )


def _has_explicit_retained_core_during_protection_repair(
    position: dict[str, Any] | None,
) -> bool:
    """Keep economic intent independent from an unresolved tactical stop repair."""

    if not _has_reviewable_hold_plan(position, "REPAIR_REQUIRED"):
        return False
    assert isinstance(position, dict)
    reviewed_text = " ".join(
        str(position.get(field) or "").strip().lower()
        for field in ("stance", "recommendation", "proposed_correction")
    )
    return "core" in reviewed_text and any(
        verb in reviewed_text for verb in ("retain", "preserve")
    )


def _is_small_exposure_row(row: dict[str, Any]) -> bool:
    holding = row.get("live_holding")
    market_value = row.get("live_market_value_sek")
    return (
        isinstance(holding, (int, float))
        and not isinstance(holding, bool)
        and holding > 0
        and (
            holding <= 5
            or (
                isinstance(market_value, (int, float))
                and not isinstance(market_value, bool)
                and market_value < 20_000
            )
        )
    )


def _conservative_weekday_deadline(reviewed_at: datetime) -> str:
    """Return a deadline no later than five actual exchange sessions.

    Exchange holidays can only make this weekday ceiling stricter, never later
    than the objective's five-regular-session maximum.
    """

    cursor = reviewed_at.astimezone(STOCKHOLM).date()
    sessions = 0
    while sessions < SMALL_HOLD_MAX_REGULAR_SESSIONS:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            sessions += 1
    return cursor.isoformat()


def _small_hold_revalidation(
    position: dict[str, Any],
    *,
    reference_time: str,
) -> dict[str, Any]:
    reviewed_raw = str(position.get("updated_at") or "").strip()
    reviewed_at = _artifact_time(reviewed_raw)
    reference_at = _artifact_time(reference_time)
    purpose = str(position.get("thesis") or "").strip()
    hold_reason = (
        str(position.get("stance") or "").strip()
        or str(position.get("protection_reason") or "").strip()
    )
    next_gate = str(position.get("next_gate") or "").strip()
    result: dict[str, Any] = {
        "hold_revalidation_required": True,
        "revalidation_reviewed_at": reviewed_raw or None,
        "revalidation_due_by": None,
        "revalidation_regular_session_limit": SMALL_HOLD_MAX_REGULAR_SESSIONS,
        "revalidation_calendar_basis": SMALL_HOLD_CALENDAR_BASIS,
        "revalidation_status": "INVALID",
        "economic_purpose": purpose,
        "why_rebuild_is_currently_inferior": (
            "Rebuilding is currently inferior until this reviewed instrument-specific gate is met: "
            f"{next_gate}"
        ),
        "why_exit_is_currently_inferior": (
            "Exiting is currently inferior under the reviewed current-holding rationale: "
            f"{hold_reason}"
        ),
    }
    if not reviewed_raw:
        result["revalidation_status"] = "MISSING"
        return result
    if (
        reviewed_at is None
        or reference_at is None
        or reviewed_at.tzinfo is None
        or reference_at.tzinfo is None
    ):
        return result
    if reviewed_at > reference_at:
        return result
    if any(len(value) < 30 for value in (purpose, hold_reason, next_gate)):
        return result
    deadline = _conservative_weekday_deadline(reviewed_at)
    result["revalidation_due_by"] = deadline
    result["revalidation_status"] = (
        "CURRENT"
        if reference_at.astimezone(STOCKHOLM).date().isoformat() <= deadline
        else "EXPIRED"
    )
    return result


def classify_row(
    row: dict[str, Any],
    registry: dict[str, Any],
    registry_updated_at: str | None,
    reference_time: str,
) -> dict[str, Any]:
    """Return the fail-closed economic classification for one exact row."""

    result = dict(row)
    account_id = str(result.get("account_id") or "")
    orderbook_id = str(result.get("orderbook_id") or "")
    position = _registry_position(registry, account_id, orderbook_id)
    embedded_protection = str(result.get("current_protection_classification") or "")
    registry_protection = str((position or {}).get("protection_classification") or "")
    if position is not None:
        if registry_protection in REGISTRY_PROTECTION_CLASSES:
            if registry_protection != embedded_protection:
                result.setdefault(
                    "superseded_embedded_protection_classification",
                    embedded_protection,
                )
            result["current_protection_classification"] = registry_protection
            result["current_protection_source"] = "CURRENT_POSITION_STRATEGY_REGISTRY"
        else:
            result["current_protection_classification"] = "REPAIR_REQUIRED"
            result["current_protection_source"] = "POSITION_STRATEGY_REGISTRY_FAIL_CLOSED"
    else:
        result.setdefault("current_protection_source", "SOURCE_ARTIFACT_NO_CURRENT_REGISTRY_ROW")

    protection = str(result.get("current_protection_classification") or "")
    existing_decision = str(result.get("low_exposure_decision") or "")

    decision = "REPAIR_REQUIRED"
    source = "R17_FULL_HISTORY_AND_POSITION_STRATEGY_RECONCILIATION"
    reason = "The exact row retains unresolved economic recovery or protection work."
    next_review = str(result.get("exact_next_gate") or "").strip()

    if protection == "NAMED_EXCEPTION":
        decision = "NAMED_EXCEPTION"
        source = "CURRENT_NAMED_EXCEPTION"
        reason = (
            str((position or {}).get("protection_reason") or "").strip()
            or "The current row remains governed by its named-instrument exception."
        )
        next_review = str((position or {}).get("next_gate") or next_review).strip()
    elif protection in {"NON_STOP_ELIGIBLE", "NON_STOP_ELIGIBLE_FUND"}:
        decision = "NON_STOP_ELIGIBLE"
        source = "CURRENT_NON_STOP_ELIGIBLE_CLASSIFICATION"
        reason = (
            str((position or {}).get("protection_reason") or "").strip()
            or "The instrument is verified as non-stop-eligible."
        )
        next_review = str((position or {}).get("next_gate") or next_review).strip()
    elif _quantified_ladder_is_valid(result):
        decision = "BUILD_REVIEW"
        source = "CURRENT_QUANTIFIED_STOCK_SPECIFIC_LADDER"
        reason = str(result.get("coverage_reason") or "").strip()
        next_review = str(result.get("exact_next_gate") or "").strip()
    elif (
        protection in HOLD_PROTECTION_CLASSES
        and _has_reviewable_hold_plan(position, protection)
    ):
        decision = "INTENTIONAL_MARKER_OR_CORE_HOLD"
        source = "CURRENT_REVIEWED_POSITION_STRATEGY"
        reason = str(position.get("protection_reason") or "").strip()
        next_review = str(position.get("next_gate") or "").strip()
    elif (
        protection == "REPAIR_REQUIRED"
        and _has_explicit_retained_core_during_protection_repair(position)
    ):
        decision = "INTENTIONAL_MARKER_OR_CORE_HOLD"
        source = "CURRENT_REVIEWED_CORE_WITH_INDEPENDENT_PROTECTION_REPAIR"
        reason = str(position.get("stance") or "").strip()
        next_review = str(position.get("next_gate") or "").strip()
    elif (
        existing_decision == "EXIT_OR_NO_REENTRY_REVIEW"
        and isinstance(result.get("no_reentry_decision"), dict)
    ):
        decision = "EXIT_OR_NO_REENTRY_REVIEW"
        source = "CURRENT_STRUCTURED_EXIT_OR_NO_REENTRY_DECISION"
        reason = str(result.get("coverage_reason") or "").strip()
        next_review = str(result.get("exact_next_gate") or "").strip()
    elif protection in FULL_EXIT_PROTECTION_CLASSES:
        reason = (
            str(result.get("coverage_reason") or "").strip()
            or "The exact full-exit row still requires an instrument-specific rebuild or terminal decision."
        )
        next_review = str(result.get("exact_next_gate") or next_review).strip()
    elif protection == "REPAIR_REQUIRED":
        reason = (
            str((position or {}).get("protection_reason") or "").strip()
            or "The current position-protection plan remains REPAIR_REQUIRED."
        )
        next_review = str((position or {}).get("next_gate") or next_review).strip()

    revalidation: dict[str, Any] = {}
    if (
        decision == "INTENTIONAL_MARKER_OR_CORE_HOLD"
        and _is_small_exposure_row(result)
        and isinstance(position, dict)
    ):
        revalidation = _small_hold_revalidation(
            position,
            reference_time=reference_time,
        )
        status = revalidation["revalidation_status"]
        if status != "CURRENT":
            prior_reason = reason
            decision = "REPAIR_REQUIRED"
            source = f"SMALL_HOLD_REVALIDATION_{status}_FAIL_CLOSED"
            due_by = revalidation.get("revalidation_due_by") or "an unavailable deadline"
            reason = (
                f"{result.get('instrument')}: the prior small-position hold review is {status.lower()} "
                f"and cannot remain economically resolved; its deadline was {due_by}. Prior rationale: "
                f"{prior_reason}"
            )
            next_review = (
                "Revalidate the exact live holding, economic purpose, rebuild-versus-exit comparison, "
                "thesis, catalyst, technicals, risk, factor/capacity and full friction now; then record "
                "a new deadline no later than five regular sessions."
            )
    result["low_exposure_decision"] = decision
    result["economic_resolution"] = {
        "state": decision,
        "source": source,
        "registry_updated_at": registry_updated_at,
        "position_audit_status": (position or {}).get("audit_status"),
        "position_bucket": (position or {}).get("bucket"),
        "strategy_class": (position or {}).get("strategy_class"),
        "reason": reason,
        "next_review": next_review,
        **revalidation,
    }
    return result


def apply_terminal_decisions_to_dynamic_rows(
    rows: list[dict[str, Any]],
    remediation_payload: dict[str, Any],
) -> None:
    """Mirror exact cycle-level terminal decisions into current dynamic coverage."""

    dynamic_rows = {_row_key(row): row for row in rows}
    for remediation in remediation_payload.get("rows", []):
        if not isinstance(remediation, dict):
            continue
        mixed_resolution = remediation.get("mixed_lot_resolution")
        terminal = remediation.get("state") == "EXPLICIT_NO_REENTRY_CURRENT_THESIS"
        dormant_decision_present = remediation.get("dormant_ladder_decision") is not None
        dormant_ladder_valid = _remediation_dormant_ladder_is_valid(remediation)
        if dormant_decision_present and not dormant_ladder_valid:
            raise ValueError(
                f"independent dormant ladder decision is invalid for {_row_key(remediation)}"
            )
        key = _row_key(remediation)
        row = dynamic_rows.get(key)
        if row is None:
            raise ValueError(f"R17 terminal dynamic row is missing for {key}")
        if row.get("recovery_cycle_id") != remediation.get("recovery_cycle_id"):
            raise ValueError(f"R17 terminal dynamic recovery cycle mismatch for {key}")
        row["sale_lot_ids"] = [
            str(lot.get("sale_lot_id") or "")
            for lot in remediation.get("sale_lots", [])
            if isinstance(lot, dict)
        ]
        row["open_sale_lot_ids"] = [
            str(lot.get("sale_lot_id") or "")
            for lot in remediation.get("sale_lots", [])
            if isinstance(lot, dict)
            and int(lot.get("remaining_open_quantity", 0) or 0) > 0
        ]
        row["mixed_lot_resolution"] = copy.deepcopy(mixed_resolution)
        row["partial_terminal_decisions"] = copy.deepcopy(
            remediation.get("partial_terminal_decisions", [])
        )
        if (
            not terminal
            and not isinstance(mixed_resolution, dict)
            and not dormant_ladder_valid
        ):
            continue

        sale_attributed = int(remediation.get("sale_attributed_active_buy_quantity", 0) or 0)
        pre_sale = int(remediation.get("pre_sale_active_buy_quantity", 0) or 0)
        unattributed = int(remediation.get("unattributed_active_buy_quantity", 0) or 0)
        broker_active = sale_attributed + pre_sale + unattributed
        if int(row.get("active_buy_volume", 0) or 0) != broker_active:
            raise ValueError(f"R19 dynamic broker active BUY inventory mismatch for {key}")
        row["sale_attributed_active_buy_quantity"] = sale_attributed
        row["pre_sale_active_buy_quantity"] = pre_sale
        row["unattributed_active_buy_quantity"] = unattributed
        row["unattributed_later_buy_quantity"] = int(
            remediation.get("unattributed_later_buy_quantity", 0) or 0
        )
        row["latest_recent_sale_date"] = remediation.get("sale_date")
        _clear_instrument_specific_path_context(row)

        if not terminal:
            dormant_decision = remediation.get("dormant_ladder_decision")
            if dormant_decision is not None:
                row["dormant_ladder_decision"] = copy.deepcopy(dormant_decision)
            else:
                row.pop("dormant_ladder_decision", None)
            remaining = int(remediation.get("remaining_open_quantity", 0) or 0)
            row["target_rebuild_quantity"] = remaining if remaining > 0 else None
            row["stages_percent_below_sold_marker"] = remediation.get(
                "recorded_stage_percentages_below_marker", "PERCENTAGE_NOT_SET"
            )
            row["stage_quantities"] = remediation.get("recorded_stage_quantities")
            row.pop("no_reentry_decision", None)
            row.pop("full_path_evidence", None)
            if remaining > 0:
                open_lot_count = sum(
                    _is_positive_integer(lot.get("remaining_open_quantity"))
                    for lot in remediation.get("sale_lots", [])
                    if isinstance(lot, dict)
                )
                instrument = str(row.get("instrument") or remediation.get("instrument") or "Instrument")
                closed = int(remediation.get("closed_no_reentry_quantity", 0) or 0)
                if dormant_ladder_valid:
                    if dormant_decision is not None:
                        reason, next_review = _independent_dormant_ladder_semantics(row)
                        row["coverage_reason"] = reason
                        row["exact_next_gate"] = next_review
                    row["buyback_coverage_state"] = "LADDER_DORMANT"
                    if not _quantified_ladder_is_valid(row):
                        raise ValueError(f"R19 dormant ladder does not exactly match dynamic coverage for {key}")
                    row["low_exposure_decision"] = "BUILD_REVIEW"
                    resolution = dict(row.get("economic_resolution") or {})
                    for field in (
                        "hold_revalidation_required",
                        "revalidation_reviewed_at",
                        "revalidation_due_by",
                        "revalidation_regular_session_limit",
                        "revalidation_calendar_basis",
                        "revalidation_status",
                        "economic_purpose",
                        "why_rebuild_is_currently_inferior",
                        "why_exit_is_currently_inferior",
                    ):
                        resolution.pop(field, None)
                    row["economic_resolution"] = {
                        **resolution,
                        "state": "BUILD_REVIEW",
                        "source": "CURRENT_QUANTIFIED_STOCK_SPECIFIC_LADDER",
                        "reason": row.get("coverage_reason"),
                        "next_review": row.get("exact_next_gate"),
                    }
                else:
                    row["buyback_coverage_state"] = "LADDER_GAP"
                    row["coverage_reason"] = (
                        f"{instrument}: R19 exact mixed-lot review attributes {sale_attributed} active BUY "
                        f"shares, records {closed} dated terminal closures, and leaves {remaining} unresolved "
                        f"shares across {open_lot_count} immutable sale lots. The residual has no supported "
                        "stock-specific percentage ladder."
                    )
            else:
                row["buyback_coverage_state"] = "LEDGER_ONLY"
                instrument = str(row.get("instrument") or remediation.get("instrument") or "Instrument")
                filled = int(remediation.get("later_filled_quantity", 0) or 0)
                closed = int(remediation.get("closed_no_reentry_quantity", 0) or 0)
                closure_unit = "share" if closed == 1 else "shares"
                row["coverage_reason"] = (
                    f"{instrument}: exact mixed-lot review reconciles the complete sold cycle with "
                    f"{filled} later filled shares, {sale_attributed} active sale-attributed BUY "
                    f"shares, and {closed} dated terminal-closure {closure_unit}; no sold-slice remainder "
                    "is open."
                )
                resolution = row.get("economic_resolution")
                if isinstance(resolution, dict):
                    current_next_review = str(resolution.get("next_review") or "").strip()
                    if current_next_review:
                        row["exact_next_gate"] = current_next_review
            continue

        decision = remediation.get("no_reentry_decision")
        if not isinstance(decision, dict):
            raise ValueError(f"R17 terminal remediation decision is missing for {key}")
        row.pop("dormant_ladder_decision", None)
        if sale_attributed != 0:
            raise ValueError(f"R17 terminal dynamic row has contradictory same-sale BUY inventory for {key}")
        reviewed_holding = decision.get("current_holding")
        if reviewed_holding is not None and int(row.get("live_holding", 0) or 0) != int(
            reviewed_holding
        ):
            raise ValueError(f"R17 terminal dynamic holding differs from the reviewed decision for {key}")

        expiry = str(decision.get("expires_at") or "")
        row["buyback_coverage_state"] = "LEDGER_ONLY"
        row["target_rebuild_quantity"] = None
        row["stages_percent_below_sold_marker"] = "PERCENTAGE_NOT_SET"
        row["stage_quantities"] = None
        row["no_reentry_decision"] = copy.deepcopy(decision)
        if isinstance(mixed_resolution, dict):
            row["mixed_lot_resolution"] = copy.deepcopy(mixed_resolution)
            row["partial_terminal_decisions"] = copy.deepcopy(
                remediation.get("partial_terminal_decisions", [])
            )
        row["coverage_reason"] = (
            "A current structured no-reentry decision closes only the exact unresolved "
            "sale-lot remainder while preserving all prior fills and immutable sale history."
        )
        row["exact_next_gate"] = (
            f"Revalidate the exact no-reentry decision no later than {expiry}; reopen the "
            "remaining sold slice if newer thesis, event, technical or complete-path evidence contradicts it."
        )
        row.pop("full_path_evidence", None)


def enrich_payload(
    payload: dict[str, Any],
    registry: dict[str, Any],
    *,
    generated_at: str,
    source_path: str,
    path_evidence: dict[str, Any] | None = None,
    path_source_path: str | None = None,
    remediation_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if payload.get("artifact") != "PORTFOLIO_BUYBACK_LIVE_COVERAGE":
        raise ValueError("input is not a live buyback coverage artifact")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("input buyback coverage rows must be non-empty")

    result = dict(payload)
    registry_updated_at = registry.get("updated_at")
    enriched_rows = [
        classify_row(row, registry, registry_updated_at, generated_at)
        for row in rows
        if isinstance(row, dict)
    ]
    if len(enriched_rows) != len(rows):
        raise ValueError("input buyback coverage contains a non-object row")
    if remediation_payload is not None:
        apply_terminal_decisions_to_dynamic_rows(enriched_rows, remediation_payload)

    path_rows: dict[tuple[str, str, str], dict[str, Any]] | None = None
    if path_evidence is not None:
        if not path_source_path:
            raise ValueError("path_source_path is required with path_evidence")
        path_rows = _path_evidence_rows(
            path_evidence,
            path_source_path=path_source_path,
        )
        open_rows = {
            _row_key(row): row
            for row in enriched_rows
            if _is_positive_integer(row.get("target_rebuild_quantity"))
        }
        if set(open_rows) != set(path_rows):
            missing = sorted(set(open_rows) - set(path_rows))
            extra = sorted(set(path_rows) - set(open_rows))
            raise ValueError(
                f"R17 open-sale path rows do not match dynamic open rows; missing={missing}, extra={extra}"
            )
        for key, row in open_rows.items():
            evidence = path_rows[key]
            if row.get("target_rebuild_quantity") != evidence["remaining_open_quantity"]:
                raise ValueError(f"R17 open-sale path target quantity mismatch for {key}")
            if row.get("active_buy_volume", 0) != evidence["active_buy_quantity"]:
                raise ValueError(f"R17 path evidence active BUY quantity mismatch for {key}")
            if row.get("sale_attributed_active_buy_quantity", 0) != evidence[
                "sale_attributed_active_buy_quantity"
            ]:
                raise ValueError(f"R17 path evidence sale-attributed BUY quantity mismatch for {key}")
            if (row.get("current_protection_classification") == "NAMED_EXCEPTION") != evidence[
                "named_exception"
            ]:
                raise ValueError(f"R17 named-exception path flag mismatch for {key}")

            _clear_instrument_specific_path_context(row)
            row["full_path_evidence"] = evidence
            if evidence["crossed_8pct_review_alarm"] and not evidence["named_exception"]:
                context = _instrument_specific_path_context(row, evidence)
                row["buyback_coverage_state"] = "REPAIR_REQUIRED"
                row[PATH_CONTEXT_FIELD] = context
                row["coverage_reason"] = _path_reconciled_reason(context, named=False)
                row["exact_next_gate"] = context["exact_next_gate"]
                row["economic_resolution"] = {
                    **dict(row.get("economic_resolution") or {}),
                    PATH_CONTEXT_FIELD: copy.deepcopy(context),
                }
            elif evidence["crossed_8pct_review_alarm"]:
                context = _instrument_specific_path_context(row, evidence)
                row["buyback_coverage_state"] = "LADDER_GAP"
                row["low_exposure_decision"] = "NAMED_EXCEPTION"
                row[PATH_CONTEXT_FIELD] = context
                row["coverage_reason"] = _path_reconciled_reason(context, named=True)
                row["exact_next_gate"] = context["exact_next_gate"]
                row["economic_resolution"] = {
                    **dict(row.get("economic_resolution") or {}),
                    PATH_CONTEXT_FIELD: copy.deepcopy(context),
                }
            else:
                resolution = dict(row.get("economic_resolution") or {})
                resolution["source"] = "R17_COMPLETE_PATH_AND_POSITION_STRATEGY_RECONCILIATION"
                row["economic_resolution"] = resolution

    result["schema_version"] = max(int(payload.get("schema_version", 0) or 0), 9)
    result["generated_at"] = generated_at
    result["superseded"] = False
    result["supersedes"] = source_path
    result["rows"] = enriched_rows
    sources = list(payload.get("source_artifacts", []))
    for source in (source_path, ".avanza_position_strategy.json", path_source_path):
        if source is None:
            continue
        if source not in sources:
            sources.append(source)
    if remediation_payload is not None:
        terminal_source = (
            remediation_payload.get("terminal_decision_overlay", {}).get("source")
            if isinstance(remediation_payload.get("terminal_decision_overlay"), dict)
            else None
        )
        if terminal_source and terminal_source not in sources:
            sources.append(terminal_source)
    result["source_artifacts"] = sources
    result["economic_classification"] = {
        "authority": "LOCAL_REVIEW_ONLY",
        "classified_at": generated_at,
        "registry_updated_at": registry_updated_at,
        "contract": (
            "Sold-cycle recovery and current exposure intent are independent controls. "
            "A reviewed current core may coexist with an open recovery repair, but neither "
            "state can close or erase the other. Unsupported percentages and protection "
            "repairs remain fail-closed."
        ),
        "broker_mutation": False,
    }
    if path_rows is not None:
        result["path_evidence_contract"] = {
            "authority": "LOCAL_REVIEW_ONLY",
            "source": path_source_path,
            "source_generated_at": path_evidence.get("generated_at") if path_evidence else None,
            "open_row_count": len(path_rows),
            "review_alarm_percent": 8.0,
            "rebound_erases_crossing": False,
            "broker_mutation": False,
        }

    summary = dict(payload.get("summary", {}))
    buyback_counts = Counter(row["buyback_coverage_state"] for row in enriched_rows)
    low_counts = Counter(row["low_exposure_decision"] for row in enriched_rows)
    summary["buyback_coverage_state_counts"] = {
        state: buyback_counts.get(state, 0)
        for state in (
            "LADDER_ACTIVE",
            "LADDER_DORMANT",
            "LEDGER_ONLY",
            "LADDER_GAP",
            "REPAIR_REQUIRED",
            "NAMED_EXCEPTION",
        )
    }
    summary["low_exposure_decision_counts"] = {
        state: low_counts.get(state, 0)
        for state in (
            "BUILD_REVIEW",
            "INTENTIONAL_MARKER_OR_CORE_HOLD",
            "EXIT_OR_NO_REENTRY_REVIEW",
            "NAMED_EXCEPTION",
            "NON_STOP_ELIGIBLE",
            "REPAIR_REQUIRED",
        )
    }
    summary["economically_unresolved_rows"] = low_counts.get("REPAIR_REQUIRED", 0)
    summary["economically_resolved_rows"] = len(enriched_rows) - summary["economically_unresolved_rows"]
    summary["percentage_ladders_with_supported_stages"] = sum(
        isinstance(row.get("stages_percent_below_sold_marker"), list)
        for row in enriched_rows
    )
    summary["percentage_not_set_rows"] = sum(
        row.get("stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET"
        for row in enriched_rows
    )
    revalidation_counts = Counter(
        str(row.get("economic_resolution", {}).get("revalidation_status") or "")
        for row in enriched_rows
        if row.get("economic_resolution", {}).get("hold_revalidation_required") is True
    )
    summary["small_hold_revalidation_required_rows"] = sum(revalidation_counts.values())
    summary["small_hold_revalidation_status_counts"] = {
        status: revalidation_counts.get(status, 0)
        for status in ("CURRENT", "EXPIRED", "MISSING", "INVALID")
    }
    open_target_rows = [
        row for row in enriched_rows if _is_positive_integer(row.get("target_rebuild_quantity"))
    ]
    summary["full_history_open_rows"] = len(open_target_rows)
    summary["full_history_open_quantity"] = sum(
        int(row.get("target_rebuild_quantity", 0) or 0) for row in open_target_rows
    )
    if path_rows is not None:
        crossed_rows = [evidence for evidence in path_rows.values() if evidence["crossed_8pct_review_alarm"]]
        named_crossed_rows = [evidence for evidence in crossed_rows if evidence["named_exception"]]
        summary["full_path_evidence_rows"] = len(path_rows)
        summary["rows_crossing_8pct_review_alarm"] = len(crossed_rows)
        summary["repair_required_missed_path_rows"] = len(crossed_rows) - len(named_crossed_rows)
        summary["sold_cycle_repair_required_rows"] = summary[
            "repair_required_missed_path_rows"
        ]
        summary["named_exception_path_review_rows"] = len(named_crossed_rows)
        summary["rows_without_crossing_8pct_review_alarm"] = len(path_rows) - len(crossed_rows)
        summary["path_evidence_missing_rows"] = 0
    result["summary"] = summary

    blockers = [
        str(value)
        for value in payload.get("blockers", [])
        if "exact governed rows still need an evidence-backed economic" not in str(value)
        and "ordinary exact account rows crossed the 8 percent" not in str(value)
        and "named exact account rows crossed the 8 percent" not in str(value)
        and "open exact account rows remain below the 8 percent" not in str(value)
    ]
    unresolved = summary["economically_unresolved_rows"]
    below = int(summary.get("below_20000_sek_rows", 0) or 0)
    blockers.insert(
        1,
        f"{unresolved} exact governed rows still need an evidence-backed economic build, "
        f"hold or exit outcome; {below} current live positions are below 20,000 SEK.",
    )
    if path_rows is not None:
        blockers.insert(
            0,
            f"{summary['repair_required_missed_path_rows']} ordinary exact account rows crossed the 8 percent "
            "review alarm without qualifying exact-lot recovery and remain REPAIR_REQUIRED.",
        )
        blockers.insert(
            1,
            f"{summary['named_exception_path_review_rows']} named exact account rows crossed the 8 percent "
            "review alarm and remain separately blocked for fresh named review.",
        )
        blockers.insert(
            2,
            f"{summary['rows_without_crossing_8pct_review_alarm']} open exact account rows remain below the "
            "8 percent alarm but still lack a supported stock-specific percentage ladder or terminal decision.",
        )
    result["blockers"] = blockers
    return result


def enrich_remediation_payload(
    payload: dict[str, Any],
    path_evidence: dict[str, Any],
    *,
    generated_at: str,
    source_path: str,
    path_source_path: str,
) -> dict[str, Any]:
    """Attach the same complete-path evidence to the authoritative sale-lot ledger."""

    if payload.get("artifact") != "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE":
        raise ValueError("remediation input is not a sold-marker remediation artifact")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("remediation rows must be non-empty")
    path_rows = _path_evidence_rows(path_evidence, path_source_path=path_source_path)
    open_rows = {
        _row_key(row): row
        for row in rows
        if isinstance(row, dict) and _is_positive_integer(row.get("remaining_open_quantity"))
    }
    if set(open_rows) != set(path_rows):
        missing = sorted(set(open_rows) - set(path_rows))
        extra = sorted(set(path_rows) - set(open_rows))
        raise ValueError(
            f"R17 path rows do not match remediation open rows; missing={missing}, extra={extra}"
        )

    result = dict(payload)
    enriched_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("remediation contains a non-object row")
        enriched = dict(row)
        key = _row_key(row)
        evidence = path_rows.get(key)
        if evidence is not None:
            if row.get("remaining_open_quantity") != evidence["remaining_open_quantity"]:
                raise ValueError(f"R17 remediation path quantity mismatch for {key}")
            enriched["full_path_evidence"] = evidence
            if evidence["crossed_8pct_review_alarm"] and evidence["named_exception"]:
                enriched["state"] = "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
            elif evidence["crossed_8pct_review_alarm"]:
                enriched["state"] = "REPAIR_REQUIRED_MISSED_PATH"
            elif _remediation_dormant_ladder_is_valid(row):
                enriched["state"] = "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
                decision = row.get("dormant_ladder_decision")
                if isinstance(decision, dict):
                    enriched["next_gate"] = str(decision.get("next_review") or "").strip()
            else:
                enriched["state"] = "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET"
        enriched_rows.append(enriched)

    result["schema_version"] = max(int(payload.get("schema_version", 0) or 0), 4)
    result["generated_at"] = generated_at
    result["verified_at"] = generated_at
    result["path_snapshot_at"] = str(path_evidence.get("generated_at") or generated_at)
    result["status"] = "ACTIVE_REPAIR_REQUIRED"
    result["superseded"] = False
    result["supersedes"] = source_path
    result["rows"] = enriched_rows
    sources = list(payload.get("sources", []))
    for source in (source_path, path_source_path):
        if source not in sources:
            sources.append(source)
    result["sources"] = sources
    result["path_evidence_contract"] = {
        "authority": "LOCAL_REVIEW_ONLY",
        "source": path_source_path,
        "source_generated_at": path_evidence.get("generated_at"),
        "open_row_count": len(path_rows),
        "review_alarm_percent": 8.0,
        "rebound_erases_crossing": False,
        "broker_mutation": False,
    }

    summary = _remediation_summary(enriched_rows, payload.get("summary"))
    result["summary"] = summary
    result["conclusion"] = _remediation_conclusion(summary)

    repair_rows = [row for row in enriched_rows if str(row.get("state") or "").startswith("REPAIR_REQUIRED")]
    named_path_rows = [
        row for row in enriched_rows if row.get("state") == "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
    ]

    verification = dict(payload.get("verification", {}))
    for tenant in ("personal", "darkcell"):
        proof = dict(verification.get(tenant, {}))
        proof["sold_cycle_repair_orderbook_ids"] = sorted(
            str(row.get("orderbook_id"))
            for row in repair_rows
            if row.get("tenant_session_id") == tenant
        )
        proof["named_path_review_orderbook_ids"] = sorted(
            str(row.get("orderbook_id"))
            for row in named_path_rows
            if row.get("tenant_session_id") == tenant
        )
        verification[tenant] = proof
    result["verification"] = verification
    return result


def build_full_history_canonical(
    base: dict[str, Any],
    posted_sales: dict[str, Any],
    raw_boundary: dict[str, Any],
    remediation: dict[str, Any],
    corporate_action_resolution: dict[str, Any],
    readiness: dict[str, Any],
    *,
    generated_at: str,
    source_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Build the current complete sold-lot boundary without a fixed row count."""

    if base.get("artifact") != "PORTFOLIO_R208_R137_STRUCTURAL_CANONICAL_INPUT":
        raise ValueError("full-history base artifact is invalid")
    if posted_sales.get("artifact") != "PORTFOLIO_R385_SEP2_ETH_POSTED_SETTLEMENT_IDENTITIES":
        raise ValueError("posted-sale delta artifact is invalid")
    if raw_boundary.get("artifact") != "PORTFOLIO_R386_FULL_RAW_BOUNDARY_AFTER_ETH_SETTLEMENT":
        raise ValueError("raw-boundary artifact is invalid")
    if remediation.get("artifact") != "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE":
        raise ValueError("current remediation artifact is invalid")
    if (
        corporate_action_resolution.get("artifact")
        != "PORTFOLIO_R132_CORPORATE_ACTION_LINEAGE_RESOLUTION"
    ):
        raise ValueError("corporate-action resolution artifact is invalid")
    if readiness.get("artifact") != "PORTFOLIO_R389_FULL_GOVERNANCE_ENFORCEMENT_READINESS":
        raise ValueError("R390 readiness artifact is invalid")

    mappings = copy.deepcopy(base.get("source_identity_to_lineage_map", []))
    lineages = copy.deepcopy(base.get("effective_lineages", []))
    lots = copy.deepcopy(base.get("immutable_sale_lots", []))
    buy_sources = copy.deepcopy(base.get("buy_sources", []))
    fill_allocations = copy.deepcopy(base.get("qualifying_fill_allocations", []))
    terminal_closures = copy.deepcopy(base.get("terminal_closures", []))
    projection = copy.deepcopy(base.get("dynamic_mirror_projection", []))
    if not all(isinstance(value, list) for value in (
        mappings,
        lineages,
        lots,
        buy_sources,
        fill_allocations,
        terminal_closures,
        projection,
    )):
        raise ValueError("full-history base arrays are incomplete")

    mapping_by_key = {
        (
            str(row.get("tenant_session_id") or ""),
            str(row.get("account_id") or ""),
            str(row.get("orderbook_id") or ""),
        ): row
        for row in mappings
        if isinstance(row, dict)
    }
    lineage_by_id = {
        str(row.get("effective_lineage_id") or ""): row
        for row in lineages
        if isinstance(row, dict)
    }
    lot_by_id = {
        str(row.get("sale_lot_id") or ""): row
        for row in lots
        if isinstance(row, dict)
    }

    # The structural input preserves original closure quantities. Promote a
    # separate canonical quantity so post-split lineages reconcile in their
    # normalized unit without rewriting the source evidence.
    for closure in terminal_closures:
        if not isinstance(closure, dict):
            continue
        canonical_quantity = closure.get(
            "post_split_equivalent_terminal_closure_antal_exact",
            closure.get("terminal_closure_antal_exact"),
        )
        closure["canonical_terminal_closure_antal_exact"] = _exact_text(
            canonical_quantity
        )
        closure["canonical_application"] = True

    # Refresh inner-lot allocations from the newest authoritative remediation.
    remediation_rows = [row for row in remediation.get("rows", []) if isinstance(row, dict)]
    for remediation_row in remediation_rows:
        for current_lot in remediation_row.get("sale_lots", []):
            if not isinstance(current_lot, dict):
                continue
            lot_id = str(current_lot.get("sale_lot_id") or "")
            canonical_lot = lot_by_id.get(lot_id)
            if canonical_lot is None:
                raise ValueError(f"current remediation lot is absent from canonical base: {lot_id}")
            canonical_lot.update(
                {
                    "raw_sold_quantity_exact": _exact_text(current_lot.get("raw_sold_quantity")),
                    "normalized_sold_quantity_exact": _exact_text(current_lot.get("sold_quantity")),
                    "qualifying_filled_quantity_exact": _exact_text(
                        current_lot.get("qualifying_filled_quantity", 0)
                    ),
                    "active_recovery_quantity_exact": _exact_text(
                        current_lot.get("active_recovery_quantity", 0)
                    ),
                    "terminal_closure_quantity_exact": _exact_text(
                        current_lot.get("closed_no_reentry_quantity", 0)
                    ),
                    "remaining_open_quantity_exact": _exact_text(
                        current_lot.get("remaining_open_quantity", 0)
                    ),
                    "quantity_normalization_factor_exact": _exact_text(
                        current_lot.get("quantity_normalization_factor", 1)
                    ),
                    "parity_delta_exact": "0",
                }
            )

    # Flatten every embedded inner terminal decision; the structural base only
    # carried the outer overlays and could not prove inner closure-source parity.
    terminal_closure_ids = {
        str(row.get("closure_id") or "")
        for row in terminal_closures
        if isinstance(row, dict)
    }
    for remediation_row in remediation_rows:
        resolution = remediation_row.get("economic_resolution")
        selected_outcome = str(
            remediation_row.get("low_exposure_decision")
            or (resolution.get("state") if isinstance(resolution, dict) else "")
            or "TERMINAL_CURRENT_DECISION"
        )
        for current_lot in remediation_row.get("sale_lots", []):
            if not isinstance(current_lot, dict):
                continue
            lot_id = str(current_lot.get("sale_lot_id") or "")
            canonical_lot = lot_by_id[lot_id]
            lot_closures = list(current_lot.get("terminal_closure_decisions", []))
            direct_closure = current_lot.get("no_reentry_decision")
            if isinstance(direct_closure, dict):
                lot_closures.append(direct_closure)
            for closure in lot_closures:
                if not isinstance(closure, dict):
                    continue
                closure_id = str(closure.get("decision_id") or "")
                if not closure_id:
                    raise ValueError(f"inner terminal closure id is missing for {lot_id}")
                if closure_id in terminal_closure_ids:
                    continue
                terminal_closures.append(
                    {
                        "closure_id": closure_id,
                        "decision_id": str(
                            closure.get("parent_decision_id") or closure_id
                        ),
                        "tenant_session_id": str(
                            closure.get("tenant_session_id") or ""
                        ),
                        "account_id": str(closure.get("account_id") or ""),
                        "identity_key": f"orderbook-{closure.get('orderbook_id')}",
                        "orderbook_id": str(closure.get("orderbook_id") or ""),
                        "instrument": remediation_row.get("instrument"),
                        "sale_lot_id": lot_id,
                        "sale_transaction_id": str(
                            closure.get("sale_transaction_id") or ""
                        ),
                        "terminal_closure_antal_exact": _exact_text(
                            closure.get("closed_quantity")
                        ),
                        "canonical_terminal_closure_antal_exact": _exact_text(
                            closure.get("closed_quantity")
                        ),
                        "quantity_unit": "NORMALIZED_ANTAL_EXACT",
                        "remaining_antal_after_closure_exact": _exact_text(
                            closure.get("remaining_after_decision_quantity", 0)
                        ),
                        "closure_type": "INNER_EXACT_LOT_TERMINAL_DECISION",
                        "selected_outcome": selected_outcome,
                        "decision_time": closure.get("decision_at"),
                        "last_revalidation": closure.get("last_revalidated_at"),
                        "expires_at": closure.get("expires_at"),
                        "contradiction_status": closure.get("contradiction_status"),
                        "newer_evidence_reviewed": closure.get(
                            "newer_evidence_reviewed"
                        ),
                        "decision_basis": closure.get("decision_basis"),
                        "thesis_evidence": closure.get("thesis_evidence"),
                        "event_evidence": closure.get("event_evidence"),
                        "technical_evidence": closure.get("technical_evidence"),
                        "path_evidence": closure.get("path_evidence"),
                        "evidence_source": "CURRENT_INNER_REMEDIATION",
                        "canonical_application": True,
                    }
                )
                canonical_lot.setdefault("terminal_closure_ids", []).append(
                    closure_id
                )
                terminal_closure_ids.add(closure_id)

    # The corporate-action source contains four closed non-stop-eligible
    # lineages and the exact later BUY sources for FireEye/Mandiant and
    # Luminar. The older structural artifact retained their lot totals but did
    # not materialize these source-to-lot rows, so promote them explicitly.
    corporate_rows = [
        row
        for row in corporate_action_resolution.get("rows", [])
        if isinstance(row, dict)
    ]
    buy_source_by_id = {
        str(row.get("buy_transaction_id") or ""): row
        for row in buy_sources
        if isinstance(row, dict)
    }
    existing_fill_ids = {
        str(row.get("allocation_id") or "")
        for row in fill_allocations
        if isinstance(row, dict)
    }
    event_by_transaction = {
        str(event.get("transaction_id") or ""): event
        for row in corporate_rows
        for event in row.get("events", [])
        if isinstance(event, dict)
    }
    for row in corporate_rows:
        lineage_id = str(row.get("lineage_id") or "")
        lineage = lineage_by_id.get(lineage_id)
        if lineage is None:
            raise ValueError(
                f"corporate-action lineage is absent from canonical base: {lineage_id}"
            )

        recovery_lots: list[list[Any]] = []
        for source_lot in row.get("sale_lots", []):
            if not isinstance(source_lot, dict):
                continue
            quantity = _exact_decimal(
                source_lot.get("qualifying_filled_quantity", 0)
            )
            if quantity > 0:
                recovery_lots.append(
                    [str(source_lot.get("sale_lot_id") or ""), quantity]
                )

        for source in row.get("buy_sources", []):
            if not isinstance(source, dict):
                continue
            source_id = str(source.get("buy_transaction_id") or "")
            recovery_quantity = _exact_decimal(
                source.get("recovery_allocated_quantity", 0)
            )
            if "normalized_quantity" in source:
                source_quantity = _exact_decimal(source.get("normalized_quantity"))
                non_recovery_quantity = source_quantity
            else:
                non_recovery_quantity = _exact_decimal(
                    source.get("non_recovery_quantity", 0)
                )
                source_quantity = recovery_quantity + non_recovery_quantity
            event = event_by_transaction.get(source_id, {})
            canonical_source = buy_source_by_id.get(source_id)
            if canonical_source is None:
                canonical_source = {
                    "buy_transaction_id": source_id,
                    "tenant_session_id": str(row.get("tenant_session_id") or ""),
                    "account_id": str(row.get("account_id") or ""),
                    "effective_lineage_id": lineage_id,
                }
                buy_sources.append(canonical_source)
                buy_source_by_id[source_id] = canonical_source
            canonical_source.update(
                {
                    "buy_timestamp": (
                        f"{event.get('date')}T00:00:00"
                        if event.get("date")
                        else canonical_source.get("buy_timestamp")
                    ),
                    "source_quantity_exact": _exact_text(source_quantity),
                    "allocated_recovery_quantity_exact": _exact_text(
                        recovery_quantity
                    ),
                    "non_recovery_quantity_exact": _exact_text(
                        non_recovery_quantity
                    ),
                    "unattributed_quantity_exact": "0",
                    "raw_source_quantity_exact": _exact_text(
                        abs(_exact_decimal(event.get("volume")))
                        if event.get("volume") is not None
                        else source_quantity
                    ),
                    "classifications": (
                        ["QUALIFYING_FILLED_RECOVERY"]
                        if recovery_quantity > 0
                        else ["PRE_SALE_OR_CORPORATE_ACTION_CORE_INVENTORY"]
                    ),
                    "evidence_source": "R132_CORPORATE_ACTION_RESOLUTION",
                }
            )

            quantity_to_allocate = recovery_quantity
            for recovery_lot in recovery_lots:
                if quantity_to_allocate <= 0:
                    break
                lot_id = str(recovery_lot[0])
                available = _exact_decimal(recovery_lot[1])
                allocated = min(quantity_to_allocate, available)
                if allocated <= 0:
                    continue
                lot = lot_by_id.get(lot_id)
                if lot is None:
                    raise ValueError(
                        f"corporate-action recovery lot is unknown: {lot_id}"
                    )
                allocation_id = (
                    f"r390-corporate-fill-{source_id}-{lot['sale_transaction_id']}"
                )
                if allocation_id in existing_fill_ids:
                    raise ValueError(
                        "corporate-action recovery allocation is duplicated: "
                        f"{allocation_id}"
                    )
                fill_allocations.append(
                    {
                        "allocation_id": allocation_id,
                        "effective_lineage_id": lineage_id,
                        "buy_transaction_id": source_id,
                        "sale_lot_id": lot_id,
                        "quantity_exact": _exact_text(allocated),
                        "allocation_type": "R132_EXACT_CORPORATE_ACTION_RECOVERY",
                    }
                )
                existing_fill_ids.add(allocation_id)
                recovery_lot[1] = available - allocated
                quantity_to_allocate -= allocated
            if quantity_to_allocate != 0:
                raise ValueError(
                    "corporate-action recovery source cannot be allocated exactly: "
                    f"{source_id}"
                )

        if row.get("verified_non_stop_eligible") is not True:
            continue
        for source_lot in row.get("sale_lots", []):
            if not isinstance(source_lot, dict):
                continue
            quantity = _exact_decimal(
                source_lot.get("verified_non_stop_closure_quantity", 0)
            )
            if quantity <= 0:
                continue
            lot_id = str(source_lot.get("sale_lot_id") or "")
            lot = lot_by_id.get(lot_id)
            if lot is None:
                raise ValueError(f"corporate-action closure lot is unknown: {lot_id}")
            closure_id = f"R390-NON-STOP-CLOSE-{lot['sale_transaction_id']}"
            if closure_id in terminal_closure_ids:
                raise ValueError(
                    f"corporate-action terminal closure is duplicated: {closure_id}"
                )
            terminal_closures.append(
                {
                    "closure_id": closure_id,
                    "decision_id": f"R390-NON-STOP-{lineage_id}",
                    "tenant_session_id": str(row.get("tenant_session_id") or ""),
                    "account_id": str(row.get("account_id") or ""),
                    "identity_key": lineage_id,
                    "orderbook_id": row.get("orderbook_id"),
                    "instrument": row.get("instrument"),
                    "sale_lot_id": lot_id,
                    "sale_transaction_id": str(lot.get("sale_transaction_id") or ""),
                    "terminal_closure_antal_exact": _exact_text(quantity),
                    "canonical_terminal_closure_antal_exact": _exact_text(quantity),
                    "quantity_unit": "NORMALIZED_ANTAL_EXACT",
                    "remaining_antal_after_closure_exact": _exact_text(
                        source_lot.get("remaining_open_quantity", 0)
                    ),
                    "closure_type": "VERIFIED_NON_STOP_ELIGIBLE_CLOSURE",
                    "selected_outcome": "NON_STOP_ELIGIBLE",
                    "classification_time": corporate_action_resolution.get(
                        "generated_at"
                    ),
                    "last_revalidation": corporate_action_resolution.get(
                        "generated_at"
                    ),
                    "contradiction_status": "NONE",
                    "verified_non_stop_eligible": True,
                    "non_stop_basis": row.get("non_stop_basis"),
                    "availability_evidence": copy.deepcopy(
                        row.get("current_avanza_search")
                    ),
                    "corporate_action_resolution": row.get(
                        "corporate_action_resolution"
                    ),
                    "evidence_source": "R132_CORPORATE_ACTION_RESOLUTION",
                    "canonical_application": True,
                }
            )
            terminal_closure_ids.add(closure_id)

    # Canonical lot membership is derived from the promoted closure rows, not
    # trusted from an older checkpoint.
    for lot in lots:
        if not isinstance(lot, dict):
            continue
        lot_id = str(lot.get("sale_lot_id") or "")
        lot["terminal_closure_ids"] = [
            str(closure.get("closure_id") or "")
            for closure in terminal_closures
            if isinstance(closure, dict)
            and str(closure.get("sale_lot_id") or "") == lot_id
        ]

    # A newly booked recovery fill may replace a formerly active source.
    missing_fill_allocations: list[dict[str, Any]] = []
    for remediation_row in remediation_rows:
        key = _row_key(remediation_row)
        mapping = mapping_by_key.get(key)
        if mapping is None:
            raise ValueError(f"remediation identity is absent from the canonical map: {key}")
        lineage_id = str(mapping["effective_lineage_id"])
        for allocation in remediation_row.get("qualifying_fill_allocations", []):
            if not isinstance(allocation, dict):
                continue
            allocation_id = str(allocation.get("allocation_id") or "")
            if not allocation_id or allocation_id in existing_fill_ids:
                continue
            converted = {
                "allocation_id": allocation_id,
                "effective_lineage_id": lineage_id,
                "buy_transaction_id": str(allocation.get("buy_transaction_id") or ""),
                "sale_lot_id": str(allocation.get("sale_lot_id") or ""),
                "quantity_exact": _exact_text(allocation.get("quantity")),
                "allocation_type": str(allocation.get("allocation_method") or "QUALIFYING_FILLED_RECOVERY"),
            }
            fill_allocations.append(converted)
            missing_fill_allocations.append(
                {
                    **converted,
                    "tenant_session_id": key[0],
                    "account_id": key[1],
                    "orderbook_id": key[2],
                    "buy_timestamp": allocation.get("buy_timestamp"),
                    "source_quantity_exact": _exact_text(
                        allocation.get("source_quantity", allocation.get("raw_source_quantity"))
                    ),
                }
            )
            existing_fill_ids.add(allocation_id)

    new_allocations_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for allocation in missing_fill_allocations:
        new_allocations_by_source[allocation["buy_transaction_id"]].append(allocation)
    for source_id, allocations in new_allocations_by_source.items():
        allocated = sum((_exact_decimal(row["quantity_exact"]) for row in allocations), Decimal("0"))
        source_quantity = _exact_decimal(allocations[0]["source_quantity_exact"])
        if source_id in buy_source_by_id:
            source = buy_source_by_id[source_id]
            source["allocated_recovery_quantity_exact"] = _exact_text(
                _exact_decimal(source.get("allocated_recovery_quantity_exact", 0)) + allocated
            )
            continue
        first = allocations[0]
        buy_source = {
            "buy_transaction_id": source_id,
            "tenant_session_id": first["tenant_session_id"],
            "account_id": first["account_id"],
            "effective_lineage_id": first["effective_lineage_id"],
            "buy_timestamp": first.get("buy_timestamp"),
            "source_quantity_exact": _exact_text(source_quantity),
            "allocated_recovery_quantity_exact": _exact_text(allocated),
            "non_recovery_quantity_exact": _exact_text(source_quantity - allocated),
            "unattributed_quantity_exact": "0",
            "classifications": ["QUALIFYING_FILLED_RECOVERY"],
        }
        buy_sources.append(buy_source)
        buy_source_by_id[source_id] = buy_source

    active_allocations: list[dict[str, Any]] = []
    active_source_groups: dict[str, dict[str, Any]] = {}
    for remediation_row in remediation_rows:
        key = _row_key(remediation_row)
        lineage_id = str(mapping_by_key[key]["effective_lineage_id"])
        for allocation in remediation_row.get("active_recovery_allocations", []):
            if not isinstance(allocation, dict):
                continue
            source_id = str(allocation.get("stop_loss_id") or "")
            converted = {
                "allocation_id": str(allocation.get("allocation_id") or ""),
                "active_recovery_source_id": source_id,
                "effective_lineage_id": lineage_id,
                "sale_lot_id": str(allocation.get("sale_lot_id") or ""),
                "quantity_exact": _exact_text(allocation.get("quantity")),
                "allocation_type": str(allocation.get("allocation_method") or "EXACT_STOP_TO_LOT"),
                "decision_id": allocation.get("decision_id"),
                "strategy_intent": allocation.get("strategy_intent"),
            }
            active_allocations.append(converted)
            source = active_source_groups.setdefault(
                source_id,
                {
                    "active_recovery_source_id": source_id,
                    "tenant_session_id": key[0],
                    "account_id": key[1],
                    "orderbook_id": key[2],
                    "effective_lineage_id": lineage_id,
                    "source_quantity_exact": _exact_text(allocation.get("source_quantity")),
                    "allocated_recovery_quantity_exact": "0",
                    "unattributed_quantity_exact": "0",
                    "classification": "ATTRIBUTED_ACTIVE_RECOVERY",
                },
            )
            source["allocated_recovery_quantity_exact"] = _exact_text(
                _exact_decimal(source["allocated_recovery_quantity_exact"])
                + _exact_decimal(converted["quantity_exact"])
            )

    posted_by_transaction = {
        str(row.get("transaction_id") or ""): row
        for row in posted_sales.get("posted_sale_rows", [])
        if isinstance(row, dict)
    }
    delta_lots = readiness.get("sep2_eth_posted_lots")
    if not isinstance(delta_lots, list) or not delta_lots:
        raise ValueError("R390 readiness does not contain posted lot allocations")
    existing_transaction_ids = {
        str(row.get("sale_transaction_id") or "") for row in lots if isinstance(row, dict)
    }
    for delta in delta_lots:
        if not isinstance(delta, dict):
            raise ValueError("R390 readiness contains a non-object posted lot")
        transaction_id = str(delta.get("transaction_id") or "")
        posted = posted_by_transaction.get(transaction_id)
        if posted is None:
            raise ValueError(f"posted transaction is absent from settlement evidence: {transaction_id}")
        if transaction_id in existing_transaction_ids:
            raise ValueError(f"posted transaction is already present in the canonical base: {transaction_id}")
        key = (
            str(delta.get("tenant") or ""),
            str(delta.get("account_id") or ""),
            str(delta.get("orderbook_id") or ""),
        )
        mapping = mapping_by_key.get(key)
        if mapping is None:
            raise ValueError(f"posted transaction identity is absent from canonical map: {key}")
        lineage_id = str(mapping["effective_lineage_id"])
        lineage = lineage_by_id[lineage_id]
        lot_id = str(delta.get("canonical_lot_id") or "")
        sold_quantity = _exact_decimal(delta.get("sale_antal"))
        active_quantity = _exact_decimal(delta.get("strict_current_sale_active_recovery_antal"))
        remaining_quantity = _exact_decimal(delta.get("current_sale_residual_antal"))
        if sold_quantity != active_quantity + remaining_quantity:
            raise ValueError(f"posted transaction quantity parity failed: {transaction_id}")
        lot = {
            "sale_lot_id": lot_id,
            "sale_transaction_id": transaction_id,
            "effective_lineage_id": lineage_id,
            "source_identity_id": str(mapping["source_identity_id"]),
            "source_scope": "INNER_R17_POSTED_DELTA",
            "sale_timestamp": str(delta.get("trade_timestamp") or ""),
            "raw_sold_quantity_exact": _exact_text(sold_quantity),
            "normalized_sold_quantity_exact": _exact_text(sold_quantity),
            "qualifying_filled_quantity_exact": "0",
            "active_recovery_quantity_exact": _exact_text(active_quantity),
            "terminal_closure_quantity_exact": "0",
            "remaining_open_quantity_exact": _exact_text(remaining_quantity),
            "quantity_normalization_factor_exact": "1",
            "terminal_closure_ids": [],
            "parity_delta_exact": "0",
            "posted_delta": True,
            "replaces_provisional_identity": delta.get("replaces_provisional_identity"),
        }
        lots.append(lot)
        lot_by_id[lot_id] = lot
        lineage.setdefault("sale_lot_ids", []).append(lot_id)
        existing_transaction_ids.add(transaction_id)

        source_id = str(delta.get("qualifying_current_sale_stop_source_id") or "")
        if source_id in active_source_groups:
            raise ValueError(f"posted active recovery source is duplicated: {source_id}")
        active_source_groups[source_id] = {
            "active_recovery_source_id": source_id,
            "tenant_session_id": key[0],
            "account_id": key[1],
            "orderbook_id": key[2],
            "effective_lineage_id": lineage_id,
            "source_quantity_exact": _exact_text(active_quantity),
            "allocated_recovery_quantity_exact": _exact_text(active_quantity),
            "unattributed_quantity_exact": "0",
            "classification": "ATTRIBUTED_ACTIVE_RECOVERY",
            "evidence_transaction_id": transaction_id,
        }
        active_allocations.append(
            {
                "allocation_id": f"r390-active-{key[0]}-{key[1]}-{transaction_id}",
                "active_recovery_source_id": source_id,
                "effective_lineage_id": lineage_id,
                "sale_lot_id": lot_id,
                "quantity_exact": _exact_text(active_quantity),
                "allocation_type": "EXACT_POSTED_SALE_STOP_TO_LOT_R390",
                "decision_id": "ETH-ALL-GAPS-R352-20260902",
                "strategy_intent": "PARTIAL_PARTICIPATION",
            }
        )

    active_sources = sorted(active_source_groups.values(), key=lambda row: row["active_recovery_source_id"])
    active_allocations.sort(key=lambda row: row["allocation_id"])

    source_identity_ids = [str(row.get("source_identity_id") or "") for row in mappings]
    lineage_ids = [str(row.get("effective_lineage_id") or "") for row in lineages]
    lot_ids = [str(row.get("sale_lot_id") or "") for row in lots]
    sale_transaction_ids = [str(row.get("sale_transaction_id") or "") for row in lots]
    current_boundary = raw_boundary.get("current_boundary", {})
    observed_counts = {
        "source_identity_count": len(source_identity_ids),
        "effective_lineage_count": len(lineage_ids),
        "immutable_sale_lot_count": len(lot_ids),
        "unique_sale_transaction_id_count": len(set(sale_transaction_ids)),
        "duplicate_sale_transaction_id_count": len(sale_transaction_ids)
        - len(set(sale_transaction_ids)),
        "missing_sale_transaction_id_count": sum(not value for value in sale_transaction_ids),
    }
    expected_counts = {
        "source_identity_count": current_boundary.get("source_identity_count"),
        "effective_lineage_count": current_boundary.get(
            "effective_lineage_count_from_unchanged_corporate_action_map"
        ),
        "immutable_sale_lot_count": current_boundary.get("immutable_sale_lot_count"),
        "unique_sale_transaction_id_count": current_boundary.get(
            "unique_sale_transaction_id_count"
        ),
        "duplicate_sale_transaction_id_count": current_boundary.get(
            "duplicate_sale_transaction_id_count"
        ),
        "missing_sale_transaction_id_count": current_boundary.get(
            "missing_sale_transaction_id_count"
        ),
    }
    if observed_counts != expected_counts or current_boundary.get("truncation_risk") is not False:
        raise ValueError(
            f"canonical arrays do not match current raw boundary: observed={observed_counts}, expected={expected_counts}"
        )

    raw_contract = {
        **observed_counts,
        "truncation_risk": False,
        "source_identity_set_sha256": _canonical_json_sha256(sorted(source_identity_ids)),
        "source_identity_to_lineage_map_sha256": _canonical_json_sha256(mappings),
        "effective_lineage_set_sha256": _canonical_json_sha256(sorted(lineage_ids)),
        "effective_lineage_content_sha256": _canonical_json_sha256(lineages),
        "sale_transaction_id_set_sha256": _canonical_json_sha256(
            sorted(sale_transaction_ids)
        ),
        "sale_lot_id_set_sha256": _canonical_json_sha256(sorted(lot_ids)),
        "immutable_sale_lot_content_sha256": _canonical_json_sha256(lots),
        "allocation_content_sha256": _canonical_json_sha256(fill_allocations),
        "active_recovery_source_content_sha256": _canonical_json_sha256(active_sources),
        "active_recovery_allocation_content_sha256": _canonical_json_sha256(
            active_allocations
        ),
        "terminal_closure_content_sha256": _canonical_json_sha256(terminal_closures),
        "live_raw_vs_canonical_sale_transaction_id_set_parity": True,
    }
    open_lots = [
        row for row in lots if _exact_decimal(row.get("remaining_open_quantity_exact", 0)) > 0
    ]
    return {
        "artifact": "PORTFOLIO_FULL_HISTORY_CANONICAL",
        "schema_version": 2,
        "generated_at": generated_at,
        "timezone": "Europe/Stockholm",
        "authority": {
            "trade_authority": False,
            "broker_mutation": False,
            "paper_mutation": False,
            "tracked_repository_mutation": False,
            "authorization_off_both_at_last_readback": True,
        },
        "sources": _source_rows(source_paths),
        "raw_boundary": raw_contract,
        "summary": {
            **observed_counts,
            "buy_source_count": len(buy_sources),
            "qualifying_fill_allocation_count": len(fill_allocations),
            "active_recovery_source_count": len(active_sources),
            "active_recovery_allocation_count": len(active_allocations),
            "terminal_closure_count": len(terminal_closures),
            "open_sale_lot_count": len(open_lots),
            "open_sale_quantity_exact": _exact_text(
                sum(
                    (_exact_decimal(row["remaining_open_quantity_exact"]) for row in open_lots),
                    Decimal("0"),
                )
            ),
            "projected_full_dynamic_row_count": len(projection),
        },
        "source_identity_to_lineage_map": mappings,
        "effective_lineages": lineages,
        "immutable_sale_lots": lots,
        "buy_sources": buy_sources,
        "qualifying_fill_allocations": fill_allocations,
        "active_recovery_sources": active_sources,
        "active_recovery_allocations": active_allocations,
        "terminal_closures": terminal_closures,
        "dynamic_mirror_projection": projection,
        "verification": {
            "raw_boundary_count_parity": True,
            "source_identity_set_complete": True,
            "effective_lineage_set_complete": True,
            "sale_transaction_ids_unique_and_complete": True,
            "posted_delta_inserted_exactly_once": True,
            "active_recovery_sources_fully_attributed": True,
            "older_sale_sources_receive_sep2_credit": False,
            "fixed_checkpoint_counts_used_as_authority": False,
        },
        "governance_result": {
            "full_history_canonical_complete": True,
            "authoritative_dynamic_mirror_complete": False,
            "economic_repair_completion": False,
            "goal_completion_claim": False,
        },
    }


def build_full_dynamic_governance_mirror(
    projection: dict[str, Any],
    canonical: dict[str, Any],
    official_close: dict[str, Any],
    eth_plan: dict[str, Any],
    *,
    generated_at: str,
    source_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Bind every effective lineage and close-path row to one dynamic row."""

    if projection.get("artifact") != "PORTFOLIO_R210_R137_FULL_DYNAMIC_MIRROR_PROJECTION":
        raise ValueError("full dynamic projection artifact is invalid")
    if canonical.get("artifact") != "PORTFOLIO_FULL_HISTORY_CANONICAL":
        raise ValueError("full-history canonical artifact is invalid")
    if official_close.get("artifact_id") != "R376_SEP2_ALL_38_OFFICIAL_CLOSE_REACHABILITY":
        raise ValueError("official-close reachability artifact is invalid")
    if eth_plan.get("artifact") != "PORTFOLIO_R352_ETH_ALL_BUYBACK_GAPS_EXACT_PREFLIGHT":
        raise ValueError("ETH governance plan artifact is invalid")

    rows = copy.deepcopy(projection.get("rows", []))
    if not isinstance(rows, list) or not rows:
        raise ValueError("dynamic projection rows are missing")
    lineage_by_id = {
        str(row.get("effective_lineage_id") or ""): row
        for row in canonical.get("effective_lineages", [])
        if isinstance(row, dict)
    }
    lot_by_id = {
        str(row.get("sale_lot_id") or ""): row
        for row in canonical.get("immutable_sale_lots", [])
        if isinstance(row, dict)
    }
    active_sources_by_lineage: dict[str, list[str]] = defaultdict(list)
    for source in canonical.get("active_recovery_sources", []):
        if isinstance(source, dict):
            active_sources_by_lineage[str(source.get("effective_lineage_id") or "")].append(
                str(source.get("active_recovery_source_id") or "")
            )
    terminal_closures_by_lot: dict[str, list[str]] = defaultdict(list)
    for closure in canonical.get("terminal_closures", []):
        if isinstance(closure, dict):
            terminal_closures_by_lot[str(closure.get("sale_lot_id") or "")].append(
                str(closure.get("closure_id") or "")
            )
    official_rows = [row for row in official_close.get("rows", []) if isinstance(row, dict)]
    official_by_key = {
        (
            str(row.get("tenant_session_id") or ""),
            str(row.get("account_id") or ""),
            str(row.get("orderbook_id") or ""),
        ): row
        for row in official_rows
    }
    if len(official_by_key) != len(official_rows):
        raise ValueError("official-close reachability identities are duplicated")

    exact_cycle_state = eth_plan.get("exact_cycle_state", {})
    mirrored_lineages: list[str] = []
    mirrored_sources: list[str] = []
    mirrored_lots: list[str] = []
    attached_official: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("dynamic projection contains a non-object row")
        lineage_ids = [
            str(value)
            for value in row.get("r137_canonical_lineage_ids", [])
            if str(value)
        ]
        row["r390_dynamic_row_id"] = (
            "lineage::" + "|".join(lineage_ids)
            if lineage_ids
            else "current::"
            + "/".join(
                (
                    str(row.get("tenant_session_id") or ""),
                    str(row.get("account_id") or ""),
                    f"orderbook-{row.get('orderbook_id')}",
                )
            )
        )
        source_ids: list[str] = []
        lot_ids: list[str] = []
        for lineage_id in lineage_ids:
            lineage = lineage_by_id.get(lineage_id)
            if lineage is None:
                raise ValueError(f"dynamic row references an unknown lineage: {lineage_id}")
            source_ids.extend(str(value) for value in lineage.get("source_identity_ids", []))
            lot_ids.extend(str(value) for value in lineage.get("sale_lot_ids", []))
        lots = [lot_by_id[lot_id] for lot_id in lot_ids]
        open_lot_ids = [
            lot_id
            for lot_id in lot_ids
            if _exact_decimal(lot_by_id[lot_id].get("remaining_open_quantity_exact", 0)) > 0
        ]
        totals = {
            "normalized_sold_quantity_exact": _exact_text(
                sum((_exact_decimal(value["normalized_sold_quantity_exact"]) for value in lots), Decimal("0"))
            ),
            "qualifying_filled_quantity_exact": _exact_text(
                sum((_exact_decimal(value["qualifying_filled_quantity_exact"]) for value in lots), Decimal("0"))
            ),
            "active_recovery_quantity_exact": _exact_text(
                sum((_exact_decimal(value["active_recovery_quantity_exact"]) for value in lots), Decimal("0"))
            ),
            "terminal_closure_quantity_exact": _exact_text(
                sum((_exact_decimal(value["terminal_closure_quantity_exact"]) for value in lots), Decimal("0"))
            ),
            "remaining_open_quantity_exact": _exact_text(
                sum((_exact_decimal(value["remaining_open_quantity_exact"]) for value in lots), Decimal("0"))
            ),
        }
        row["r390_full_history"] = {
            "effective_lineage_ids": lineage_ids,
            "source_identity_ids": source_ids,
            "sale_lot_ids": lot_ids,
            "open_sale_lot_ids": open_lot_ids,
            "active_recovery_source_ids": sorted(
                source_id
                for lineage_id in lineage_ids
                for source_id in active_sources_by_lineage.get(lineage_id, [])
            ),
            "terminal_closure_ids": [
                closure_id
                for lot_id in lot_ids
                for closure_id in terminal_closures_by_lot.get(lot_id, [])
            ],
            **totals,
        }
        mirrored_lineages.extend(lineage_ids)
        mirrored_sources.extend(source_ids)
        mirrored_lots.extend(lot_ids)

        key = (
            str(row.get("tenant_session_id") or ""),
            str(row.get("account_id") or ""),
            str(row.get("orderbook_id") or ""),
        )
        close_row = official_by_key.get(key)
        if close_row is not None:
            row["r390_official_close_reachability"] = copy.deepcopy(close_row)
            attached_official.append(close_row)
            if close_row.get("state") in {
                "CURRENT_STAGE_REACHED_UNSERVED_REPAIR",
                "HISTORICAL_STAGE_REACHED_REBOUNDED_REPAIR",
            }:
                row["buyback_coverage_state"] = "REPAIR_REQUIRED"
                row["low_exposure_decision"] = "REPAIR_REQUIRED"
                row["r390_missed_crossing_preserved"] = True

        if str(row.get("orderbook_id") or "") == "791709" and key[0] in exact_cycle_state:
            cycle = exact_cycle_state[key[0]]
            posted_lots = [lot_id for lot_id in open_lot_ids if lot_by_id[lot_id].get("posted_delta") is True]
            older_lots = [lot_id for lot_id in open_lot_ids if lot_id not in posted_lots]
            components = [
                {
                    "component_id": f"{key[0]}-{key[1]}-791709-pre-sep2-residual",
                    "state": "LADDER_DORMANT",
                    "economic_state": "BUILD_REVIEW",
                    "exact_open_sale_lot_ids": older_lots,
                    "target_rebuild_quantity": int(cycle["older_open_quantity"]),
                    "stages_percent_below_sold_marker": list(
                        cycle["older_dormant_stages_percent"]
                    ),
                    "stage_quantities": list(cycle["older_dormant_quantities"]),
                    "promotion_evidence": "Fresh exact named-instrument quote, reversal, volatility, factor, capacity, churn, spread and full-friction preflight.",
                },
                {
                    "component_id": f"{key[0]}-{key[1]}-791709-sep2-residual",
                    "state": "LADDER_DORMANT",
                    "economic_state": "BUILD_REVIEW",
                    "exact_open_sale_lot_ids": posted_lots,
                    "target_rebuild_quantity": int(cycle["sep2_residual_dormant_quantity"]),
                    "stages_percent_below_sold_marker": list(
                        cycle["sep2_residual_dormant_stages_percent"]
                    ),
                    "stage_quantities": list(cycle["sep2_residual_dormant_quantities"]),
                    "promotion_evidence": "Fresh exact named-instrument quote, reversal, volatility, factor, capacity, churn, spread and full-friction preflight.",
                },
            ]
            component_target = sum(value["target_rebuild_quantity"] for value in components)
            if _exact_decimal(totals["remaining_open_quantity_exact"]) != component_target:
                raise ValueError(f"ETH component residual parity failed for {key}")
            for component in components:
                if sum(component["stage_quantities"]) != component["target_rebuild_quantity"]:
                    raise ValueError(f"ETH component stage parity failed for {component['component_id']}")
            row["latest_recent_sale_date"] = "2026-09-02"
            row["sale_lot_ids"] = lot_ids
            row["open_sale_lot_ids"] = open_lot_ids
            row["sale_attributed_active_buy_quantity"] = int(
                _exact_decimal(totals["active_recovery_quantity_exact"])
            )
            row["target_rebuild_quantity"] = component_target
            row["stages_percent_below_sold_marker"] = "SEE_R390_RECOVERY_COMPONENTS"
            row["stage_quantities"] = None
            row["buyback_coverage_state"] = "LADDER_DORMANT"
            row["coverage_reason"] = (
                "ETH complete account history is split into exact stock-specific recovery components. "
                "Every remaining unit is governed by an individually recorded dormant percentage ladder, "
                "while active recovery sources retain exact sale-lot attribution and older sources receive no September 2 credit."
            )
            row["exact_next_gate"] = (
                "Revalidate named ETH catalyst, support or reversal, volatility, factor capacity, duplicate state, "
                "hard churn, spread and full friction before promoting any dormant component."
            )
            row["r390_recovery_components"] = components

    canonical_lineage_ids = [
        str(row.get("effective_lineage_id") or "")
        for row in canonical.get("effective_lineages", [])
        if isinstance(row, dict)
    ]
    canonical_source_ids = [
        str(row.get("source_identity_id") or "")
        for row in canonical.get("source_identity_to_lineage_map", [])
        if isinstance(row, dict)
    ]
    canonical_lot_ids = [
        str(row.get("sale_lot_id") or "")
        for row in canonical.get("immutable_sale_lots", [])
        if isinstance(row, dict)
    ]
    if Counter(mirrored_lineages) != Counter(canonical_lineage_ids):
        raise ValueError("dynamic mirror does not contain every canonical lineage exactly once")
    if Counter(mirrored_sources) != Counter(canonical_source_ids):
        raise ValueError("dynamic mirror does not contain every source identity exactly once")
    if Counter(mirrored_lots) != Counter(canonical_lot_ids):
        raise ValueError("dynamic mirror does not contain every sale lot exactly once")
    if Counter(_canonical_json_sha256(row) for row in attached_official) != Counter(
        _canonical_json_sha256(row) for row in official_rows
    ):
        raise ValueError("dynamic mirror does not attach every official-close row exactly once")

    close_states = Counter(str(row.get("state") or "") for row in official_rows)
    return {
        "artifact": "PORTFOLIO_FULL_DYNAMIC_GOVERNANCE_MIRROR",
        "schema_version": 2,
        "generated_at": generated_at,
        "timezone": "Europe/Stockholm",
        "status": "ACTIVE_REPAIR_REQUIRED",
        "authority": {
            "trade_authority": False,
            "broker_mutation": False,
            "paper_mutation": False,
            "tracked_repository_mutation": False,
            "authoritative_dynamic_ledger": True,
        },
        "sources": _source_rows(source_paths),
        "canonical_contract": {
            "artifact": canonical.get("artifact"),
            "generated_at": canonical.get("generated_at"),
            "payload_sha256": _canonical_json_sha256(canonical),
            "source_identity_count": len(canonical_source_ids),
            "effective_lineage_count": len(canonical_lineage_ids),
            "immutable_sale_lot_count": len(canonical_lot_ids),
        },
        "official_close_contract": {
            "artifact_id": official_close.get("artifact_id"),
            "generated_at": official_close.get("generated_at"),
            "payload_sha256": _canonical_json_sha256(official_close),
            "row_count": len(official_rows),
            "row_identity_set_sha256": _canonical_json_sha256(
                sorted(
                    [
                        [
                            str(row.get("tenant_session_id") or ""),
                            str(row.get("account_id") or ""),
                            str(row.get("orderbook_id") or ""),
                        ]
                        for row in official_rows
                    ]
                )
            ),
            "later_rebound_erases_crossing": False,
        },
        "summary": {
            "dynamic_row_count": len(rows),
            "mirrored_effective_lineage_count": len(mirrored_lineages),
            "mirrored_source_identity_count": len(mirrored_sources),
            "mirrored_immutable_sale_lot_count": len(mirrored_lots),
            "official_close_row_count": len(attached_official),
            "current_unserved_crossing_rows": close_states[
                "CURRENT_STAGE_REACHED_UNSERVED_REPAIR"
            ],
            "historical_rebounded_repair_rows": close_states[
                "HISTORICAL_STAGE_REACHED_REBOUNDED_REPAIR"
            ],
            "buyback_coverage_state_counts": dict(
                Counter(str(row.get("buyback_coverage_state") or "") for row in rows)
            ),
        },
        "rows": rows,
        "verification": {
            "every_effective_lineage_mirrored_once": True,
            "every_source_identity_mirrored_once": True,
            "every_sale_lot_mirrored_once": True,
            "every_official_close_row_attached_once": True,
            "missed_crossings_preserved_after_rebound": True,
            "fixed_checkpoint_counts_used_as_authority": False,
        },
        "blockers": list(
            projection.get("governance_result", {}).get("blockers_retained", [])
        ),
        "objective_complete": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--path-evidence", type=Path)
    parser.add_argument("--path-output", type=Path)
    parser.add_argument("--terminal-decisions", type=Path)
    parser.add_argument("--remediation-input", type=Path)
    parser.add_argument("--remediation-output", type=Path)
    parser.add_argument("--build-full-governance", action="store_true")
    parser.add_argument("--full-history-base", type=Path)
    parser.add_argument("--posted-sales", type=Path)
    parser.add_argument("--raw-boundary", type=Path)
    parser.add_argument("--corporate-action-resolution", type=Path)
    parser.add_argument("--readiness", type=Path)
    parser.add_argument("--full-history-output", type=Path)
    parser.add_argument("--dynamic-projection", type=Path)
    parser.add_argument("--official-close", type=Path)
    parser.add_argument("--eth-plan", type=Path)
    parser.add_argument("--full-dynamic-output", type=Path)
    parser.add_argument("--generated-at")
    args = parser.parse_args()

    generated_at = args.generated_at or datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(timespec="seconds")
    if args.build_full_governance:
        required = {
            "--full-history-base": args.full_history_base,
            "--posted-sales": args.posted_sales,
            "--raw-boundary": args.raw_boundary,
            "--corporate-action-resolution": args.corporate_action_resolution,
            "--readiness": args.readiness,
            "--remediation-input": args.remediation_input,
            "--full-history-output": args.full_history_output,
            "--dynamic-projection": args.dynamic_projection,
            "--official-close": args.official_close,
            "--eth-plan": args.eth_plan,
            "--full-dynamic-output": args.full_dynamic_output,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error("--build-full-governance requires " + ", ".join(missing))
        assert args.full_history_base is not None
        assert args.posted_sales is not None
        assert args.raw_boundary is not None
        assert args.corporate_action_resolution is not None
        assert args.readiness is not None
        assert args.remediation_input is not None
        assert args.full_history_output is not None
        assert args.dynamic_projection is not None
        assert args.official_close is not None
        assert args.eth_plan is not None
        assert args.full_dynamic_output is not None
        canonical = build_full_history_canonical(
            _load_json(args.full_history_base),
            _load_json(args.posted_sales),
            _load_json(args.raw_boundary),
            _load_json(args.remediation_input),
            _load_json(args.corporate_action_resolution),
            _load_json(args.readiness),
            generated_at=generated_at,
            source_paths={
                "STRUCTURAL_CANONICAL_BASE": args.full_history_base,
                "POSTED_SALE_DELTA": args.posted_sales,
                "CURRENT_RAW_BOUNDARY": args.raw_boundary,
                "CURRENT_INNER_REMEDIATION": args.remediation_input,
                "CORPORATE_ACTION_RESOLUTION": args.corporate_action_resolution,
                "R390_READINESS": args.readiness,
            },
        )
        args.full_history_output.parent.mkdir(parents=True, exist_ok=True)
        with args.full_history_output.open("w", encoding="utf-8") as handle:
            json.dump(canonical, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
        mirror = build_full_dynamic_governance_mirror(
            _load_json(args.dynamic_projection),
            canonical,
            _load_json(args.official_close),
            _load_json(args.eth_plan),
            generated_at=generated_at,
            source_paths={
                "AUTHORITATIVE_FULL_HISTORY_CANONICAL": args.full_history_output,
                "FULL_DYNAMIC_PROJECTION_BASE": args.dynamic_projection,
                "LATEST_OFFICIAL_CLOSE_REACHABILITY": args.official_close,
                "ETH_COMPONENT_GOVERNANCE": args.eth_plan,
            },
        )
        args.full_dynamic_output.parent.mkdir(parents=True, exist_ok=True)
        with args.full_dynamic_output.open("w", encoding="utf-8") as handle:
            json.dump(mirror, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
        print(
            json.dumps(
                {
                    "full_history_output": str(args.full_history_output),
                    "source_identities": canonical["raw_boundary"]["source_identity_count"],
                    "effective_lineages": canonical["raw_boundary"]["effective_lineage_count"],
                    "immutable_sale_lots": canonical["raw_boundary"]["immutable_sale_lot_count"],
                    "full_dynamic_output": str(args.full_dynamic_output),
                    "dynamic_rows": mirror["summary"]["dynamic_row_count"],
                    "official_close_rows": mirror["summary"]["official_close_row_count"],
                    "broker_mutation": False,
                },
                indent=2,
            )
        )
        return 0

    if args.input is None or args.output is None:
        parser.error("--input and --output are required unless --build-full-governance is used")
    payload = _load_json(args.input)
    registry = _load_json(args.registry)
    path_payload = _load_json(args.path_evidence) if args.path_evidence else None
    terminal_payload = _load_json(args.terminal_decisions) if args.terminal_decisions else None
    if bool(args.remediation_input) != bool(args.remediation_output):
        parser.error("--remediation-input and --remediation-output must be provided together")
    if args.remediation_input and path_payload is None:
        parser.error("--path-evidence is required when writing remediation output")
    if args.terminal_decisions and not (
        args.remediation_input and args.remediation_output and args.path_evidence and args.path_output
    ):
        parser.error(
            "--terminal-decisions requires remediation input/output plus path evidence/output"
        )
    try:
        source_path = str(args.input.resolve().relative_to(ROOT))
    except ValueError:
        source_path = str(args.input)
    path_source_path = None
    if args.path_evidence:
        try:
            path_source_path = str(args.path_evidence.resolve().relative_to(ROOT))
        except ValueError:
            path_source_path = str(args.path_evidence)
    remediation_payload = _load_json(args.remediation_input) if args.remediation_input else None
    remediation_source_path = None
    if args.remediation_input:
        try:
            remediation_source_path = str(args.remediation_input.resolve().relative_to(ROOT))
        except ValueError:
            remediation_source_path = str(args.remediation_input)

    if terminal_payload is not None:
        assert remediation_payload is not None
        assert path_payload is not None
        assert path_source_path is not None
        assert args.path_output is not None
        try:
            decision_source_path = str(args.terminal_decisions.resolve().relative_to(ROOT))
        except ValueError:
            decision_source_path = str(args.terminal_decisions)
        remediation_payload = apply_terminal_decisions_to_remediation(
            remediation_payload,
            terminal_payload,
            generated_at=generated_at,
            decision_source_path=decision_source_path,
        )
        filtered_path = filter_path_evidence_to_open_remediation(
            path_payload,
            remediation_payload,
            generated_at=generated_at,
            source_path=path_source_path,
            decision_source_path=decision_source_path,
        )
        path_payload = filtered_path
        args.path_output.parent.mkdir(parents=True, exist_ok=True)
        with args.path_output.open("w", encoding="utf-8") as handle:
            json.dump(filtered_path, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
        try:
            path_source_path = str(args.path_output.resolve().relative_to(ROOT))
        except ValueError:
            path_source_path = str(args.path_output)
    result = enrich_payload(
        payload,
        registry,
        generated_at=generated_at,
        source_path=source_path,
        path_evidence=path_payload,
        path_source_path=path_source_path,
        remediation_payload=remediation_payload,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    remediation_result = None
    if (
        args.remediation_input
        and args.remediation_output
        and remediation_payload
        and remediation_source_path
        and path_payload
        and path_source_path
    ):
        remediation_result = enrich_remediation_payload(
            remediation_payload,
            path_payload,
            generated_at=generated_at,
            source_path=remediation_source_path,
            path_source_path=path_source_path,
        )
        args.remediation_output.parent.mkdir(parents=True, exist_ok=True)
        with args.remediation_output.open("w", encoding="utf-8") as handle:
            json.dump(remediation_result, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "rows": len(result["rows"]),
                "low_exposure_decision_counts": result["summary"]["low_exposure_decision_counts"],
                "repair_required_missed_path_rows": result["summary"].get(
                    "repair_required_missed_path_rows"
                ),
                "remediation_output": str(args.remediation_output) if remediation_result else None,
                "broker_mutation": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
