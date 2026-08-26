#!/usr/bin/env python3
"""Attach current economic exposure decisions to an R17 buyback artifact.

The full-history replay answers which sale lots remain unmatched. This script
answers a different question: whether the current account-position is an
intentional hold, a named/non-stop exception, or still requires repair. It is
strictly local and never accesses or mutates broker state.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / ".avanza_position_strategy.json"
HOLD_PROTECTION_CLASSES = {"CORE_HOLD_EXCEPTION", "MARKER_EXCEPTION"}
NAMED_PATH_STATE = "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
MISSED_PATH_STATE = "MISSED_PATH_REPAIR_REQUIRED"
OPEN_PATH_STATE = "LADDER_GAP_PERCENTAGE_NOT_SET"


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


def enrich_payload(
    payload: dict[str, Any],
    registry: dict[str, Any],
    *,
    generated_at: str,
    source_path: str,
    path_evidence: dict[str, Any] | None = None,
    path_source_path: str | None = None,
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
                row["buyback_coverage_state"] = "REPAIR_REQUIRED"
                row["low_exposure_decision"] = "REPAIR_REQUIRED"
                row["coverage_reason"] = (
                    "The complete authenticated path crossed the 8 percent review alarm "
                    "without a qualifying exact-lot fill or still-valid same-lot recovery row. "
                    "A later rebound does not erase the missed crossing."
                )
                row["exact_next_gate"] = (
                    "Reconcile the exact open sale lots against current thesis, catalyst, "
                    "technical reversal, spread, capacity, factors and full friction; do not "
                    "chase a rebound or invent a generic percentage ladder."
                )
                row["economic_resolution"] = {
                    **dict(row.get("economic_resolution") or {}),
                    "state": "REPAIR_REQUIRED",
                    "source": "R17_COMPLETE_PATH_RECONCILIATION",
                    "reason": row["coverage_reason"],
                    "next_review": row["exact_next_gate"],
                }
            elif evidence["crossed_8pct_review_alarm"]:
                row["buyback_coverage_state"] = "LADDER_GAP"
                row["low_exposure_decision"] = "NAMED_EXCEPTION"
                row["coverage_reason"] = (
                    "The complete authenticated path crossed the 8 percent review alarm, "
                    "but this named instrument remains separately governed and cannot be "
                    "promoted into ordinary buyback authority."
                )
                row["exact_next_gate"] = (
                    "Run the exact named-instrument thesis, path, technical, exposure, "
                    "capacity and friction review; any broker mutation still requires fresh "
                    "named authorization."
                )
                row["economic_resolution"] = {
                    **dict(row.get("economic_resolution") or {}),
                    "state": "NAMED_EXCEPTION",
                    "source": "CURRENT_NAMED_EXCEPTION_WITH_R17_PATH_REVIEW",
                    "reason": row["coverage_reason"],
                    "next_review": row["exact_next_gate"],
                }
            else:
                resolution = dict(row.get("economic_resolution") or {})
                resolution["source"] = "R17_COMPLETE_PATH_AND_POSITION_STRATEGY_RECONCILIATION"
                row["economic_resolution"] = resolution

    result["schema_version"] = max(
        int(payload.get("schema_version", 0) or 0),
        5 if path_rows is not None else 4,
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

    repair_rows = [row for row in enriched_rows if str(row.get("state") or "").startswith("REPAIR_REQUIRED")]
    percentage_gap_rows = [
        row for row in enriched_rows if row.get("state") == "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET"
    ]
    named_path_rows = [
        row for row in enriched_rows if row.get("state") == "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
    ]
    summary = dict(payload.get("summary", {}))
    summary["repair_required_missed_path_rows"] = len(repair_rows)
    summary["sold_cycle_repair_required_rows"] = len(repair_rows)
    summary["percentage_not_set_open_rows"] = len(path_rows)
    summary["material_path_open_rows"] = len(percentage_gap_rows)
    summary["named_exception_path_review_rows"] = len(named_path_rows)
    summary["full_path_evidence_rows"] = len(path_rows)
    summary["rows_crossing_8pct_review_alarm"] = len(repair_rows) + len(named_path_rows)
    summary["path_evidence_missing_rows"] = 0
    result["summary"] = summary

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
    parser.add_argument("--remediation-input", type=Path)
    parser.add_argument("--remediation-output", type=Path)
    parser.add_argument("--generated-at")
    args = parser.parse_args()

    generated_at = args.generated_at or datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(timespec="seconds")
    payload = _load_json(args.input)
    registry = _load_json(args.registry)
    path_payload = _load_json(args.path_evidence) if args.path_evidence else None
    if bool(args.remediation_input) != bool(args.remediation_output):
        parser.error("--remediation-input and --remediation-output must be provided together")
    if args.remediation_input and path_payload is None:
        parser.error("--path-evidence is required when writing remediation output")
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
    result = enrich_payload(
        payload,
        registry,
        generated_at=generated_at,
        source_path=source_path,
        path_evidence=path_payload,
        path_source_path=path_source_path,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    remediation_result = None
    if args.remediation_input and args.remediation_output and path_payload and path_source_path:
        remediation_payload = _load_json(args.remediation_input)
        try:
            remediation_source_path = str(args.remediation_input.resolve().relative_to(ROOT))
        except ValueError:
            remediation_source_path = str(args.remediation_input)
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
