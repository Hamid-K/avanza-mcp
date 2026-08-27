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
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / ".avanza_position_strategy.json"
HOLD_PROTECTION_CLASSES = {"CORE_HOLD_EXCEPTION", "MARKER_EXCEPTION"}
NAMED_PATH_STATE = "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
MISSED_PATH_STATE = "MISSED_PATH_REPAIR_REQUIRED"
OPEN_PATH_STATE = "LADDER_GAP_PERCENTAGE_NOT_SET"
TERMINAL_DECISION_ARTIFACT = "PORTFOLIO_R17_TERMINAL_DECISIONS"
TERMINAL_DECISION_MAX_VALIDITY = timedelta(days=14)
PATH_CONTEXT_FIELD = "instrument_specific_path_context"


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


def _instrument_specific_path_context(
    row: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Preserve the stock-specific decision that complete-path evidence refines."""

    instrument = str(row.get("instrument") or "").strip()
    reason = str(row.get("coverage_reason") or "").strip()
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
    if payload.get("schema_version") != 1:
        raise ValueError("R17 terminal-decision schema version must be 1")
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
        closures = row.get("sale_lot_closures")
        if not isinstance(closures, list) or not closures:
            raise ValueError(f"R17 terminal-decision sale-lot closures are missing for {key}")
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
            lot_ids.append(lot_id)
        if len(lot_ids) != len(set(lot_ids)):
            raise ValueError(f"R17 terminal-decision reuses a sale lot for {key}")
        result[key] = row
    return result


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

    for key, decision in decision_rows.items():
        row = remediation_rows.get(key)
        if row is None:
            raise ValueError(f"R17 terminal-decision remediation row is missing for {key}")
        if row.get("recovery_cycle_id") != decision.get("recovery_cycle_id"):
            raise ValueError(f"R17 terminal-decision recovery cycle mismatch for {key}")
        if row.get("instrument") != decision.get("instrument"):
            raise ValueError(f"R17 terminal-decision instrument mismatch for {key}")
        for field in (
            "sale_attributed_active_buy_quantity",
            "pre_sale_active_buy_quantity",
            "unattributed_active_buy_quantity",
        ):
            if int(row.get(field, 0) or 0) != 0:
                raise ValueError(f"R17 terminal decision is contradicted by {field} for {key}")

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
    open_keys = {
        _row_key(row)
        for row in remediation_payload.get("rows", [])
        if isinstance(row, dict) and _is_positive_integer(row.get("remaining_open_quantity"))
    }
    filtered = [row for row in rows if isinstance(row, dict) and _row_key(row) in open_keys]
    if {_row_key(row) for row in filtered} != open_keys:
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


def classify_row(
    row: dict[str, Any],
    registry: dict[str, Any],
    registry_updated_at: str | None,
) -> dict[str, Any]:
    """Return the fail-closed economic classification for one exact row."""

    account_id = str(row.get("account_id") or "")
    orderbook_id = str(row.get("orderbook_id") or "")
    protection = str(row.get("current_protection_classification") or "")
    buyback_state = str(row.get("buyback_coverage_state") or "")
    position = _registry_position(registry, account_id, orderbook_id)

    decision = "REPAIR_REQUIRED"
    source = "R17_FULL_HISTORY_AND_POSITION_STRATEGY_RECONCILIATION"
    reason = "The exact row retains unresolved economic recovery or protection work."
    next_review = str(row.get("exact_next_gate") or "").strip()

    if protection == "NAMED_EXCEPTION":
        decision = "NAMED_EXCEPTION"
        source = "CURRENT_NAMED_EXCEPTION"
        reason = (
            str((position or {}).get("protection_reason") or "").strip()
            or "The current row remains governed by its named-instrument exception."
        )
        next_review = str((position or {}).get("next_gate") or next_review).strip()
    elif protection == "NON_STOP_ELIGIBLE_FUND":
        decision = "NON_STOP_ELIGIBLE"
        source = "CURRENT_NON_STOP_ELIGIBLE_CLASSIFICATION"
        reason = (
            str((position or {}).get("protection_reason") or "").strip()
            or "The instrument is verified as non-stop-eligible."
        )
        next_review = str((position or {}).get("next_gate") or next_review).strip()
    elif (
        buyback_state == "LEDGER_ONLY"
        and protection in HOLD_PROTECTION_CLASSES
        and float(row.get("active_buy_volume", 0) or 0) == 0
        and float(row.get("sale_attributed_active_buy_quantity", 0) or 0) == 0
        and row.get("target_rebuild_quantity") is None
        and _has_reviewable_hold_plan(position, protection)
    ):
        decision = "INTENTIONAL_MARKER_OR_CORE_HOLD"
        source = "CURRENT_REVIEWED_POSITION_STRATEGY"
        reason = str(position.get("protection_reason") or "").strip()
        next_review = str(position.get("next_gate") or "").strip()
    elif protection == "REPAIR_REQUIRED":
        reason = (
            str((position or {}).get("protection_reason") or "").strip()
            or "The current position-protection plan remains REPAIR_REQUIRED."
        )
        next_review = str((position or {}).get("next_gate") or next_review).strip()
    elif buyback_state == "LADDER_GAP":
        reason = (
            "The full-history replay retains open sold quantity and no supported "
            "stock-specific percentage ladder or valid terminal decision closes it."
        )
    elif buyback_state == "LEDGER_ONLY":
        reason = (
            "The sold-cycle ledger is closed, but the current position plan is missing "
            "the evidence required for an intentional hold classification."
        )

    result = dict(row)
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
    }
    return result


def apply_terminal_decisions_to_dynamic_rows(
    rows: list[dict[str, Any]],
    remediation_payload: dict[str, Any],
) -> None:
    """Mirror exact cycle-level terminal decisions into current dynamic coverage."""

    dynamic_rows = {_row_key(row): row for row in rows}
    for remediation in remediation_payload.get("rows", []):
        if not isinstance(remediation, dict) or remediation.get("state") != "EXPLICIT_NO_REENTRY_CURRENT_THESIS":
            continue
        decision = remediation.get("no_reentry_decision")
        if not isinstance(decision, dict):
            raise ValueError(f"R17 terminal remediation decision is missing for {_row_key(remediation)}")
        key = _row_key(remediation)
        row = dynamic_rows.get(key)
        if row is None:
            raise ValueError(f"R17 terminal dynamic row is missing for {key}")
        if int(row.get("active_buy_volume", 0) or 0) != 0:
            raise ValueError(f"R17 terminal dynamic row has contradictory active BUY inventory for {key}")
        if int(row.get("sale_attributed_active_buy_quantity", 0) or 0) != 0:
            raise ValueError(f"R17 terminal dynamic row has contradictory sale-attributed BUY inventory for {key}")
        if row.get("recovery_cycle_id") != remediation.get("recovery_cycle_id"):
            raise ValueError(f"R17 terminal dynamic recovery cycle mismatch for {key}")
        reviewed_holding = decision.get("current_holding")
        if reviewed_holding is not None and int(row.get("live_holding", 0) or 0) != int(
            reviewed_holding
        ):
            raise ValueError(f"R17 terminal dynamic holding differs from the reviewed decision for {key}")

        expiry = str(decision.get("expires_at") or "")
        row["buyback_coverage_state"] = "LEDGER_ONLY"
        row["low_exposure_decision"] = "EXIT_OR_NO_REENTRY_REVIEW"
        row["target_rebuild_quantity"] = None
        row["stages_percent_below_sold_marker"] = "PERCENTAGE_NOT_SET"
        row["stage_quantities"] = None
        row["latest_recent_sale_date"] = remediation.get("sale_date")
        row["no_reentry_decision"] = copy.deepcopy(decision)
        row["coverage_reason"] = (
            "A current structured no-reentry decision closes only the exact unresolved "
            "sale-lot remainder while preserving all prior fills and immutable sale history."
        )
        row["exact_next_gate"] = (
            f"Revalidate the exact no-reentry decision no later than {expiry}; reopen the "
            "remaining sold slice if newer thesis, event, technical or complete-path evidence contradicts it."
        )
        row["economic_resolution"] = {
            **dict(row.get("economic_resolution") or {}),
            "state": "EXIT_OR_NO_REENTRY_REVIEW",
            "source": "R17_STRUCTURED_TERMINAL_DECISION",
            "reason": row["coverage_reason"],
            "next_review": row["exact_next_gate"],
        }
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
        classify_row(row, registry, registry_updated_at)
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

            row["full_path_evidence"] = evidence
            if evidence["crossed_8pct_review_alarm"] and not evidence["named_exception"]:
                context = _instrument_specific_path_context(row, evidence)
                row["buyback_coverage_state"] = "REPAIR_REQUIRED"
                row["low_exposure_decision"] = "REPAIR_REQUIRED"
                row[PATH_CONTEXT_FIELD] = context
                row["coverage_reason"] = _path_reconciled_reason(context, named=False)
                row["exact_next_gate"] = context["exact_next_gate"]
                row["economic_resolution"] = {
                    **dict(row.get("economic_resolution") or {}),
                    "state": "REPAIR_REQUIRED",
                    "source": "R17_COMPLETE_PATH_RECONCILIATION",
                    "reason": row["coverage_reason"],
                    "next_review": row["exact_next_gate"],
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
                    "state": "NAMED_EXCEPTION",
                    "source": "CURRENT_NAMED_EXCEPTION_WITH_R17_PATH_REVIEW",
                    "reason": row["coverage_reason"],
                    "next_review": row["exact_next_gate"],
                    PATH_CONTEXT_FIELD: copy.deepcopy(context),
                }
            else:
                resolution = dict(row.get("economic_resolution") or {})
                resolution["source"] = "R17_COMPLETE_PATH_AND_POSITION_STRATEGY_RECONCILIATION"
                row["economic_resolution"] = resolution

    result["schema_version"] = max(
        int(payload.get("schema_version", 0) or 0),
        6 if path_rows is not None else 4,
    )
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
            "A completed sold-cycle may be an intentional hold only when the exact "
            "current registry plan is reviewed and complete. Open sold quantity, "
            "unsupported percentages and position-protection repairs remain fail-closed."
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--path-evidence", type=Path)
    parser.add_argument("--path-output", type=Path)
    parser.add_argument("--terminal-decisions", type=Path)
    parser.add_argument("--remediation-input", type=Path)
    parser.add_argument("--remediation-output", type=Path)
    parser.add_argument("--generated-at")
    args = parser.parse_args()

    generated_at = args.generated_at or datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(timespec="seconds")
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
