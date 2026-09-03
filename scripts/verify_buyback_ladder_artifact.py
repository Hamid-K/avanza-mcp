#!/usr/bin/env python3
"""Validate the local buyback-ladder presentation contract.

This is deliberately read-only. It checks that the rendered table cannot
silently promote broker inventory or stale templates into active ladders.
"""

from __future__ import annotations

import json
import argparse
import hashlib
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


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
FULL_HISTORY_CANONICAL_GLOB = "PORTFOLIO_FULL_HISTORY_CANONICAL_[0-9]*.json"
FULL_DYNAMIC_GOVERNANCE_MIRROR_GLOB = (
    "PORTFOLIO_FULL_DYNAMIC_GOVERNANCE_MIRROR_[0-9]*.json"
)
OFFICIAL_CLOSE_REACHABILITY_GLOB = (
    "PORTFOLIO_R*_OFFICIAL_CLOSE_REACHABILITY_[0-9]*.json"
)
CURRENT_RAW_BOUNDARY_GLOB = "PORTFOLIO_R*_FULL_RAW_BOUNDARY_*.json"

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
SMALL_HOLD_MAX_REGULAR_SESSIONS = 5
SMALL_HOLD_CALENDAR_BASIS = "CONSERVATIVE_WEEKDAY_CEILING"
SMALL_HOLD_REVALIDATION_STATUSES = {"CURRENT", "EXPIRED", "MISSING", "INVALID"}
STOCKHOLM = ZoneInfo("Europe/Stockholm")
PATH_CONTEXT_FIELD = "instrument_specific_path_context"
PATH_RECONCILIATION_MARKER = " Complete authenticated path evidence records a maximum "


def _expected_path_reconciled_reason(context: dict[str, Any], *, named: bool) -> str:
    maximum = context.get("maximum_open_lot_drop_percent")
    maximum_text = (
        f"{float(maximum):.2f}%"
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool)
        else "an authenticated amount"
    )
    result = (
        f"{str(context.get('coverage_reason') or '').strip()}"
        f"{PATH_RECONCILIATION_MARKER}{maximum_text} drop below the applicable sold markers "
        "and an unserved 8% review-alarm crossing. A later rebound does not erase that crossing."
    )
    if named:
        result += " The named-instrument restrictions remain binding."
    return result


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


def _latest_output_path(pattern: str) -> Path | None:
    paths = sorted((ROOT / "output").glob(pattern))
    return paths[-1] if paths else None


def latest_full_history_canonical_path() -> Path | None:
    return _latest_output_path(FULL_HISTORY_CANONICAL_GLOB)


def latest_full_dynamic_governance_mirror_path() -> Path | None:
    return _latest_output_path(FULL_DYNAMIC_GOVERNANCE_MIRROR_GLOB)


def latest_official_close_reachability_path() -> Path | None:
    return _latest_output_path(OFFICIAL_CLOSE_REACHABILITY_GLOB)


def latest_current_raw_boundary_path() -> Path | None:
    return _latest_output_path(CURRENT_RAW_BOUNDARY_GLOB)


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    return hashlib.sha256(encoded).hexdigest()


def _exact_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def validate_full_history_canonical(
    payload: dict[str, Any],
    raw_boundary_payload: dict[str, Any] | None = None,
) -> list[str]:
    """Validate every source, lineage, lot, and recovery source independently."""

    errors: list[str] = []
    _require(
        payload.get("artifact") == "PORTFOLIO_FULL_HISTORY_CANONICAL",
        "full-history canonical artifact id missing",
        errors,
    )
    _require(
        payload.get("schema_version") == 2,
        "full-history canonical schema version must be 2",
        errors,
    )
    authority = payload.get("authority", {})
    _require(authority.get("trade_authority") is False, "full-history trade authority must be false", errors)
    _require(authority.get("broker_mutation") is False, "full-history broker mutation must be false", errors)
    _require(authority.get("paper_mutation") is False, "full-history paper mutation must be false", errors)
    _require(_artifact_time(payload.get("generated_at")) is not None, "full-history generated_at is invalid", errors)

    array_names = (
        "source_identity_to_lineage_map",
        "effective_lineages",
        "immutable_sale_lots",
        "buy_sources",
        "qualifying_fill_allocations",
        "active_recovery_sources",
        "active_recovery_allocations",
        "terminal_closures",
        "dynamic_mirror_projection",
    )
    arrays: dict[str, list[dict[str, Any]]] = {}
    for name in array_names:
        value = payload.get(name)
        _require(isinstance(value, list), f"full-history {name} must be a list", errors)
        arrays[name] = [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []
        if isinstance(value, list):
            _require(
                len(arrays[name]) == len(value),
                f"full-history {name} contains a non-object row",
                errors,
            )

    mappings = arrays["source_identity_to_lineage_map"]
    lineages = arrays["effective_lineages"]
    lots = arrays["immutable_sale_lots"]
    buy_sources = arrays["buy_sources"]
    fill_allocations = arrays["qualifying_fill_allocations"]
    active_sources = arrays["active_recovery_sources"]
    active_allocations = arrays["active_recovery_allocations"]
    terminal_closures = arrays["terminal_closures"]

    source_ids = [str(row.get("source_identity_id") or "") for row in mappings]
    lineage_ids = [str(row.get("effective_lineage_id") or "") for row in lineages]
    lot_ids = [str(row.get("sale_lot_id") or "") for row in lots]
    sale_transaction_ids = [str(row.get("sale_transaction_id") or "") for row in lots]
    _require(all(source_ids), "full-history source identity id is missing", errors)
    _require(len(source_ids) == len(set(source_ids)), "full-history source identity id is duplicated", errors)
    _require(all(lineage_ids), "full-history lineage id is missing", errors)
    _require(len(lineage_ids) == len(set(lineage_ids)), "full-history lineage id is duplicated", errors)
    _require(all(lot_ids), "full-history sale lot id is missing", errors)
    _require(len(lot_ids) == len(set(lot_ids)), "full-history sale lot id is duplicated", errors)
    _require(all(sale_transaction_ids), "full-history sale transaction id is missing", errors)
    _require(
        len(sale_transaction_ids) == len(set(sale_transaction_ids)),
        "full-history sale transaction id is duplicated",
        errors,
    )

    mapping_by_source = {str(row.get("source_identity_id") or ""): row for row in mappings}
    lineage_by_id = {str(row.get("effective_lineage_id") or ""): row for row in lineages}
    lot_by_id = {str(row.get("sale_lot_id") or ""): row for row in lots}
    for source_id, mapping in mapping_by_source.items():
        target = str(mapping.get("effective_lineage_id") or "")
        _require(target in lineage_by_id, f"full-history source {source_id} maps to an unknown lineage", errors)

    for lineage_id, lineage in lineage_by_id.items():
        expected_sources = [
            str(row.get("source_identity_id") or "")
            for row in mappings
            if str(row.get("effective_lineage_id") or "") == lineage_id
        ]
        expected_lots = [
            str(row.get("sale_lot_id") or "")
            for row in lots
            if str(row.get("effective_lineage_id") or "") == lineage_id
        ]
        _require(
            lineage.get("source_identity_ids") == expected_sources,
            f"full-history lineage source order or membership mismatch for {lineage_id}",
            errors,
        )
        _require(
            lineage.get("sale_lot_ids") == expected_lots,
            f"full-history lineage lot order or membership mismatch for {lineage_id}",
            errors,
        )

    fill_by_lot: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    fill_by_source: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    fill_ids: list[str] = []
    for allocation in fill_allocations:
        allocation_id = str(allocation.get("allocation_id") or "")
        source_id = str(allocation.get("buy_transaction_id") or "")
        lot_id = str(allocation.get("sale_lot_id") or "")
        quantity = _exact_decimal(allocation.get("quantity_exact"))
        fill_ids.append(allocation_id)
        _require(bool(allocation_id), "full-history fill allocation id is missing", errors)
        _require(quantity is not None and quantity > 0, f"full-history fill allocation quantity is invalid for {allocation_id}", errors)
        _require(lot_id in lot_by_id, f"full-history fill allocation lot is unknown for {allocation_id}", errors)
        if quantity is not None:
            fill_by_lot[lot_id] += quantity
            fill_by_source[source_id] += quantity
        if lot_id in lot_by_id:
            _require(
                allocation.get("effective_lineage_id") == lot_by_id[lot_id].get("effective_lineage_id"),
                f"full-history fill allocation lineage mismatch for {allocation_id}",
                errors,
            )
    _require(len(fill_ids) == len(set(fill_ids)), "full-history fill allocation id is duplicated", errors)

    buy_source_ids = [str(row.get("buy_transaction_id") or "") for row in buy_sources]
    _require(all(buy_source_ids), "full-history buy source id is missing", errors)
    _require(len(buy_source_ids) == len(set(buy_source_ids)), "full-history buy source id is duplicated", errors)
    buy_source_by_id = {str(row.get("buy_transaction_id") or ""): row for row in buy_sources}
    for source_id, source in buy_source_by_id.items():
        source_quantity = _exact_decimal(source.get("source_quantity_exact"))
        allocated = _exact_decimal(source.get("allocated_recovery_quantity_exact"))
        non_recovery = _exact_decimal(source.get("non_recovery_quantity_exact"))
        unattributed = _exact_decimal(source.get("unattributed_quantity_exact"))
        _require(
            None not in (source_quantity, allocated, non_recovery, unattributed)
            and source_quantity == allocated + non_recovery + unattributed,
            f"full-history buy source quantity parity failed for {source_id}",
            errors,
        )
        _require(
            allocated == fill_by_source[source_id],
            f"full-history buy source allocation parity failed for {source_id}",
            errors,
        )
        _require(
            str(source.get("effective_lineage_id") or "") in lineage_by_id,
            f"full-history buy source lineage is unknown for {source_id}",
            errors,
        )
    for source_id in fill_by_source:
        _require(source_id in buy_source_by_id, f"full-history fill source is missing for {source_id}", errors)

    active_by_lot: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    active_by_source: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    active_allocation_ids: list[str] = []
    for allocation in active_allocations:
        allocation_id = str(allocation.get("allocation_id") or "")
        source_id = str(allocation.get("active_recovery_source_id") or "")
        lot_id = str(allocation.get("sale_lot_id") or "")
        quantity = _exact_decimal(allocation.get("quantity_exact"))
        active_allocation_ids.append(allocation_id)
        _require(bool(allocation_id), "full-history active allocation id is missing", errors)
        _require(quantity is not None and quantity > 0, f"full-history active allocation quantity is invalid for {allocation_id}", errors)
        _require(lot_id in lot_by_id, f"full-history active allocation lot is unknown for {allocation_id}", errors)
        if quantity is not None:
            active_by_lot[lot_id] += quantity
            active_by_source[source_id] += quantity
        if lot_id in lot_by_id:
            _require(
                allocation.get("effective_lineage_id") == lot_by_id[lot_id].get("effective_lineage_id"),
                f"full-history active allocation lineage mismatch for {allocation_id}",
                errors,
            )
    _require(
        len(active_allocation_ids) == len(set(active_allocation_ids)),
        "full-history active allocation id is duplicated",
        errors,
    )
    active_source_ids = [str(row.get("active_recovery_source_id") or "") for row in active_sources]
    _require(all(active_source_ids), "full-history active source id is missing", errors)
    _require(len(active_source_ids) == len(set(active_source_ids)), "full-history active source id is duplicated", errors)
    _require(
        not set(active_source_ids).intersection(buy_source_ids),
        "full-history one source is classified as both filled and active",
        errors,
    )
    active_source_by_id = {
        str(row.get("active_recovery_source_id") or ""): row for row in active_sources
    }
    for source_id, source in active_source_by_id.items():
        source_quantity = _exact_decimal(source.get("source_quantity_exact"))
        allocated = _exact_decimal(source.get("allocated_recovery_quantity_exact"))
        unattributed = _exact_decimal(source.get("unattributed_quantity_exact"))
        _require(
            None not in (source_quantity, allocated, unattributed)
            and source_quantity == allocated + unattributed,
            f"full-history active source quantity parity failed for {source_id}",
            errors,
        )
        _require(
            allocated == active_by_source[source_id],
            f"full-history active source allocation parity failed for {source_id}",
            errors,
        )
        _require(
            str(source.get("effective_lineage_id") or "") in lineage_by_id,
            f"full-history active source lineage is unknown for {source_id}",
            errors,
        )
    for source_id in active_by_source:
        _require(source_id in active_source_by_id, f"full-history active source is missing for {source_id}", errors)

    closure_by_lot: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    closure_ids: list[str] = []
    generated_at = _artifact_time(payload.get("generated_at"))
    for closure in terminal_closures:
        closure_id = str(closure.get("closure_id") or "")
        lot_id = str(closure.get("sale_lot_id") or "")
        quantity = _exact_decimal(
            closure.get(
                "canonical_terminal_closure_antal_exact",
                closure.get(
                    "post_split_equivalent_terminal_closure_antal_exact",
                    closure.get("terminal_closure_antal_exact"),
                ),
            )
        )
        closure_ids.append(closure_id)
        _require(bool(closure_id), "full-history terminal closure id is missing", errors)
        _require(lot_id in lot_by_id, f"full-history terminal closure lot is unknown for {closure_id}", errors)
        _require(quantity is not None and quantity > 0, f"full-history terminal closure quantity is invalid for {closure_id}", errors)
        if quantity is not None:
            closure_by_lot[lot_id] += quantity
        if lot_id in lot_by_id:
            _require(
                closure.get("sale_transaction_id") == lot_by_id[lot_id].get("sale_transaction_id"),
                f"full-history terminal closure transaction mismatch for {closure_id}",
                errors,
            )
        if closure.get("selected_outcome") == "NON_STOP_ELIGIBLE":
            classification_time = _artifact_time(closure.get("classification_time"))
            _require(
                closure.get("verified_non_stop_eligible") is True
                and classification_time is not None
                and bool(str(closure.get("non_stop_basis") or "").strip())
                and isinstance(closure.get("availability_evidence"), dict),
                f"full-history non-stop closure evidence is invalid for {closure_id}",
                errors,
            )
            if generated_at is not None and classification_time is not None:
                try:
                    current = (
                        generated_at >= classification_time
                        and generated_at - classification_time
                        <= NO_REENTRY_MAX_VALIDITY
                    )
                except TypeError:
                    current = False
                _require(
                    current,
                    f"full-history non-stop closure evidence is stale for {closure_id}",
                    errors,
                )
        else:
            decision_time = _artifact_time(closure.get("decision_time"))
            expires_at = _artifact_time(closure.get("expires_at"))
            _require(
                decision_time is not None
                and expires_at is not None
                and expires_at > decision_time
                and expires_at - decision_time <= NO_REENTRY_MAX_VALIDITY,
                f"full-history terminal closure validity is invalid for {closure_id}",
                errors,
            )
            if generated_at is not None and expires_at is not None:
                try:
                    current = expires_at > generated_at
                except TypeError:
                    current = False
                _require(
                    current,
                    f"full-history terminal closure is expired for {closure_id}",
                    errors,
                )
        _require(
            closure.get("contradiction_status") == "NONE",
            f"full-history terminal closure is contradicted for {closure_id}",
            errors,
        )
    _require(len(closure_ids) == len(set(closure_ids)), "full-history terminal closure id is duplicated", errors)

    for lot_id, lot in lot_by_id.items():
        raw = _exact_decimal(lot.get("raw_sold_quantity_exact"))
        factor = _exact_decimal(lot.get("quantity_normalization_factor_exact"))
        normalized = _exact_decimal(lot.get("normalized_sold_quantity_exact"))
        filled = _exact_decimal(lot.get("qualifying_filled_quantity_exact"))
        active = _exact_decimal(lot.get("active_recovery_quantity_exact"))
        terminal = _exact_decimal(lot.get("terminal_closure_quantity_exact"))
        remaining = _exact_decimal(lot.get("remaining_open_quantity_exact"))
        _require(_artifact_time(lot.get("sale_timestamp")) is not None, f"full-history sale timestamp is invalid for {lot_id}", errors)
        _require(
            None not in (raw, factor, normalized, filled, active, terminal, remaining)
            and raw > 0
            and factor > 0
            and normalized == raw * factor,
            f"full-history lot normalization parity failed for {lot_id}",
            errors,
        )
        _require(
            None not in (normalized, filled, active, terminal, remaining)
            and normalized == filled + active + terminal + remaining,
            f"full-history lot quantity parity failed for {lot_id}",
            errors,
        )
        _require(filled == fill_by_lot[lot_id], f"full-history lot fill allocation mismatch for {lot_id}", errors)
        _require(active == active_by_lot[lot_id], f"full-history lot active allocation mismatch for {lot_id}", errors)
        _require(terminal == closure_by_lot[lot_id], f"full-history lot terminal closure mismatch for {lot_id}", errors)
        _require(
            lot.get("terminal_closure_ids")
            == [
                str(row.get("closure_id") or "")
                for row in terminal_closures
                if str(row.get("sale_lot_id") or "") == lot_id
            ],
            f"full-history lot terminal closure membership mismatch for {lot_id}",
            errors,
        )
        source = mapping_by_source.get(str(lot.get("source_identity_id") or ""))
        _require(source is not None, f"full-history lot source identity is unknown for {lot_id}", errors)
        if source is not None:
            _require(
                lot.get("effective_lineage_id") == source.get("effective_lineage_id"),
                f"full-history lot lineage conflicts with source mapping for {lot_id}",
                errors,
            )
        _require(_exact_decimal(lot.get("parity_delta_exact")) == 0, f"full-history lot parity delta is nonzero for {lot_id}", errors)

    boundary = payload.get("raw_boundary", {})
    observed_counts = {
        "source_identity_count": len(source_ids),
        "effective_lineage_count": len(lineage_ids),
        "immutable_sale_lot_count": len(lot_ids),
        "unique_sale_transaction_id_count": len(set(sale_transaction_ids)),
        "duplicate_sale_transaction_id_count": len(sale_transaction_ids) - len(set(sale_transaction_ids)),
        "missing_sale_transaction_id_count": sum(not value for value in sale_transaction_ids),
    }
    for field, expected in observed_counts.items():
        _require(boundary.get(field) == expected, f"full-history raw boundary {field} mismatch", errors)
    _require(boundary.get("truncation_risk") is False, "full-history raw boundary has truncation risk", errors)
    digest_values = {
        "source_identity_set_sha256": sorted(source_ids),
        "source_identity_to_lineage_map_sha256": mappings,
        "effective_lineage_set_sha256": sorted(lineage_ids),
        "effective_lineage_content_sha256": lineages,
        "sale_transaction_id_set_sha256": sorted(sale_transaction_ids),
        "sale_lot_id_set_sha256": sorted(lot_ids),
        "immutable_sale_lot_content_sha256": lots,
        "allocation_content_sha256": fill_allocations,
        "active_recovery_source_content_sha256": active_sources,
        "active_recovery_allocation_content_sha256": active_allocations,
        "terminal_closure_content_sha256": terminal_closures,
    }
    for field, value in digest_values.items():
        _require(
            boundary.get(field) == _canonical_json_sha256(value),
            f"full-history raw boundary {field} mismatch",
            errors,
        )
    _require(
        boundary.get("live_raw_vs_canonical_sale_transaction_id_set_parity") is True,
        "full-history live raw transaction parity is not true",
        errors,
    )

    if raw_boundary_payload is not None:
        _require(
            raw_boundary_payload.get("artifact")
            == "PORTFOLIO_R386_FULL_RAW_BOUNDARY_AFTER_ETH_SETTLEMENT",
            "current raw-boundary artifact id is invalid",
            errors,
        )
        current = raw_boundary_payload.get("current_boundary", {})
        expected_current = {
            "source_identity_count": current.get("source_identity_count"),
            "effective_lineage_count": current.get(
                "effective_lineage_count_from_unchanged_corporate_action_map"
            ),
            "immutable_sale_lot_count": current.get("immutable_sale_lot_count"),
            "unique_sale_transaction_id_count": current.get("unique_sale_transaction_id_count"),
            "duplicate_sale_transaction_id_count": current.get("duplicate_sale_transaction_id_count"),
            "missing_sale_transaction_id_count": current.get("missing_sale_transaction_id_count"),
        }
        _require(observed_counts == expected_current, "full-history canonical does not match current raw boundary", errors)
        _require(current.get("truncation_risk") is False, "current raw boundary has truncation risk", errors)
        new_transactions = raw_boundary_payload.get("parity", {}).get("new_sale_transaction_ids", [])
        for transaction_id in new_transactions if isinstance(new_transactions, list) else []:
            _require(
                sale_transaction_ids.count(str(transaction_id)) == 1,
                f"current raw-boundary transaction is not inserted exactly once: {transaction_id}",
                errors,
            )
        _require(
            raw_boundary_payload.get("parity", {}).get("prior_sale_transaction_id_omissions") == [],
            "current raw boundary reports prior transaction omissions",
            errors,
        )
    return errors


def validate_full_dynamic_governance_mirror(
    mirror: dict[str, Any],
    canonical: dict[str, Any],
    official_close: dict[str, Any],
) -> list[str]:
    """Validate complete dynamic identity parity and close-path preservation."""

    errors: list[str] = []
    _require(
        mirror.get("artifact") == "PORTFOLIO_FULL_DYNAMIC_GOVERNANCE_MIRROR",
        "full dynamic mirror artifact id missing",
        errors,
    )
    _require(mirror.get("schema_version") == 2, "full dynamic mirror schema version must be 2", errors)
    authority = mirror.get("authority", {})
    _require(authority.get("authoritative_dynamic_ledger") is True, "full dynamic mirror is not authoritative", errors)
    _require(authority.get("trade_authority") is False, "full dynamic mirror trade authority must be false", errors)
    _require(authority.get("broker_mutation") is False, "full dynamic mirror broker mutation must be false", errors)
    _require(mirror.get("objective_complete") is False, "full dynamic mirror cannot claim objective completion", errors)

    canonical_errors = validate_full_history_canonical(canonical)
    errors.extend(f"canonical link: {error}" for error in canonical_errors)
    canonical_contract = mirror.get("canonical_contract", {})
    _require(
        canonical_contract.get("payload_sha256") == _canonical_json_sha256(canonical),
        "full dynamic mirror canonical payload hash mismatch",
        errors,
    )
    official_contract = mirror.get("official_close_contract", {})
    _require(
        official_contract.get("payload_sha256") == _canonical_json_sha256(official_close),
        "full dynamic mirror official-close payload hash mismatch",
        errors,
    )
    _require(
        official_contract.get("later_rebound_erases_crossing") is False,
        "full dynamic mirror permits a rebound to erase a crossing",
        errors,
    )

    rows_value = mirror.get("rows")
    _require(isinstance(rows_value, list), "full dynamic mirror rows must be a list", errors)
    rows = [row for row in rows_value if isinstance(row, dict)] if isinstance(rows_value, list) else []
    if isinstance(rows_value, list):
        _require(len(rows) == len(rows_value), "full dynamic mirror contains a non-object row", errors)
    row_keys = [
        (
            str(row.get("tenant_session_id") or ""),
            str(row.get("account_id") or ""),
            str(row.get("orderbook_id") or ""),
        )
        for row in rows
    ]
    row_ids = [str(row.get("r390_dynamic_row_id") or "") for row in rows]
    _require(all(row_ids), "full dynamic mirror row id is missing", errors)
    _require(
        len(row_ids) == len(set(row_ids)),
        "full dynamic mirror row id is duplicated",
        errors,
    )

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
    canonical_lineage_ids = list(lineage_by_id)
    canonical_source_ids = [
        str(row.get("source_identity_id") or "")
        for row in canonical.get("source_identity_to_lineage_map", [])
        if isinstance(row, dict)
    ]
    canonical_lot_ids = list(lot_by_id)
    active_source_by_lineage: dict[str, list[str]] = defaultdict(list)
    for source in canonical.get("active_recovery_sources", []):
        if isinstance(source, dict):
            active_source_by_lineage[
                str(source.get("effective_lineage_id") or "")
            ].append(str(source.get("active_recovery_source_id") or ""))
    terminal_closures_by_lot: dict[str, list[str]] = defaultdict(list)
    for closure in canonical.get("terminal_closures", []):
        if isinstance(closure, dict):
            terminal_closures_by_lot[str(closure.get("sale_lot_id") or "")].append(
                str(closure.get("closure_id") or "")
            )
    mirrored_lineages: list[str] = []
    mirrored_sources: list[str] = []
    mirrored_lots: list[str] = []
    mirrored_active_sources: list[str] = []
    mirrored_terminal_closures: list[str] = []
    attached_official: list[dict[str, Any]] = []
    for key, row in zip(row_keys, rows):
        lineage_link = [
            str(value)
            for value in row.get("r137_canonical_lineage_ids", [])
            if str(value)
        ]
        expected_row_id = (
            "lineage::" + "|".join(lineage_link)
            if lineage_link
            else "current::" + "/".join((key[0], key[1], f"orderbook-{row.get('orderbook_id')}"))
        )
        _require(
            row.get("r390_dynamic_row_id") == expected_row_id,
            f"full dynamic row id does not match its lineage or current identity for {key}",
            errors,
        )
        history = row.get("r390_full_history")
        _require(isinstance(history, dict), f"full dynamic history link missing for {key}", errors)
        if not isinstance(history, dict):
            continue
        lineage_ids = history.get("effective_lineage_ids")
        lineage_ids = lineage_ids if isinstance(lineage_ids, list) else []
        expected_sources: list[str] = []
        expected_lots: list[str] = []
        for lineage_id in lineage_ids:
            lineage = lineage_by_id.get(str(lineage_id))
            _require(lineage is not None, f"full dynamic row references unknown lineage for {key}", errors)
            if lineage is not None:
                expected_sources.extend(str(value) for value in lineage.get("source_identity_ids", []))
                expected_lots.extend(str(value) for value in lineage.get("sale_lot_ids", []))
        expected_open_lots = [
            lot_id
            for lot_id in expected_lots
            if _exact_decimal(lot_by_id[lot_id].get("remaining_open_quantity_exact")) > 0
        ]
        _require(history.get("source_identity_ids") == expected_sources, f"full dynamic source identity mismatch for {key}", errors)
        _require(history.get("sale_lot_ids") == expected_lots, f"full dynamic sale-lot order or membership mismatch for {key}", errors)
        _require(history.get("open_sale_lot_ids") == expected_open_lots, f"full dynamic open-lot membership mismatch for {key}", errors)
        expected_active_sources = sorted(
            source_id
            for lineage_id in lineage_ids
            for source_id in active_source_by_lineage.get(str(lineage_id), [])
        )
        expected_terminal_closures = [
            closure_id
            for lot_id in expected_lots
            for closure_id in terminal_closures_by_lot.get(lot_id, [])
        ]
        _require(
            history.get("active_recovery_source_ids") == expected_active_sources,
            f"full dynamic active-source membership mismatch for {key}",
            errors,
        )
        _require(
            history.get("terminal_closure_ids") == expected_terminal_closures,
            f"full dynamic terminal-closure membership mismatch for {key}",
            errors,
        )
        totals = {
            field: sum(
                (_exact_decimal(lot_by_id[lot_id].get(field)) or Decimal("0") for lot_id in expected_lots),
                Decimal("0"),
            )
            for field in (
                "normalized_sold_quantity_exact",
                "qualifying_filled_quantity_exact",
                "active_recovery_quantity_exact",
                "terminal_closure_quantity_exact",
                "remaining_open_quantity_exact",
            )
        }
        for field, total in totals.items():
            _require(
                _exact_decimal(history.get(field)) == total,
                f"full dynamic {field} mismatch for {key}",
                errors,
            )
        mirrored_lineages.extend(str(value) for value in lineage_ids)
        mirrored_sources.extend(expected_sources)
        mirrored_lots.extend(expected_lots)
        mirrored_active_sources.extend(expected_active_sources)
        mirrored_terminal_closures.extend(expected_terminal_closures)

        close_row = row.get("r390_official_close_reachability")
        if close_row is not None:
            _require(isinstance(close_row, dict), f"full dynamic official-close row is invalid for {key}", errors)
            if isinstance(close_row, dict):
                attached_official.append(close_row)
                if close_row.get("state") in {
                    "CURRENT_STAGE_REACHED_UNSERVED_REPAIR",
                    "HISTORICAL_STAGE_REACHED_REBOUNDED_REPAIR",
                }:
                    _require(
                        row.get("buyback_coverage_state") == "REPAIR_REQUIRED"
                        and row.get("r390_missed_crossing_preserved") is True,
                        f"full dynamic missed crossing was cleared for {key}",
                        errors,
                    )

        components = row.get("r390_recovery_components")
        if components is not None:
            _require(isinstance(components, list) and bool(components), f"full dynamic recovery components are invalid for {key}", errors)
            if isinstance(components, list):
                component_lots: list[str] = []
                component_target = 0
                component_ids: list[str] = []
                for component in components:
                    if not isinstance(component, dict):
                        errors.append(f"full dynamic recovery component is not an object for {key}")
                        continue
                    component_ids.append(str(component.get("component_id") or ""))
                    target = component.get("target_rebuild_quantity")
                    stages = component.get("stages_percent_below_sold_marker")
                    quantities = component.get("stage_quantities")
                    lots_for_component = component.get("exact_open_sale_lot_ids")
                    _require(
                        component.get("state") == "LADDER_DORMANT"
                        and isinstance(target, int)
                        and not isinstance(target, bool)
                        and target > 0
                        and isinstance(stages, list)
                        and 1 <= len(stages) <= 3
                        and all(_is_positive_number(value) for value in stages)
                        and all(float(left) < float(right) for left, right in zip(stages, stages[1:]))
                        and isinstance(quantities, list)
                        and len(quantities) == len(stages)
                        and all(_is_positive_integer(value) for value in quantities)
                        and sum(quantities) == target,
                        f"full dynamic dormant component is not fully quantified for {key}",
                        errors,
                    )
                    if isinstance(target, int) and not isinstance(target, bool):
                        component_target += target
                    if isinstance(lots_for_component, list):
                        component_lots.extend(str(value) for value in lots_for_component)
                _require(all(component_ids) and len(component_ids) == len(set(component_ids)), f"full dynamic recovery component id is missing or duplicated for {key}", errors)
                _require(component_lots == expected_open_lots, f"full dynamic recovery component lot parity failed for {key}", errors)
                _require(
                    Decimal(component_target) == totals["remaining_open_quantity_exact"],
                    f"full dynamic recovery component target parity failed for {key}",
                    errors,
                )

    _require(Counter(mirrored_lineages) == Counter(canonical_lineage_ids), "full dynamic lineage parity mismatch", errors)
    _require(Counter(mirrored_sources) == Counter(canonical_source_ids), "full dynamic source identity parity mismatch", errors)
    _require(Counter(mirrored_lots) == Counter(canonical_lot_ids), "full dynamic sale-lot parity mismatch", errors)
    _require(
        Counter(mirrored_active_sources)
        == Counter(
            str(row.get("active_recovery_source_id") or "")
            for row in canonical.get("active_recovery_sources", [])
            if isinstance(row, dict)
        ),
        "full dynamic active-source parity mismatch",
        errors,
    )
    _require(
        Counter(mirrored_terminal_closures)
        == Counter(
            str(row.get("closure_id") or "")
            for row in canonical.get("terminal_closures", [])
            if isinstance(row, dict)
        ),
        "full dynamic terminal-closure parity mismatch",
        errors,
    )

    official_rows = [row for row in official_close.get("rows", []) if isinstance(row, dict)]
    _require(
        Counter(_canonical_json_sha256(row) for row in attached_official)
        == Counter(_canonical_json_sha256(row) for row in official_rows),
        "full dynamic official-close row parity mismatch",
        errors,
    )
    official_identities = sorted(
        [
            [
                str(row.get("tenant_session_id") or ""),
                str(row.get("account_id") or ""),
                str(row.get("orderbook_id") or ""),
            ]
            for row in official_rows
        ]
    )
    _require(
        official_contract.get("row_identity_set_sha256")
        == _canonical_json_sha256(official_identities),
        "full dynamic official-close identity digest mismatch",
        errors,
    )
    summary = mirror.get("summary", {})
    for field, expected in (
        ("dynamic_row_count", len(rows)),
        ("mirrored_effective_lineage_count", len(mirrored_lineages)),
        ("mirrored_source_identity_count", len(mirrored_sources)),
        ("mirrored_immutable_sale_lot_count", len(mirrored_lots)),
        ("official_close_row_count", len(attached_official)),
    ):
        _require(summary.get(field) == expected, f"full dynamic summary {field} mismatch", errors)
    return errors


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


def _independent_dormant_ladder_decision_gaps(
    decision: Any,
    *,
    tenant_session_id: Any,
    account_id: Any,
    orderbook_id: Any,
    recovery_cycle_id: Any,
    exact_open_sale_lot_ids: Any,
    target_rebuild_quantity: Any,
    stages_percent_below_sold_marker: Any,
    stage_quantities: Any,
    allow_legacy: bool = False,
) -> list[str]:
    """Validate future rebuild intent without clearing a historical path repair."""

    if not isinstance(decision, dict):
        return ["independent dormant ladder decision is not an object"]
    gaps: list[str] = []
    strict_fields = (
        "authority",
        "broker_mutation",
        "trade_authority",
        "exact_open_sale_lot_ids",
    )
    legacy = allow_legacy and all(field not in decision for field in strict_fields)
    if not legacy:
        if (
            decision.get("authority") != "LOCAL_REVIEW_ONLY"
            or decision.get("broker_mutation") is not False
            or decision.get("trade_authority") is not False
        ):
            gaps.append("independent dormant ladder authority is invalid")
    if decision.get("state") != "LADDER_DORMANT" or decision.get("economic_state") != "BUILD_REVIEW":
        gaps.append("independent dormant ladder state is invalid")
    for field, expected in (
        ("tenant_session_id", tenant_session_id),
        ("account_id", account_id),
        ("orderbook_id", orderbook_id),
        ("recovery_cycle_id", recovery_cycle_id),
    ):
        if str(decision.get(field) or "") != str(expected or ""):
            gaps.append(f"independent dormant ladder {field} mismatch")
    if decision.get("target_rebuild_quantity") != target_rebuild_quantity:
        gaps.append("independent dormant ladder target mismatch")
    if decision.get("stages_percent_below_sold_marker") != stages_percent_below_sold_marker:
        gaps.append("independent dormant ladder percentages mismatch")
    if decision.get("stage_quantities") != stage_quantities:
        gaps.append("independent dormant ladder quantities mismatch")
    if not legacy:
        lot_ids = decision.get("exact_open_sale_lot_ids")
        if (
            not isinstance(lot_ids, list)
            or not lot_ids
            or len(lot_ids) != len(set(str(value) for value in lot_ids))
            or any(not str(value or "") for value in lot_ids)
            or lot_ids != exact_open_sale_lot_ids
        ):
            gaps.append("independent dormant ladder exact sale-lot set mismatch")
    for field in (
        "decision_id",
        "calibration_evidence",
        "promotion_evidence",
        "rejection_evidence",
        "next_review",
        "expires_at",
    ):
        if not str(decision.get(field) or "").strip():
            gaps.append(f"independent dormant ladder {field} is missing")
    expires_at = _artifact_time(decision.get("expires_at"))
    if expires_at is None or expires_at.tzinfo is None:
        gaps.append("independent dormant ladder expiry is invalid")
    return gaps


def _independent_dormant_ladder_semantic_gaps(row: dict[str, Any]) -> list[str]:
    """Reject stale gap wording beside a valid independent rebuild decision."""

    decision = row.get("dormant_ladder_decision")
    if not isinstance(decision, dict):
        return ["independent dormant ladder decision is not an object"]
    instrument = str(row.get("instrument") or decision.get("instrument") or "").strip()
    calibration = str(decision.get("calibration_evidence") or "").strip()
    next_review = str(decision.get("next_review") or "").strip()
    expected_reason = f"{instrument}: {calibration}" if instrument and calibration else ""
    path_context = row.get(PATH_CONTEXT_FIELD)
    current_reason = str(row.get("coverage_reason") or "").strip()
    current_gate = str(row.get("exact_next_gate") or "").strip()
    if isinstance(path_context, dict):
        current_reason = str(path_context.get("coverage_reason") or "").strip()
        current_gate = str(path_context.get("exact_next_gate") or "").strip()

    gaps: list[str] = []
    if not expected_reason or current_reason != expected_reason:
        gaps.append("independent dormant ladder coverage reason is stale or contradictory")
    if not next_review or current_gate != next_review:
        gaps.append("independent dormant ladder next gate is stale or contradictory")
    resolution = row.get("economic_resolution")
    if not isinstance(resolution, dict):
        gaps.append("independent dormant ladder economic resolution is missing")
    else:
        if str(resolution.get("reason") or "").strip() != expected_reason:
            gaps.append("independent dormant ladder economic rationale mismatch")
        if str(resolution.get("next_review") or "").strip() != next_review:
            gaps.append("independent dormant ladder economic next review mismatch")
    return gaps


def _is_nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _conservative_weekday_deadline(reviewed_at: datetime) -> date:
    cursor = reviewed_at.astimezone(STOCKHOLM).date()
    sessions = 0
    while sessions < SMALL_HOLD_MAX_REGULAR_SESSIONS:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            sessions += 1
    return cursor


def _validate_instrument_specific_path_context(
    row: dict[str, Any],
    evidence: dict[str, Any],
    key: tuple[str, str, str],
    errors: list[str],
) -> None:
    context = row.get(PATH_CONTEXT_FIELD)
    _require(isinstance(context, dict), f"dynamic instrument-specific path context missing for {key}", errors)
    if not isinstance(context, dict):
        return

    instrument = str(row.get("instrument") or "").strip()
    reason = str(context.get("coverage_reason") or "").strip()
    next_gate = str(context.get("exact_next_gate") or "").strip()
    _require(context.get("instrument") == instrument, f"dynamic path-context instrument mismatch for {key}", errors)
    _require(
        bool(reason) and instrument.casefold() in reason.casefold(),
        f"dynamic complete-path reason is not instrument-specific for {key}",
        errors,
    )
    _require(bool(next_gate), f"dynamic instrument-specific path next gate missing for {key}", errors)
    for field in (
        "remaining_open_quantity",
        "remaining_open_lot_count",
        "maximum_open_lot_drop_percent",
        "current_drop_below_weighted_marker_percent",
    ):
        _require(
            context.get(field) == evidence.get(field),
            f"dynamic instrument-specific path context {field} mismatch for {key}",
            errors,
        )

    top_reason = str(row.get("coverage_reason") or "")
    expected_reason = _expected_path_reconciled_reason(
        context,
        named=evidence.get("named_exception") is True,
    )
    _require(
        top_reason == expected_reason
        and top_reason.count(PATH_RECONCILIATION_MARKER) == 1,
        f"dynamic path reconciliation reason is not canonical for {key}",
        errors,
    )
    _require(
        row.get("exact_next_gate") == next_gate,
        f"dynamic path reconciliation erased its stock-specific next gate for {key}",
        errors,
    )
    resolution = row.get("economic_resolution")
    if isinstance(resolution, dict):
        _require(
            resolution.get(PATH_CONTEXT_FIELD) == context,
            f"dynamic economic resolution lost its stock-specific path context for {key}",
            errors,
        )


def _no_reentry_decision_gaps(
    identity_row: dict[str, Any],
    decision: Any,
    reference_time: datetime | None,
    *,
    require_fully_closed: bool = True,
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
    if require_fully_closed and identity_row.get("remaining_open_quantity") != 0:
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
        ):
            residual_after = original_sold_quantity - recovered_before_decision - sold_quantity
            if require_fully_closed and residual_after != 0:
                gaps.append("terminal decision slice does not reconcile to the original sale lot")
            elif not require_fully_closed:
                if residual_after < 0:
                    gaps.append("terminal decision overcloses the original sale lot")
                if decision.get("remaining_after_decision_quantity") != residual_after:
                    gaps.append("terminal decision remaining quantity does not reconcile")
                if identity_row.get("remaining_open_quantity") != residual_after:
                    gaps.append("terminal decision residual differs from the current sale lot")

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
    active_source_quantities: dict[str, int] = {}
    active_allocated_quantities: dict[str, int] = defaultdict(int)
    for allocation in active_allocations or []:
        if not isinstance(allocation, dict):
            continue
        stop_loss_id = str(allocation.get("stop_loss_id") or "")
        source_quantity = allocation.get("source_quantity")
        if stop_loss_id and _is_positive_integer(source_quantity):
            if stop_loss_id in active_source_quantities:
                _require(
                    active_source_quantities[stop_loss_id] == source_quantity,
                    f"active recovery source quantity changes between allocations for {key}",
                    errors,
                )
            else:
                active_source_quantities[stop_loss_id] = source_quantity
            active_allocated_quantities[stop_loss_id] += int(allocation.get("quantity", 0) or 0)
        if allocation.get("allocation_method") == "REVIEWED_EXACT_STOP_TO_LOT_R19":
            _require(
                bool(str(allocation.get("strategy_intent") or "").strip())
                and bool(str(allocation.get("strategy_reason") or "").strip()),
                f"R19 active recovery allocation lacks strategy intent or reason for {key}",
                errors,
            )
    for stop_loss_id, source_quantity in active_source_quantities.items():
        _require(
            active_allocated_quantities.get(stop_loss_id) == source_quantity,
            f"active recovery stop source is not fully attributed for {key}/{stop_loss_id}",
            errors,
        )

    mixed_resolution = row.get("mixed_lot_resolution")
    if mixed_resolution is not None:
        _require(isinstance(mixed_resolution, dict), f"R19 mixed-lot resolution is not an object for {key}", errors)
        mixed_schema = (
            int(mixed_resolution.get("schema_version", 0) or 0)
            if isinstance(mixed_resolution, dict)
            else 0
        )
        events = row.get("qualifying_fill_reallocation_events", [])
        _require(isinstance(events, list), f"R19 fill reallocation events must be a list for {key}", errors)
        event_ids: set[str] = set()
        for event in events if isinstance(events, list) else []:
            if not isinstance(event, dict):
                errors.append(f"R19 fill reallocation event is not an object for {key}")
                continue
            event_id = str(event.get("reallocation_id") or "")
            source_lot_id = str(event.get("source_sale_lot_id") or "")
            target_lot_id = str(event.get("target_sale_lot_id") or "")
            _require(
                bool(event_id) and event_id not in event_ids,
                f"R19 fill reallocation id is missing or duplicated for {key}",
                errors,
            )
            _require(
                source_lot_id in lot_ids and target_lot_id in lot_ids and source_lot_id != target_lot_id,
                f"R19 fill reallocation references invalid lots for {key}",
                errors,
            )
            _require(
                _is_positive_integer(event.get("quantity")),
                f"R19 fill reallocation quantity is invalid for {key}",
                errors,
            )
            if event.get("target_allocation_id"):
                before = event.get("target_quantity_before")
                after = event.get("target_quantity_after")
                target_allocation_id = str(event.get("target_allocation_id") or "")
                _require(
                    bool(target_allocation_id)
                    and _is_positive_integer(before)
                    and _is_positive_integer(after)
                    and int(after) - int(before) == int(event.get("quantity", 0) or 0),
                    f"R31 fill reallocation target delta is invalid for {key}",
                    errors,
                )
                matching_targets = [
                    allocation
                    for allocation in fill_allocations or []
                    if isinstance(allocation, dict)
                    and allocation.get("allocation_id") == target_allocation_id
                    and allocation.get("buy_transaction_id") == event.get("buy_transaction_id")
                    and allocation.get("sale_lot_id") == target_lot_id
                    and allocation.get("quantity") == after
                    and event_id in (allocation.get("reviewed_reallocation_ids") or [])
                ]
            else:
                matching_targets = [
                    allocation
                    for allocation in fill_allocations or []
                    if isinstance(allocation, dict)
                    and allocation.get("allocation_id") == event_id
                    and allocation.get("buy_transaction_id") == event.get("buy_transaction_id")
                    and allocation.get("sale_lot_id") == target_lot_id
                    and allocation.get("quantity") == event.get("quantity")
                    and allocation.get("allocation_method") == "REVIEWED_EXACT_LOT_REALLOCATION_R19"
                ]
            _require(
                len(matching_targets) == 1,
                f"R19 fill reallocation does not match one exact target allocation for {key}",
                errors,
            )
            event_ids.add(event_id)
        if isinstance(mixed_resolution, dict):
            _require(
                mixed_resolution.get("schema_version") in {2, 3}
                and mixed_resolution.get("broker_mutation") is False,
                f"R19 mixed-lot resolution authority is invalid for {key}",
                errors,
            )
            decision_events = [
                event
                for event in events
                if isinstance(event, dict)
                and event.get("decision_id") == mixed_resolution.get("decision_id")
            ]
            if mixed_schema == 2 and not decision_events:
                decision_events = events if isinstance(events, list) else []
            _require(
                mixed_resolution.get("fill_reallocation_count") == len(decision_events),
                f"R19 fill reallocation count mismatch for {key}",
                errors,
            )
            _require(
                mixed_resolution.get("active_allocation_count")
                == sum(
                    allocation.get("decision_id") == mixed_resolution.get("decision_id")
                    for allocation in active_allocations or []
                    if isinstance(allocation, dict)
                ),
                f"R19 active allocation count mismatch for {key}",
                errors,
            )

    sold_total = 0
    fill_total = 0
    active_total = 0
    closed_total = 0
    remaining_total = 0
    closure_ids: set[str] = set()
    for lot_id, lot in lot_by_id.items():
        sold = lot.get("sold_quantity")
        filled = fill_by_lot.get(lot_id, 0)
        active = active_by_lot.get(lot_id, 0)
        decision = lot.get("no_reentry_decision")
        closure_decisions = lot.get("terminal_closure_decisions", [])
        if not isinstance(closure_decisions, list):
            errors.append(f"sale-lot terminal closure decisions must be a list for {key}/{lot_id}")
            closure_decisions = []
        _require(
            not (isinstance(decision, dict) and closure_decisions),
            f"sale lot mixes legacy and R19 terminal decisions for {key}/{lot_id}",
            errors,
        )
        closed = (
            decision.get("closed_quantity", 0)
            if isinstance(decision, dict)
            else sum(
                int(item.get("closed_quantity", 0) or 0)
                for item in closure_decisions
                if isinstance(item, dict)
            )
        )
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
        if closure_decisions:
            prior_terminal_closed = 0
            prior_decision_at: datetime | None = None
            for closure_decision in closure_decisions:
                if not isinstance(closure_decision, dict):
                    errors.append(f"sale-lot terminal closure is not an object for {key}/{lot_id}")
                    continue
                closure_id = str(closure_decision.get("decision_id") or "")
                _require(
                    bool(closure_id) and closure_id not in closure_ids,
                    f"R19 terminal closure id is missing or duplicated for {key}/{lot_id}",
                    errors,
                )
                closure_ids.add(closure_id)
                closure_quantity = closure_decision.get("closed_quantity")
                recovered_before = filled + active + prior_terminal_closed
                remaining_after = (
                    sold - recovered_before - closure_quantity
                    if _is_positive_integer(sold)
                    and _is_nonnegative_integer(recovered_before)
                    and _is_positive_integer(closure_quantity)
                    else None
                )
                identity = {
                    "tenant_session_id": key[0],
                    "account_id": key[1],
                    "orderbook_id": key[2],
                    "sale_lot_id": lot_id,
                    "sale_transaction_id": lot.get("sale_transaction_id"),
                    "sale_timestamp": lot.get("sale_timestamp"),
                    "sale_date": str(lot.get("sale_timestamp") or "")[:10],
                    "sold_quantity": closure_quantity,
                    "remaining_open_quantity": remaining_after,
                    "original_sold_quantity": sold,
                    "recovered_before_decision_quantity": recovered_before,
                }
                for gap in _no_reentry_decision_gaps(
                    identity,
                    closure_decision,
                    reference_time,
                    require_fully_closed=False,
                ):
                    errors.append(f"R19 sale-lot terminal closure is invalid for {key}/{lot_id}: {gap}")
                decision_at = _artifact_time(closure_decision.get("decision_at"))
                if (
                    prior_decision_at is not None
                    and decision_at is not None
                    and decision_at < prior_decision_at
                ):
                    errors.append(
                        f"R19 sale-lot terminal closures are not chronological for {key}/{lot_id}"
                    )
                if decision_at is not None:
                    prior_decision_at = decision_at
                if _is_positive_integer(closure_quantity):
                    prior_terminal_closed += int(closure_quantity)
            _require(
                prior_terminal_closed == closed,
                f"R19 cumulative terminal closure quantity mismatch for {key}/{lot_id}",
                errors,
            )
            if (
                _is_positive_integer(sold)
                and _is_nonnegative_integer(filled)
                and _is_nonnegative_integer(active)
                and _is_nonnegative_integer(remaining)
            ):
                _require(
                    sold - filled - active - prior_terminal_closed == remaining,
                    f"R19 cumulative terminal closure residual mismatch for {key}/{lot_id}",
                    errors,
                )
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
    if isinstance(mixed_resolution, dict):
        r19_closure_count = sum(
            len(lot.get("terminal_closure_decisions", []))
            for lot in lots
            if isinstance(lot, dict) and isinstance(lot.get("terminal_closure_decisions", []), list)
        )
        _require(
            mixed_resolution.get("terminal_closure_count") == r19_closure_count,
            f"R19 terminal closure count mismatch for {key}",
            errors,
        )
        _require(
            mixed_resolution.get("remaining_open_quantity") == remaining_total,
            f"R19 residual quantity mismatch for {key}",
            errors,
        )
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
    _require(schema_version in {2, 3, 4, 5}, "sold-marker remediation schema version must be 2, 3, 4 or 5", errors)
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
    dormant_ladder_rows = [
        row
        for row in rows
        if row.get("state") == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
    ]
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
            int(summary.get("dormant_stock_specific_review_ladder_rows", 0) or 0)
            == len(dormant_ladder_rows),
            "sold-marker remediation dormant-ladder count mismatch",
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
            dormant_decision = row.get("dormant_ladder_decision")
            if dormant_decision is not None:
                open_lot_ids = [
                    str(lot.get("sale_lot_id") or "")
                    for lot in row.get("sale_lots", [])
                    if isinstance(lot, dict)
                    and int(lot.get("remaining_open_quantity", 0) or 0) > 0
                ]
                for gap in _independent_dormant_ladder_decision_gaps(
                    dormant_decision,
                    tenant_session_id=row.get("tenant_session_id"),
                    account_id=row.get("account_id"),
                    orderbook_id=row.get("orderbook_id"),
                    recovery_cycle_id=row.get("recovery_cycle_id"),
                    exact_open_sale_lot_ids=open_lot_ids,
                    target_rebuild_quantity=row.get("remaining_open_quantity"),
                    stages_percent_below_sold_marker=row.get(
                        "recorded_stage_percentages_below_marker"
                    ),
                    stage_quantities=row.get("recorded_stage_quantities"),
                    allow_legacy=(
                        row.get("state")
                        == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
                    ),
                ):
                    errors.append(
                        f"sold-marker remediation independent dormant ladder is invalid for {key}: {gap}"
                    )
            crossed = evidence.get("crossed_8pct_review_alarm") is True
            named = evidence.get("named_exception") is True
            expected_state = (
                "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
                if crossed and named
                else "REPAIR_REQUIRED_MISSED_PATH"
                if crossed
                else "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
                if row.get("state") == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
                else "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET"
            )
            _require(
                row.get("state") == expected_state,
                f"sold-marker remediation path state is inconsistent for {key}",
                errors,
            )
            if row.get("state") == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED":
                stages = row.get("recorded_stage_percentages_below_marker")
                quantities = row.get("recorded_stage_quantities")
                remaining = int(row.get("remaining_open_quantity", 0) or 0)
                _require(
                    isinstance(stages, list)
                    and 1 <= len(stages) <= 3
                    and all(isinstance(value, (int, float)) and float(value) > 0 for value in stages)
                    and all(float(left) < float(right) for left, right in zip(stages, stages[1:])),
                    f"sold-marker remediation dormant ladder percentages are invalid for {key}",
                    errors,
                )
                _require(
                    isinstance(quantities, list)
                    and isinstance(stages, list)
                    and len(quantities) == len(stages)
                    and all(isinstance(value, int) and value > 0 for value in quantities)
                    and sum(quantities) == remaining,
                    f"sold-marker remediation dormant ladder quantities are invalid for {key}",
                    errors,
                )
                if isinstance(dormant_decision, dict):
                    expected_next_gate = str(dormant_decision.get("next_review") or "").strip()
                    actual_next_gate = str(row.get("next_gate") or "").strip()
                    _require(
                        actual_next_gate == expected_next_gate,
                        f"sold-marker remediation dormant ladder next gate is stale for {key}",
                        errors,
                    )
                    _require(
                        "percentage_not_set" not in actual_next_gate.casefold(),
                        f"sold-marker remediation dormant ladder next gate contradicts quantified coverage for {key}",
                        errors,
                    )
    _require(summary.get("open_material_rows") == len(open_rows), "sold-marker remediation open-row count mismatch", errors)
    _require(
        summary.get("remaining_open_quantity_across_material_rows") == remaining_quantity,
        "sold-marker remediation remaining quantity mismatch",
        errors,
    )
    if int(schema_version or 2) >= 4:
        conclusion = str(payload.get("conclusion") or "")
        expected_conclusion_facts = (
            (
                int(summary.get("exact_account_rows_with_prior_same_account_sales", 0) or 0),
                "governed prior-sale identities",
            ),
            (int(summary.get("modeled_sale_lots", 0) or 0), "raw sale lots"),
            (int(summary.get("qualifying_filled_quantity_total", 0) or 0), "filled recovery shares"),
            (remaining_quantity, "still-open shares"),
        )
        _require(bool(conclusion), "sold-marker remediation conclusion is missing", errors)
        for value, label in expected_conclusion_facts:
            _require(
                f"{value:,} {label}" in conclusion,
                f"sold-marker remediation conclusion contradicts {label}",
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
        open_sale_lot_ids = [
            str(lot.get("sale_lot_id") or "")
            for lot in recovery.get("sale_lots", [])
            if isinstance(lot, dict)
            and int(lot.get("remaining_open_quantity", 0) or 0) > 0
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
            "open_sale_lot_ids": open_sale_lot_ids,
            "recovery_state": recovery.get("state"),
            "recovery_artifact_generated_at": remediation_payload.get("generated_at"),
            "recovery_artifact_verified_at": remediation_payload.get("verified_at"),
            "recovery_schema_version": remediation_payload.get("schema_version"),
            "recovery_no_reentry_decision": recovery.get("no_reentry_decision"),
            "recovery_mixed_lot_resolution": recovery.get("mixed_lot_resolution"),
            "recovery_partial_terminal_decisions": recovery.get(
                "partial_terminal_decisions", []
            ),
            "recovery_dormant_ladder_decision": recovery.get("dormant_ladder_decision"),
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
            "dynamic_open_sale_lot_ids": dynamic.get("open_sale_lot_ids"),
            "dynamic_target_rebuild_quantity": dynamic.get("target_rebuild_quantity"),
            "dynamic_latest_recent_sale_date": dynamic.get("latest_recent_sale_date"),
            "dynamic_stages_percent_below_sold_marker": dynamic.get("stages_percent_below_sold_marker"),
            "dynamic_stage_quantities": dynamic.get("stage_quantities"),
            "dynamic_coverage_reason": dynamic.get("coverage_reason"),
            "dynamic_artifact_generated_at": dynamic_payload.get("generated_at"),
            "dynamic_schema_version": dynamic_payload.get("schema_version"),
            "dynamic_no_reentry_decision": dynamic.get("no_reentry_decision"),
            "dynamic_mixed_lot_resolution": dynamic.get("mixed_lot_resolution"),
            "dynamic_partial_terminal_decisions": dynamic.get(
                "partial_terminal_decisions", []
            ),
            "dynamic_dormant_ladder_decision": dynamic.get("dormant_ladder_decision"),
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


def governed_dormant_ladder_count(
    dynamic_payload: dict[str, Any],
    remediation_payload: dict[str, Any],
) -> int:
    """Count fully governed dormant review ladders, never broker orders."""

    return sum(
        row.get("recovery_state") == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
        and not _dormant_ladder_governance_gaps(row)
        for row in sold_marker_dynamic_reconciliation_rows(dynamic_payload, remediation_payload)
    )


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
        if row.get("dynamic_mixed_lot_resolution") != row.get("recovery_mixed_lot_resolution"):
            reasons.append("dynamic mixed-lot resolution does not match the recovery cycle")
        if row.get("dynamic_partial_terminal_decisions") != row.get("recovery_partial_terminal_decisions"):
            reasons.append("dynamic partial terminal decisions do not match the recovery cycle")
        if row.get("dynamic_dormant_ladder_decision") != row.get(
            "recovery_dormant_ladder_decision"
        ):
            reasons.append("dynamic dormant ladder decision does not match the recovery cycle")
        if (
            row.get("dynamic_low_exposure_decision") == "EXIT_OR_NO_REENTRY_REVIEW"
            and _is_positive_number(dynamic_attributed)
        ):
            reasons.append("exit/no-reentry classification is contradicted by active recovery inventory")

        if state.startswith("REPAIR_REQUIRED"):
            if row.get("recovery_dormant_ladder_decision") is not None:
                reasons.extend(
                    _independent_dormant_ladder_decision_gaps(
                        row.get("recovery_dormant_ladder_decision"),
                        tenant_session_id=row.get("tenant_session_id"),
                        account_id=row.get("account_id"),
                        orderbook_id=row.get("orderbook_id"),
                        recovery_cycle_id=row.get("recovery_cycle_id"),
                        exact_open_sale_lot_ids=row.get("open_sale_lot_ids"),
                        target_rebuild_quantity=row.get("remaining_open_quantity"),
                        stages_percent_below_sold_marker=row.get(
                            "recovery_recorded_stage_percentages_below_marker"
                        ),
                        stage_quantities=row.get("recovery_recorded_stage_quantities"),
                    )
                )
                if row.get("dynamic_low_exposure_decision") != "BUILD_REVIEW":
                    reasons.append("independent dormant ladder is not retained as BUILD_REVIEW")
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
        if int(row.get("dynamic_schema_version", 0) or 0) >= 9:
            _require(
                row.get("dynamic_open_sale_lot_ids") == row.get("open_sale_lot_ids"),
                f"dynamic open-sale-lot set mismatch for sold-marker recovery {key}",
                errors,
            )
        _require(
            row.get("dynamic_mixed_lot_resolution") == row.get("recovery_mixed_lot_resolution")
            and row.get("dynamic_partial_terminal_decisions")
            == row.get("recovery_partial_terminal_decisions"),
            f"dynamic R19 mixed-lot decision mismatch for sold-marker recovery {key}",
            errors,
        )
        _require(
            row.get("dynamic_dormant_ladder_decision")
            == row.get("recovery_dormant_ladder_decision"),
            f"dynamic dormant ladder decision mismatch for sold-marker recovery {key}",
            errors,
        )
        if row.get("dynamic_low_exposure_decision") == "EXIT_OR_NO_REENTRY_REVIEW":
            _require(
                int(row.get("remaining_open_quantity", 0) or 0) == 0,
                f"dynamic EXIT/no-reentry state retains an open sold-slice residual for {key}",
                errors,
            )
        reason = str(row.get("dynamic_coverage_reason") or "").lower()
        if state.startswith("REPAIR_REQUIRED"):
            _require(
                row.get("dynamic_buyback_coverage_state") == "REPAIR_REQUIRED"
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
            if row.get("recovery_dormant_ladder_decision") is not None:
                for gap in _independent_dormant_ladder_decision_gaps(
                    row.get("recovery_dormant_ladder_decision"),
                    tenant_session_id=row.get("tenant_session_id"),
                    account_id=row.get("account_id"),
                    orderbook_id=row.get("orderbook_id"),
                    recovery_cycle_id=row.get("recovery_cycle_id"),
                    exact_open_sale_lot_ids=row.get("open_sale_lot_ids"),
                    target_rebuild_quantity=row.get("remaining_open_quantity"),
                    stages_percent_below_sold_marker=row.get(
                        "recovery_recorded_stage_percentages_below_marker"
                    ),
                    stage_quantities=row.get("recovery_recorded_stage_quantities"),
                ):
                    errors.append(
                        f"repair row independent dormant ladder is invalid for {key}: {gap}"
                    )
                _require(
                    row.get("dynamic_low_exposure_decision") == "BUILD_REVIEW"
                    and row.get("dynamic_stages_percent_below_sold_marker")
                    == row.get("recovery_recorded_stage_percentages_below_marker")
                    and row.get("dynamic_stage_quantities")
                    == row.get("recovery_recorded_stage_quantities"),
                    f"repair row does not preserve its independent quantified build decision for {key}",
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
                row.get("dynamic_buyback_coverage_state") in {"LEDGER_ONLY", "LADDER_GAP", "REPAIR_REQUIRED"}
                and row.get("dynamic_stages_percent_below_sold_marker") == "PERCENTAGE_NOT_SET",
                f"partial sold-slice row must remain explicit fail-closed coverage for {key}",
                errors,
            )
            _require(
                row.get("dynamic_target_rebuild_quantity") == row.get("remaining_open_quantity"),
                f"partial sold-slice target does not preserve exact residual quantity for {key}",
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
    small_hold_revalidation_required_rows = 0
    small_hold_revalidation_status_counts: Counter[str] = Counter()
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
                independent_protection_repair = (
                    row.get("current_protection_classification") == "REPAIR_REQUIRED"
                    and isinstance(resolution, dict)
                    and resolution.get("source")
                    == "CURRENT_REVIEWED_CORE_WITH_INDEPENDENT_PROTECTION_REPAIR"
                )
                _require(
                    row.get("current_protection_classification")
                    in {"CORE_HOLD_EXCEPTION", "MARKER_EXCEPTION"}
                    or independent_protection_repair,
                    f"dynamic intentional hold lacks a reviewed hold protection class for {key}",
                    errors,
                )
            if row.get("low_exposure_decision") == "BUILD_REVIEW":
                independent_ladder_gaps = _independent_dormant_ladder_decision_gaps(
                    row.get("dormant_ladder_decision"),
                    tenant_session_id=row.get("tenant_session_id"),
                    account_id=row.get("account_id"),
                    orderbook_id=row.get("orderbook_id"),
                    recovery_cycle_id=row.get("recovery_cycle_id"),
                    exact_open_sale_lot_ids=row.get("open_sale_lot_ids"),
                    target_rebuild_quantity=row.get("target_rebuild_quantity"),
                    stages_percent_below_sold_marker=row.get(
                        "stages_percent_below_sold_marker"
                    ),
                    stage_quantities=row.get("stage_quantities"),
                    allow_legacy=(
                        row.get("buyback_coverage_state") == "LADDER_DORMANT"
                        and not (
                            isinstance(row.get("full_path_evidence"), dict)
                            and row["full_path_evidence"].get("crossed_8pct_review_alarm")
                            is True
                        )
                    ),
                )
                independently_recorded_repair_ladder = (
                    row.get("buyback_coverage_state") == "REPAIR_REQUIRED"
                    and not independent_ladder_gaps
                    and isinstance(row.get("full_path_evidence"), dict)
                    and row["full_path_evidence"].get("crossed_8pct_review_alarm") is True
                    and row["full_path_evidence"].get("named_exception") is not True
                )
                _require(
                    (
                        row.get("buyback_coverage_state") in {"LADDER_ACTIVE", "LADDER_DORMANT"}
                        or independently_recorded_repair_ladder
                    )
                    and isinstance(row.get("stages_percent_below_sold_marker"), list)
                    and isinstance(row.get("stage_quantities"), list),
                    f"dynamic BUILD_REVIEW lacks a quantified stock-specific ladder for {key}",
                    errors,
                )
                if row.get("dormant_ladder_decision") is not None:
                    for gap in independent_ladder_gaps:
                        errors.append(
                            f"dynamic independent dormant ladder is invalid for {key}: {gap}"
                        )
                    if not independent_ladder_gaps:
                        for gap in _independent_dormant_ladder_semantic_gaps(row):
                            errors.append(
                                f"dynamic independent dormant ladder is invalid for {key}: {gap}"
                            )

            if schema_version >= 9 and isinstance(resolution, dict):
                live_holding = row.get("live_holding")
                market_value = row.get("live_market_value_sek")
                small_exposure = (
                    isinstance(live_holding, (int, float))
                    and not isinstance(live_holding, bool)
                    and live_holding > 0
                    and (
                        live_holding <= 5
                        or (
                            isinstance(market_value, (int, float))
                            and not isinstance(market_value, bool)
                            and market_value < 20_000
                        )
                    )
                )
                hold_revalidation_required = (
                    resolution.get("hold_revalidation_required") is True
                )
                if (
                    small_exposure
                    and row.get("low_exposure_decision")
                    == "INTENTIONAL_MARKER_OR_CORE_HOLD"
                ):
                    _require(
                        hold_revalidation_required,
                        f"dynamic small intentional hold lacks structured revalidation for {key}",
                        errors,
                    )
                if hold_revalidation_required:
                    small_hold_revalidation_required_rows += 1
                    status = str(resolution.get("revalidation_status") or "")
                    small_hold_revalidation_status_counts[status] += 1
                    _require(
                        status in SMALL_HOLD_REVALIDATION_STATUSES,
                        f"dynamic small-hold revalidation status is invalid for {key}",
                        errors,
                    )
                    _require(
                        resolution.get("revalidation_regular_session_limit")
                        == SMALL_HOLD_MAX_REGULAR_SESSIONS,
                        f"dynamic small-hold revalidation session limit is invalid for {key}",
                        errors,
                    )
                    _require(
                        resolution.get("revalidation_calendar_basis")
                        == SMALL_HOLD_CALENDAR_BASIS,
                        f"dynamic small-hold revalidation calendar basis is invalid for {key}",
                        errors,
                    )
                    for field in (
                        "economic_purpose",
                        "why_rebuild_is_currently_inferior",
                        "why_exit_is_currently_inferior",
                    ):
                        _require(
                            len(str(resolution.get(field) or "").strip()) >= 30,
                            f"dynamic small-hold {field} is not specific for {key}",
                            errors,
                        )

                    reviewed_at = _artifact_time(
                        resolution.get("revalidation_reviewed_at")
                    )
                    due_by = _artifact_date(resolution.get("revalidation_due_by"))
                    if status in {"CURRENT", "EXPIRED"}:
                        _require(
                            reviewed_at is not None and reviewed_at.tzinfo is not None,
                            f"dynamic small-hold reviewed timestamp is invalid for {key}",
                            errors,
                        )
                        _require(
                            due_by is not None,
                            f"dynamic small-hold deadline is invalid for {key}",
                            errors,
                        )
                        if reviewed_at is not None and reviewed_at.tzinfo is not None and due_by is not None:
                            _require(
                                due_by == _conservative_weekday_deadline(reviewed_at),
                                f"dynamic small-hold deadline exceeds or contradicts the five-session ceiling for {key}",
                                errors,
                            )
                        if dynamic_reference_time is not None and due_by is not None:
                            reference_date = dynamic_reference_time.astimezone(STOCKHOLM).date()
                            _require(
                                (status == "CURRENT") == (reference_date <= due_by),
                                f"dynamic small-hold revalidation status contradicts its deadline for {key}",
                                errors,
                            )
                    if status == "CURRENT":
                        _require(
                            row.get("low_exposure_decision")
                            == "INTENTIONAL_MARKER_OR_CORE_HOLD",
                            f"current small-hold revalidation is not an intentional hold for {key}",
                            errors,
                        )
                    else:
                        _require(
                            row.get("low_exposure_decision") == "REPAIR_REQUIRED",
                            f"stale small-hold revalidation did not fail closed for {key}",
                            errors,
                        )

        if schema_version >= 7 and isinstance(row.get("mixed_lot_resolution"), dict):
            mixed_resolution = row["mixed_lot_resolution"]
            _require(
                mixed_resolution.get("schema_version") in {2, 3}
                and mixed_resolution.get("broker_mutation") is False,
                f"dynamic R19 mixed-lot authority is invalid for {key}",
                errors,
            )
            target = row.get("target_rebuild_quantity")
            _require(
                target == mixed_resolution.get("remaining_open_quantity")
                if _is_positive_integer(mixed_resolution.get("remaining_open_quantity"))
                else target is None,
                f"dynamic R19 target does not equal the exact residual for {key}",
                errors,
            )
            if _is_positive_integer(mixed_resolution.get("remaining_open_quantity")):
                _require(
                    row.get("low_exposure_decision") != "EXIT_OR_NO_REENTRY_REVIEW",
                    f"dynamic R19 residual is incorrectly terminal for {key}",
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
                        row.get("buyback_coverage_state") == "REPAIR_REQUIRED",
                        f"crossed ordinary sold cycle is not REPAIR_REQUIRED for {key}",
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
                if crossed and schema_version >= 6:
                    _validate_instrument_specific_path_context(row, evidence, key, errors)
                elif schema_version >= 8:
                    _require(
                        PATH_CONTEXT_FIELD not in row,
                        f"noncrossed complete path retains stale instrument-specific path context for {key}",
                        errors,
                    )
                    resolution = row.get("economic_resolution")
                    if isinstance(resolution, dict):
                        _require(
                            PATH_CONTEXT_FIELD not in resolution,
                            f"noncrossed economic resolution retains stale instrument-specific path context for {key}",
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
                row.get("buyback_coverage_state") == "LEDGER_ONLY"
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
    if schema_version >= 9:
        _require(
            summary.get("small_hold_revalidation_required_rows")
            == small_hold_revalidation_required_rows,
            "dynamic small-hold revalidation required-row count mismatch",
            errors,
        )
        _require(
            _normalized_state_summary(
                summary.get("small_hold_revalidation_status_counts"),
                SMALL_HOLD_REVALIDATION_STATUSES,
            )
            == {
                status: small_hold_revalidation_status_counts.get(status, 0)
                for status in SMALL_HOLD_REVALIDATION_STATUSES
            },
            "dynamic small-hold revalidation status counts mismatch",
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
    parser.add_argument("--full-history-canonical", type=Path)
    parser.add_argument("--full-dynamic-mirror", type=Path)
    parser.add_argument("--official-close", type=Path)
    parser.add_argument("--raw-boundary", type=Path)
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
    governed_dormant_ladders = 0
    full_dynamic_rows = 0
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
                governed_dormant_ladders = governed_dormant_ladder_count(
                    dynamic_payload,
                    remediation_payload,
                )
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
        full_history_path = args.full_history_canonical or latest_full_history_canonical_path()
        full_dynamic_path = args.full_dynamic_mirror or latest_full_dynamic_governance_mirror_path()
        official_close_path = args.official_close or latest_official_close_reachability_path()
        raw_boundary_path = args.raw_boundary or latest_current_raw_boundary_path()
        required_full_paths = {
            "full-history canonical": full_history_path,
            "full dynamic governance mirror": full_dynamic_path,
            "official-close reachability": official_close_path,
            "current raw boundary": raw_boundary_path,
        }
        for label, path in required_full_paths.items():
            if path is None or not path.exists():
                errors.append(f"{label} artifact is missing")
        if all(path is not None and path.exists() for path in required_full_paths.values()):
            assert full_history_path is not None
            assert full_dynamic_path is not None
            assert official_close_path is not None
            assert raw_boundary_path is not None
            full_history_payload = json.loads(
                full_history_path.read_text(encoding="utf-8")
            )
            full_dynamic_payload = json.loads(
                full_dynamic_path.read_text(encoding="utf-8")
            )
            official_close_payload = json.loads(
                official_close_path.read_text(encoding="utf-8")
            )
            raw_boundary_payload = json.loads(
                raw_boundary_path.read_text(encoding="utf-8")
            )
            errors.extend(
                validate_full_history_canonical(
                    full_history_payload,
                    raw_boundary_payload,
                )
            )
            errors.extend(
                validate_full_dynamic_governance_mirror(
                    full_dynamic_payload,
                    full_history_payload,
                    official_close_payload,
                )
            )
            full_dynamic_rows = len(full_dynamic_payload.get("rows", []))
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
        f"[buyback] PASS: {len(ladders)} executable broker ladders; "
        f"{governed_dormant_ladders} governed dormant review ladders; "
        f"{dynamic_rows} dynamic live rows; {remediation_rows} sold-marker remediation rows; "
        f"{full_dynamic_rows} full-history dynamic rows; broker inventory remains separately classified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
