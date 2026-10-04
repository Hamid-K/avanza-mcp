#!/usr/bin/env python3
"""Validate the fail-closed twice-daily portfolio governance review streak."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "output" / "PORTFOLIO_GOVERNANCE_REVIEW_STREAK.json"
STOCKHOLM = ZoneInfo("Europe/Stockholm")
EXPECTED_ACCOUNTS = {
    ("personal", "5227886"),
    ("darkcell", "7616265"),
}
REQUIRED_GATES = {
    "session_scope",
    "live_state",
    "position_strategies",
    "protection_classifications",
    "stop_strategies",
    "notable_movers",
    "sold_slices_buyback",
    "recovery_reachability",
    "raw_failed_orders",
    "fills_protection",
    "factors_capacity",
    "churn_friction",
    "scheduler_priority",
    "mutations_reconciled",
    "authorization_off",
    "evidence_fresh",
}
REVIEW_WINDOWS = {"MORNING", "EVENING"}
MARKET_SESSION_STATES = {
    "REGULAR_SESSION",
    "EARLY_CLOSE",
    "HOLIDAY",
    "WEEKEND",
    "UNKNOWN",
}
ELIGIBLE_SESSION_STATES = {"REGULAR_SESSION", "EARLY_CLOSE"}
TIMING_ANNOTATION_CLASSES = {
    "PRESERVED_LATE_FAILED_ATTEMPT",
    "PRESERVED_OUT_OF_WINDOW_HEARTBEAT",
    "PRESERVED_SEP3_MORNING_UNAVAILABLE_ATTEMPT",
}
BROKER_SELL_ASSESSMENT_STATUSES = {"ASSESSED", "NOT_ASSESSED"}
BROKER_SELL_COVERAGE_STATUSES = {
    "BROKER_SELL_PROTECTION_PRESENT",
    "BROKER_SELL_PROTECTION_PARTIAL",
    "BROKER_SELL_PROTECTION_ABSENT",
    "NOT_APPLICABLE",
    "NOT_ASSESSED",
}
BROKER_SELL_REVIEW_STATUSES = {
    "CURRENT",
    "PROTECTION_REVIEW_REQUIRED",
    "NOT_ASSESSED",
}
STRATEGY_TARGET_COVERAGE_STATUSES = {
    "MATCHED",
    "UNDERCOVERED",
    "OVERCOVERED",
    "MISSING",
    "NOT_APPLICABLE",
}
STRATEGY_TARGET_MISMATCH_STATUSES = {
    "UNDERCOVERED",
    "OVERCOVERED",
    "MISSING",
}
NO_STOP_EVIDENCE_STATUSES = {
    "CURRENT",
    "MISSING",
    "INVALID",
    "EXPIRED",
    "NOT_APPLICABLE",
}
NO_STOP_PROTECTION_CHOICES = {
    "TACTICAL_PROFIT_SLICE",
    "WIDER_CALIBRATED_CORE_ROW",
    "DELIBERATELY_UNPROTECTED_CORE",
}
NO_STOP_EVIDENCE_FIELDS = {
    "decision_at",
    "evidence_as_of",
    "next_review_at",
    "valid_until",
    "gap_risk_statement",
    "evidence_source_ids",
    "protection_choice",
}
NON_STOP_ELIGIBLE_EVIDENCE_FIELDS = {
    "evidence_as_of",
    "valid_until",
    "capability_statement",
    "evidence_source_ids",
}


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _aware_datetime(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a valid ISO timestamp string")
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        errors.append(f"{label} must be a valid ISO timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{label} must include a UTC offset")
        return None
    return parsed


def _account_scope(accounts: Any) -> set[tuple[str, str]]:
    if not isinstance(accounts, list):
        return set()
    return {
        (
            str(row.get("tenant_session_id") or ""),
            str(row.get("account_id") or ""),
        )
        for row in accounts
        if isinstance(row, dict)
    }


def _nonnegative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
    )


def _nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _first_present(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _zero_sell_material_position_count(value: Any) -> int | None:
    """Return an explicit zero-SELL material-row count, never an inferred zero."""

    if _nonnegative_integer(value):
        return int(value)
    if isinstance(value, list):
        return len(value)
    return None


def _protection_rows(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    if isinstance(value, dict) and all(
        isinstance(row, dict) for row in value.values()
    ):
        rows: list[dict[str, Any]] = []
        for orderbook_id, source in value.items():
            row = dict(source)
            row.setdefault("orderbook_id", str(orderbook_id))
            rows.append(row)
        return rows
    return None


def _valid_source_ids(value: Any) -> bool:
    return bool(
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item.strip() for item in value)
        and len(value) == len(set(value))
    )


def _validate_current_no_stop_evidence(
    value: Any,
    *,
    choice: str,
    as_of: datetime | None,
    label: str,
    errors: list[str],
) -> None:
    _require(isinstance(value, dict), f"{label} must be an object", errors)
    if not isinstance(value, dict):
        return
    _require(
        set(value) == NO_STOP_EVIDENCE_FIELDS,
        f"{label} must contain the exact closed no-stop evidence fields",
        errors,
    )
    decision_at = _aware_datetime(
        value.get("decision_at"), f"{label}.decision_at", errors
    )
    evidence_as_of = _aware_datetime(
        value.get("evidence_as_of"), f"{label}.evidence_as_of", errors
    )
    next_review_at = _aware_datetime(
        value.get("next_review_at"), f"{label}.next_review_at", errors
    )
    valid_until = _aware_datetime(
        value.get("valid_until"), f"{label}.valid_until", errors
    )
    _require(
        isinstance(value.get("gap_risk_statement"), str)
        and bool(value["gap_risk_statement"].strip()),
        f"{label}.gap_risk_statement must be nonblank",
        errors,
    )
    _require(
        _valid_source_ids(value.get("evidence_source_ids")),
        f"{label}.evidence_source_ids must be nonempty and unique",
        errors,
    )
    _require(
        isinstance(value.get("protection_choice"), str)
        and value["protection_choice"].strip().upper() == choice,
        f"{label}.protection_choice differs from the audit projection",
        errors,
    )
    if evidence_as_of is not None and decision_at is not None:
        _require(
            evidence_as_of <= decision_at,
            f"{label}.evidence_as_of must be at or before decision_at",
            errors,
        )
    if decision_at is not None and next_review_at is not None:
        _require(
            next_review_at > decision_at,
            f"{label}.next_review_at must be after decision_at",
            errors,
        )
    if next_review_at is not None and valid_until is not None:
        _require(
            valid_until >= next_review_at,
            f"{label}.valid_until must be at or after next_review_at",
            errors,
        )
    if as_of is None:
        errors.append(f"{label} cannot be CURRENT without a valid review timestamp")
        return
    for field, timestamp in (
        ("evidence_as_of", evidence_as_of),
        ("decision_at", decision_at),
    ):
        if timestamp is not None:
            _require(
                timestamp <= as_of,
                f"{label}.{field} must not be future-dated",
                errors,
            )
    if next_review_at is not None:
        _require(
            next_review_at > as_of,
            f"{label}.next_review_at has elapsed",
            errors,
        )
    if valid_until is not None:
        _require(
            valid_until > as_of,
            f"{label}.valid_until has elapsed",
            errors,
        )


def _validate_current_non_stop_evidence(
    value: Any,
    *,
    as_of: datetime | None,
    label: str,
    errors: list[str],
) -> None:
    _require(isinstance(value, dict), f"{label} must be an object", errors)
    if not isinstance(value, dict):
        return
    _require(
        set(value) == NON_STOP_ELIGIBLE_EVIDENCE_FIELDS,
        f"{label} must contain the exact closed capability-evidence fields",
        errors,
    )
    evidence_as_of = _aware_datetime(
        value.get("evidence_as_of"), f"{label}.evidence_as_of", errors
    )
    valid_until = _aware_datetime(
        value.get("valid_until"), f"{label}.valid_until", errors
    )
    _require(
        isinstance(value.get("capability_statement"), str)
        and bool(value["capability_statement"].strip()),
        f"{label}.capability_statement must be nonblank",
        errors,
    )
    _require(
        _valid_source_ids(value.get("evidence_source_ids")),
        f"{label}.evidence_source_ids must be nonempty and unique",
        errors,
    )
    if evidence_as_of is not None and valid_until is not None:
        _require(
            valid_until > evidence_as_of,
            f"{label}.valid_until must be after evidence_as_of",
            errors,
        )
    if as_of is None:
        errors.append(f"{label} cannot be CURRENT without a valid review timestamp")
        return
    if evidence_as_of is not None:
        _require(
            evidence_as_of <= as_of,
            f"{label}.evidence_as_of must not be future-dated",
            errors,
        )
    if valid_until is not None:
        _require(
            valid_until > as_of,
            f"{label}.valid_until has elapsed",
            errors,
        )


def _broker_sell_protection_block(account: dict[str, Any]) -> dict[str, Any] | None:
    """Return canonical or compatible broker SELL evidence for one account.

    A missing block is intentionally not synthesized from registry completeness,
    stop-audit success, or zero-valued helper defaults. Those states are
    NOT_ASSESSED for strategic broker SELL protection.
    """

    nested = account.get("broker_sell_protection")
    if isinstance(nested, dict):
        return nested
    compatible_status_fields = {
        "broker_sell_protection_assessment",
        "broker_sell_protection_status",
        "broker_sell_protection_review_status",
    }
    if compatible_status_fields.intersection(account):
        return account
    return None


def _broker_sell_protection_state(account: dict[str, Any]) -> dict[str, Any]:
    """Normalize explicit broker evidence without treating absence as zero."""

    block = _broker_sell_protection_block(account)
    if block is None:
        return {
            "explicit": False,
            "assessment_status": "NOT_ASSESSED",
            "coverage_status": "NOT_ASSESSED",
            "review_status": "NOT_ASSESSED",
            "active_sell_row_count": None,
            "eligible_position_count": None,
            "covered_position_count": None,
            "zero_sell_material_position_count": None,
            "per_instrument": None,
            "strategy_target_contract_current": False,
            "eligible": False,
        }

    assessment_status = str(
        _first_present(
            block,
            "assessment_status",
            "broker_sell_protection_assessment",
        )
        or ""
    ).strip().upper()
    coverage_status = str(
        _first_present(
            block,
            "coverage_status",
            "broker_sell_protection_status",
        )
        or ""
    ).strip().upper()
    review_status = str(
        _first_present(
            block,
            "review_status",
            "broker_sell_protection_review_status",
        )
        or ""
    ).strip().upper()
    active_sell_row_count = _first_present(
        block,
        "active_sell_row_count",
        "active_sell_count",
    )
    eligible_position_count = _first_present(
        block,
        "eligible_position_count",
    )
    covered_position_count = _first_present(
        block,
        "covered_position_count",
        "positions_with_active_sell_count",
    )
    zero_sell_value = _first_present(
        block,
        "zero_sell_material_positions",
        "zero_sell_material_position_count",
    )
    zero_sell_count = _zero_sell_material_position_count(zero_sell_value)
    per_instrument = _protection_rows(block.get("per_instrument"))
    reported_target_mismatch_count = block.get(
        "strategy_target_mismatch_position_count"
    )
    target_statuses = [
        str(row.get("strategy_target_coverage_status") or "").strip().upper()
        for row in per_instrument or []
        if _nonnegative_integer(row.get("active_sell_row_count"))
        and row.get("active_sell_row_count") > 0
    ]
    target_status_unknown_count = sum(
        status not in STRATEGY_TARGET_COVERAGE_STATUSES
        for status in target_statuses
    )
    derived_target_mismatch_count = sum(
        status != "MATCHED" for status in target_statuses
    )
    strategy_target_contract_current = bool(
        _nonnegative_integer(reported_target_mismatch_count)
        and reported_target_mismatch_count == derived_target_mismatch_count
        and derived_target_mismatch_count == 0
        and target_status_unknown_count == 0
    )

    assessed = assessment_status == "ASSESSED"
    zero_sell_absent = bool(
        assessed
        and zero_sell_count is not None
        and zero_sell_count > 0
        and coverage_status == "BROKER_SELL_PROTECTION_ABSENT"
        and review_status == "PROTECTION_REVIEW_REQUIRED"
    )
    eligible = bool(
        assessed
        and zero_sell_count == 0
        and _nonnegative_integer(eligible_position_count)
        and covered_position_count == eligible_position_count
        and strategy_target_contract_current
        and review_status == "CURRENT"
        and coverage_status
        in {
            "BROKER_SELL_PROTECTION_PRESENT",
            "BROKER_SELL_PROTECTION_PARTIAL",
            "NOT_APPLICABLE",
        }
    )
    return {
        "explicit": True,
        "assessment_status": assessment_status,
        "coverage_status": coverage_status,
        "review_status": review_status,
        "active_sell_row_count": active_sell_row_count,
        "eligible_position_count": eligible_position_count,
        "covered_position_count": covered_position_count,
        "zero_sell_material_position_count": zero_sell_count,
        "per_instrument": per_instrument,
        "reported_target_mismatch_count": reported_target_mismatch_count,
        "derived_target_mismatch_count": derived_target_mismatch_count,
        "target_status_unknown_count": target_status_unknown_count,
        "strategy_target_contract_current": strategy_target_contract_current,
        "zero_sell_absent": zero_sell_absent,
        "eligible": eligible,
    }


def _validate_broker_sell_protection(
    account: dict[str, Any],
    *,
    label: str,
    as_of: datetime | None,
    errors: list[str],
) -> dict[str, Any]:
    """Validate explicit evidence while allowing legacy rows to fail closed."""

    state = _broker_sell_protection_state(account)
    if not state["explicit"]:
        return state

    block = _broker_sell_protection_block(account)
    assert block is not None
    assessment_status = state["assessment_status"]
    coverage_status = state["coverage_status"]
    review_status = state["review_status"]
    _require(
        assessment_status in BROKER_SELL_ASSESSMENT_STATUSES,
        f"{label}.broker_sell_protection assessment_status is invalid",
        errors,
    )
    _require(
        coverage_status in BROKER_SELL_COVERAGE_STATUSES,
        f"{label}.broker_sell_protection coverage_status is invalid",
        errors,
    )
    _require(
        review_status in BROKER_SELL_REVIEW_STATUSES,
        f"{label}.broker_sell_protection review_status is invalid",
        errors,
    )

    if assessment_status == "NOT_ASSESSED":
        _require(
            coverage_status == "NOT_ASSESSED"
            and review_status == "NOT_ASSESSED",
            f"{label}.broker_sell_protection NOT_ASSESSED state is inconsistent",
            errors,
        )
        return state

    _require(
        block.get("coverage_semantics")
        == "POSITION_COUNT_AND_PER_ORDERBOOK_ANTAL_ONLY",
        f"{label}.broker_sell_protection coverage_semantics must prohibit cross-instrument Antal coverage",
        errors,
    )
    _require(
        block.get("cross_instrument_antal_aggregated") is False,
        f"{label}.broker_sell_protection cross_instrument_antal_aggregated must be false",
        errors,
    )
    canonical_block = isinstance(account.get("broker_sell_protection"), dict)
    if canonical_block:
        for field in (
            "active_sell_row_count",
            "eligible_position_count",
            "covered_position_count",
            "zero_sell_material_positions",
            "per_instrument",
            "strategy_target_mismatch_position_count",
            "verified_non_stop_eligible_position_count",
            "verified_non_stop_eligible_positions",
        ):
            _require(
                field in block,
                f"{label}.broker_sell_protection canonical field {field} is required",
                errors,
            )

    active_sell_row_count = state["active_sell_row_count"]
    eligible_position_count = state["eligible_position_count"]
    covered_position_count = state["covered_position_count"]
    zero_sell_count = state["zero_sell_material_position_count"]
    per_instrument = state["per_instrument"]
    reported_target_mismatch_count = state[
        "reported_target_mismatch_count"
    ]
    _require(
        _nonnegative_integer(active_sell_row_count),
        f"{label}.broker_sell_protection active_sell_row_count must be a non-negative integer",
        errors,
    )
    for field, value in (
        ("eligible_position_count", eligible_position_count),
        ("covered_position_count", covered_position_count),
    ):
        _require(
            _nonnegative_integer(value),
            f"{label}.broker_sell_protection {field} must be a non-negative integer",
            errors,
        )
    _require(
        zero_sell_count is not None,
        f"{label}.broker_sell_protection zero_sell_material_positions must be a list or non-negative count",
        errors,
    )

    _require(
        per_instrument is not None,
        f"{label}.broker_sell_protection per_instrument evidence must be a list or map",
        errors,
    )
    _require(
        _nonnegative_integer(reported_target_mismatch_count),
        f"{label}.broker_sell_protection strategy_target_mismatch_position_count must be a non-negative integer",
        errors,
    )
    verified_non_stop_count = block.get(
        "verified_non_stop_eligible_position_count"
    )
    verified_non_stop_positions = block.get(
        "verified_non_stop_eligible_positions"
    )
    _require(
        _nonnegative_integer(verified_non_stop_count),
        f"{label}.broker_sell_protection verified_non_stop_eligible_position_count must be a non-negative integer",
        errors,
    )
    _require(
        isinstance(verified_non_stop_positions, list),
        f"{label}.broker_sell_protection verified_non_stop_eligible_positions must be a list",
        errors,
    )
    if _nonnegative_integer(verified_non_stop_count) and isinstance(
        verified_non_stop_positions, list
    ):
        _require(
            len(verified_non_stop_positions) == verified_non_stop_count,
            f"{label}.broker_sell_protection verified non-stop count differs from evidence rows",
            errors,
        )
        for evidence_index, evidence_row in enumerate(
            verified_non_stop_positions
        ):
            evidence_label = (
                f"{label}.broker_sell_protection."
                f"verified_non_stop_eligible_positions[{evidence_index}]"
            )
            if not isinstance(evidence_row, dict):
                errors.append(f"{evidence_label} must be an object")
                continue
            evidence = evidence_row.get("non_stop_eligible_evidence")
            source_ids = (
                evidence.get("evidence_source_ids")
                if isinstance(evidence, dict)
                else None
            )
            _require(
                evidence_row.get("non_stop_eligible_evidence_status")
                == "CURRENT",
                f"{evidence_label} must have CURRENT capability evidence",
                errors,
            )
            _validate_current_non_stop_evidence(
                evidence,
                as_of=as_of,
                label=f"{evidence_label}.non_stop_eligible_evidence",
                errors=errors,
            )
            _require(
                isinstance(source_ids, list)
                and bool(source_ids)
                and all(isinstance(item, str) and item.strip() for item in source_ids)
                and len(source_ids) == len(set(source_ids)),
                f"{evidence_label} requires nonempty unique evidence_source_ids",
                errors,
            )
    derived_with_sell = 0
    derived_zero_ids: list[str] = []
    derived_active_rows = 0
    any_partial = False
    seen_orderbooks: set[str] = set()
    for row_index, row in enumerate(per_instrument or []):
        row_label = f"{label}.broker_sell_protection.per_instrument[{row_index}]"
        if canonical_block:
            for field in (
                "orderbook_id",
                "holding_antal",
                "active_sell_row_count",
                "active_sell_antal",
                "protected_antal",
                "active_sell_rows",
            ):
                _require(
                    field in row,
                    f"{row_label}.{field} is required by the canonical schema",
                    errors,
                )
        orderbook_id = str(row.get("orderbook_id") or "").strip()
        holding_antal = row.get("holding_antal", row.get("holding"))
        protected_antal = row.get("protected_antal")
        row_active_sell_count = row.get(
            "active_sell_row_count", row.get("active_sell_count")
        )
        row_active_sell_antal = row.get(
            "active_sell_antal", row.get("active_sell_volume")
        )
        active_sell_rows = row.get("active_sell_rows")
        target_status = str(
            row.get("strategy_target_coverage_status") or ""
        ).strip().upper()
        protection_target_antal = row.get("protection_target_antal")
        retained_core_antal = row.get("retained_core_antal")
        _require(bool(orderbook_id), f"{row_label}.orderbook_id is required", errors)
        _require(
            orderbook_id not in seen_orderbooks,
            f"{row_label}.orderbook_id is duplicated",
            errors,
        )
        seen_orderbooks.add(orderbook_id)
        _require(
            _nonnegative_number(holding_antal) and holding_antal > 1,
            f"{row_label}.holding_antal must be greater than one",
            errors,
        )
        _require(
            _nonnegative_integer(row_active_sell_count),
            f"{row_label}.active_sell_row_count must be a non-negative integer",
            errors,
        )
        _require(
            _nonnegative_number(row_active_sell_antal),
            f"{row_label}.active_sell_antal must be a non-negative number",
            errors,
        )
        _require(
            _nonnegative_number(protected_antal),
            f"{row_label}.protected_antal must be a non-negative number",
            errors,
        )
        _require(
            isinstance(active_sell_rows, list),
            f"{row_label}.active_sell_rows must be a list",
            errors,
        )
        if _nonnegative_integer(row_active_sell_count) and row_active_sell_count > 0:
            _require(
                target_status in STRATEGY_TARGET_COVERAGE_STATUSES,
                f"{row_label}.strategy_target_coverage_status is missing or invalid",
                errors,
            )
            _require(
                target_status != "NOT_APPLICABLE",
                f"{row_label} active SELL row cannot have NOT_APPLICABLE strategy target coverage",
                errors,
            )
        if target_status == "MATCHED":
            _require(
                _nonnegative_number(protection_target_antal)
                and protection_target_antal > 0
                and _nonnegative_number(retained_core_antal),
                f"{row_label} MATCHED target requires positive protection_target_antal and non-negative retained_core_antal",
                errors,
            )
            if (
                _nonnegative_number(protection_target_antal)
                and _nonnegative_number(retained_core_antal)
                and _nonnegative_number(holding_antal)
            ):
                _require(
                    abs(
                        protection_target_antal
                        + retained_core_antal
                        - holding_antal
                    )
                    <= 1e-8,
                    f"{row_label} target plus retained core differs from holding",
                    errors,
                )
            if _nonnegative_number(row_active_sell_antal):
                _require(
                    protection_target_antal == row_active_sell_antal,
                    f"{row_label} MATCHED target differs from active SELL Antal",
                    errors,
                )
        elif target_status == "UNDERCOVERED" and _nonnegative_number(
            protection_target_antal
        ):
            _require(
                _nonnegative_number(row_active_sell_antal)
                and row_active_sell_antal < protection_target_antal,
                f"{row_label} UNDERCOVERED status contradicts target Antal",
                errors,
            )
        elif target_status == "OVERCOVERED" and _nonnegative_number(
            protection_target_antal
        ):
            _require(
                _nonnegative_number(row_active_sell_antal)
                and row_active_sell_antal > protection_target_antal,
                f"{row_label} OVERCOVERED status contradicts target Antal",
                errors,
            )

        evidence_status = str(
            row.get("no_stop_exception_evidence_status") or ""
        ).strip().upper()
        if evidence_status:
            _require(
                evidence_status in NO_STOP_EVIDENCE_STATUSES,
                f"{row_label}.no_stop_exception_evidence_status is invalid",
                errors,
            )
            decision_current = row.get("no_stop_exception_decision_current")
            _require(
                decision_current is (evidence_status == "CURRENT"),
                f"{row_label} no-stop decision-current flag contradicts evidence status",
                errors,
            )
            if evidence_status == "CURRENT":
                evidence = row.get("no_stop_exception_evidence")
                source_ids = (
                    evidence.get("evidence_source_ids")
                    if isinstance(evidence, dict)
                    else None
                )
                choice = str(
                    row.get("no_stop_exception_protection_choice") or ""
                ).strip().upper()
                _require(
                    choice in NO_STOP_PROTECTION_CHOICES,
                    f"{row_label} CURRENT no-stop evidence has invalid protection choice",
                    errors,
                )
                _require(
                    isinstance(source_ids, list)
                    and bool(source_ids)
                    and all(
                        isinstance(item, str) and item.strip()
                        for item in source_ids
                    )
                    and len(source_ids) == len(set(source_ids)),
                    f"{row_label} CURRENT no-stop evidence requires nonempty unique evidence_source_ids",
                    errors,
                )
                expected_action_required = choice in {
                    "TACTICAL_PROFIT_SLICE",
                    "WIDER_CALIBRATED_CORE_ROW",
                }
                _require(
                    row.get("no_stop_exception_protection_action_required")
                    is expected_action_required,
                    f"{row_label} no-stop action-required flag contradicts protection choice",
                    errors,
                )
                _validate_current_no_stop_evidence(
                    evidence,
                    choice=choice,
                    as_of=as_of,
                    label=f"{row_label}.no_stop_exception_evidence",
                    errors=errors,
                )
        active_row_ids: list[str] = []
        active_row_antal: list[float] = []
        for active_index, active_row in enumerate(active_sell_rows or []):
            active_label = f"{row_label}.active_sell_rows[{active_index}]"
            if not isinstance(active_row, dict):
                errors.append(f"{active_label} must be an object")
                continue
            if canonical_block:
                _require(
                    "antal" in active_row,
                    f"{active_label}.antal is required by the canonical schema",
                    errors,
                )
            antal = _first_present(active_row, "antal", "volume")
            _require(
                _nonnegative_number(antal) and antal > 0,
                f"{active_label}.antal must be a positive number",
                errors,
            )
            if _nonnegative_number(antal):
                active_row_antal.append(float(antal))
            row_id = str(
                _first_present(active_row, "stop_loss_id", "order_id", "id")
                or ""
            ).strip()
            if row_id:
                active_row_ids.append(row_id)
        _require(
            len(active_row_ids) == len(set(active_row_ids)),
            f"{row_label}.active_sell_rows contains duplicate broker IDs",
            errors,
        )
        if (
            _nonnegative_integer(row_active_sell_count)
            and _nonnegative_number(row_active_sell_antal)
        ):
            _require(
                (row_active_sell_count == 0) == (row_active_sell_antal == 0),
                f"{row_label} active SELL row count/Antal are inconsistent",
                errors,
            )
            if isinstance(active_sell_rows, list):
                _require(
                    len(active_sell_rows) == row_active_sell_count,
                    f"{row_label}.active_sell_rows count differs from active_sell_row_count",
                    errors,
                )
                _require(
                    abs(sum(active_row_antal) - float(row_active_sell_antal))
                    <= 1e-8,
                    f"{row_label}.active_sell_rows Antal does not sum to the per-orderbook active SELL Antal",
                    errors,
                )
            derived_active_rows += row_active_sell_count
            if row_active_sell_count > 0:
                derived_with_sell += 1
            else:
                derived_zero_ids.append(orderbook_id)
        if _nonnegative_number(protected_antal) and _nonnegative_number(holding_antal):
            _require(
                protected_antal <= holding_antal,
                f"{row_label}.protected_antal exceeds holding_antal",
                errors,
            )
            any_partial = any_partial or (0 < protected_antal < holding_antal)
        if _nonnegative_number(protected_antal) and _nonnegative_number(row_active_sell_antal):
            _require(
                protected_antal <= row_active_sell_antal,
                f"{row_label}.protected_antal exceeds active SELL Antal",
                errors,
            )
            if _nonnegative_number(holding_antal):
                _require(
                    protected_antal == min(holding_antal, row_active_sell_antal),
                    f"{row_label}.protected_antal does not match per-orderbook broker SELL coverage",
                    errors,
                )
            if row_active_sell_antal > 0:
                _require(
                    protected_antal > 0,
                    f"{row_label} active SELL row has zero actual protected Antal",
                    errors,
                )

    if _nonnegative_integer(eligible_position_count) and per_instrument is not None:
        _require(
            len(per_instrument) == eligible_position_count,
            f"{label}.broker_sell_protection eligible_position_count differs from per_instrument evidence",
            errors,
        )
    if _nonnegative_integer(covered_position_count):
        _require(
            covered_position_count == derived_with_sell,
            f"{label}.broker_sell_protection covered_position_count differs from per_instrument evidence",
            errors,
        )
    if _nonnegative_integer(active_sell_row_count):
        _require(
            active_sell_row_count >= derived_active_rows,
            f"{label}.broker_sell_protection active_sell_row_count is below per_instrument rows",
            errors,
        )
    if _nonnegative_integer(reported_target_mismatch_count):
        _require(
            reported_target_mismatch_count
            == state["derived_target_mismatch_count"],
            f"{label}.broker_sell_protection strategy target mismatch count differs from per-instrument evidence",
            errors,
        )
    if zero_sell_count is not None:
        _require(
            zero_sell_count == len(derived_zero_ids),
            f"{label}.broker_sell_protection zero_sell_material_positions differs from per_instrument evidence",
            errors,
        )
        zero_sell_value = _first_present(
            block,
            "zero_sell_material_positions",
            "zero_sell_material_position_count",
        )
        if isinstance(zero_sell_value, list):
            recorded_ids = {
                str(item.get("orderbook_id") or "").strip()
                if isinstance(item, dict)
                else str(item).strip()
                for item in zero_sell_value
            }
            _require(
                recorded_ids == set(derived_zero_ids),
                f"{label}.broker_sell_protection zero-SELL material identities differ from per_instrument evidence",
                errors,
            )

    if zero_sell_count is not None and zero_sell_count > 0:
        _require(
            coverage_status == "BROKER_SELL_PROTECTION_ABSENT"
            and review_status == "PROTECTION_REVIEW_REQUIRED",
            f"{label}.broker_sell_protection material zero-SELL rows must be BROKER_SELL_PROTECTION_ABSENT / PROTECTION_REVIEW_REQUIRED",
            errors,
        )
    elif (
        zero_sell_count == 0
        and eligible_position_count == 0
    ):
        _require(
            coverage_status == "NOT_APPLICABLE"
            and review_status == "CURRENT",
            f"{label}.broker_sell_protection empty eligible universe must be current NOT_APPLICABLE",
            errors,
        )
    elif zero_sell_count == 0 and _nonnegative_integer(eligible_position_count):
        _require(
            covered_position_count == eligible_position_count,
            f"{label}.broker_sell_protection every eligible position must have an active SELL row",
            errors,
        )
        expected_status = (
            "BROKER_SELL_PROTECTION_PARTIAL"
            if any_partial
            else "BROKER_SELL_PROTECTION_PRESENT"
        )
        expected_review_status = (
            "PROTECTION_REVIEW_REQUIRED"
            if state["derived_target_mismatch_count"]
            or state["target_status_unknown_count"]
            else "CURRENT"
        )
        _require(
            coverage_status == expected_status
            and review_status == expected_review_status,
            f"{label}.broker_sell_protection coverage/review status contradicts per-instrument protected and target Antal",
            errors,
        )
    return state


def canonical_review_sha256(review: dict[str, Any]) -> str:
    """Return the immutable canonical hash used by appended annotations."""

    encoded = json.dumps(
        review,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    return hashlib.sha256(encoded).hexdigest()


def _validated_timing_annotations(
    payload: dict[str, Any],
    reviews: list[dict[str, Any]],
    errors: list[str],
) -> dict[int, str]:
    annotations_value = payload.get("timing_annotations", [])
    _require(
        isinstance(annotations_value, list),
        "timing_annotations must be a list",
        errors,
    )
    annotations = annotations_value if isinstance(annotations_value, list) else []
    seen_ids: set[str] = set()
    seen_targets: set[int] = set()
    validated: dict[int, str] = {}

    for annotation_index, annotation in enumerate(annotations):
        label = f"timing_annotations[{annotation_index}]"
        if not isinstance(annotation, dict):
            errors.append(f"{label} must be an object")
            continue
        annotation_id = str(annotation.get("annotation_id") or "").strip()
        _require(bool(annotation_id), f"{label}.annotation_id is required", errors)
        _require(
            annotation_id not in seen_ids,
            f"{label}.annotation_id is duplicated",
            errors,
        )
        seen_ids.add(annotation_id)

        review_index = annotation.get("review_index")
        if not isinstance(review_index, int) or isinstance(review_index, bool):
            errors.append(f"{label}.review_index must be an integer")
            continue
        _require(
            review_index not in seen_targets,
            f"{label}.review_index already has an annotation",
            errors,
        )
        seen_targets.add(review_index)
        if review_index < 0 or review_index >= len(reviews):
            errors.append(f"{label}.review_index does not identify a review")
            continue
        review = reviews[review_index]
        if not isinstance(review, dict):
            errors.append(f"{label}.review_index does not identify an object review")
            continue

        classification = str(annotation.get("classification") or "")
        valid = True
        if classification not in TIMING_ANNOTATION_CLASSES:
            errors.append(f"{label}.classification is invalid")
            valid = False
        if annotation.get("review_id") != review.get("review_id"):
            errors.append(f"{label}.review_id does not match its review")
            valid = False
        if annotation.get("canonical_sha256") != canonical_review_sha256(review):
            errors.append(f"{label}.canonical_sha256 does not match its review")
            valid = False
        if annotation.get("preserves_eligible_false") is not True:
            errors.append(f"{label}.preserves_eligible_false must be true")
            valid = False
        if review.get("eligible") is not False:
            errors.append(f"{label} cannot annotate an eligible review")
            valid = False
        if not str(annotation.get("reason") or "").strip():
            errors.append(f"{label}.reason is required")
            valid = False
        recorded = _aware_datetime(annotation.get("recorded_at"), f"{label}.recorded_at", errors)
        completed = _aware_datetime(review.get("completed_at"), f"{label}.review_completed_at", errors)
        if recorded is not None and completed is not None and recorded < completed:
            errors.append(f"{label}.recorded_at predates its review")
            valid = False

        if classification == "PRESERVED_LATE_FAILED_ATTEMPT":
            scheduled = _aware_datetime(review.get("scheduled_at"), f"{label}.review_scheduled_at", errors)
            if scheduled is None or completed is None or completed - scheduled <= timedelta(hours=6):
                errors.append(f"{label} does not identify a late review")
                valid = False
        elif classification == "PRESERVED_OUT_OF_WINDOW_HEARTBEAT":
            if review.get("market_session_state") != "UNKNOWN":
                errors.append(f"{label} out-of-window review must have UNKNOWN session state")
                valid = False
        elif classification == "PRESERVED_SEP3_MORNING_UNAVAILABLE_ATTEMPT":
            if review.get("market_session_state") not in ELIGIBLE_SESSION_STATES:
                errors.append(f"{label} unavailable attempt must be an eligible session state")
                valid = False

        if valid:
            validated[review_index] = classification
    return validated


def _review_eligible(review: dict[str, Any]) -> bool:
    accounts = review.get("accounts")
    gates = review.get("gates")
    account_controls_ok = bool(
        isinstance(accounts, list)
        and _account_scope(accounts) == EXPECTED_ACCOUNTS
        and all(
            row.get("session_verified") is True
            and row.get("position_governance_complete") is True
            and row.get("stop_strategy_complete") is True
            and row.get("failed_order_count") == 0
            and row.get("authorization_off") is True
            and _broker_sell_protection_state(row).get("eligible") is True
            for row in accounts
            if isinstance(row, dict)
        )
    )
    gates_ok = bool(
        isinstance(gates, dict)
        and set(gates) == REQUIRED_GATES
        and all(value is True for value in gates.values())
    )
    return bool(
        review.get("market_session_state") in ELIGIBLE_SESSION_STATES
        and account_controls_ok
        and gates_ok
        and review.get("blockers") == []
        and isinstance(review.get("evidence"), list)
        and bool(review["evidence"])
        and all(str(item).strip() for item in review["evidence"])
    )


def _derived_streak(reviews: list[dict[str, Any]]) -> tuple[int, int, bool]:
    session_reviews = [
        row
        for row in reviews
        if row.get("market_session_state") in ELIGIBLE_SESSION_STATES
    ]
    consecutive: list[dict[str, Any]] = []
    for row in reversed(session_reviews):
        if row.get("eligible") is not True:
            break
        consecutive.append(row)
    consecutive.reverse()

    tail = consecutive[-10:]
    dates: dict[str, set[str]] = {}
    for row in tail:
        dates.setdefault(str(row.get("market_session_date") or ""), set()).add(
            str(row.get("window") or "")
        )
    complete = bool(
        len(tail) == 10
        and len(dates) >= 5
        and all(windows == REVIEW_WINDOWS for windows in dates.values())
        and len(dates) * 2 == len(tail)
    )
    consecutive_session_count = len(
        {str(row.get("market_session_date") or "") for row in consecutive}
    )
    return len(consecutive), consecutive_session_count, complete


def validate(
    payload: dict[str, Any],
    *,
    require_complete: bool = False,
) -> list[str]:
    """Return every structural or fail-closed completion error."""

    errors: list[str] = []
    _require(
        payload.get("artifact") == "PORTFOLIO_GOVERNANCE_REVIEW_STREAK",
        "ledger artifact id is invalid",
        errors,
    )
    _require(payload.get("version") == 1, "ledger version must be 1", errors)
    _require(
        payload.get("timezone") == "Europe/Stockholm",
        "ledger timezone must be Europe/Stockholm",
        errors,
    )
    _require(
        payload.get("required_review_count") == 10,
        "required_review_count must be 10",
        errors,
    )
    _require(
        payload.get("required_regular_session_count") == 5,
        "required_regular_session_count must be 5",
        errors,
    )

    reviews_value = payload.get("reviews")
    _require(isinstance(reviews_value, list), "reviews must be a list", errors)
    reviews = reviews_value if isinstance(reviews_value, list) else []
    annotations = _validated_timing_annotations(payload, reviews, errors)
    seen_ids: set[str] = set()
    seen_windows: dict[tuple[str, str], int] = {}
    previous_completed: datetime | None = None

    for index, review in enumerate(reviews):
        label = f"reviews[{index}]"
        if not isinstance(review, dict):
            errors.append(f"{label} must be an object")
            continue
        review_id = str(review.get("review_id") or "").strip()
        _require(bool(review_id), f"{label}.review_id is required", errors)
        _require(review_id not in seen_ids, f"{label}.review_id is duplicated", errors)
        seen_ids.add(review_id)

        window = str(review.get("window") or "")
        session_date = str(review.get("market_session_date") or "")
        session_state = str(review.get("market_session_state") or "")
        _require(window in REVIEW_WINDOWS, f"{label}.window is invalid", errors)
        _require(
            session_state in MARKET_SESSION_STATES,
            f"{label}.market_session_state is invalid",
            errors,
        )
        date_window = (session_date, window)
        if date_window in seen_windows:
            prior_index = seen_windows[date_window]
            duplicate_is_preserved = bool(
                annotations.get(prior_index) == "PRESERVED_OUT_OF_WINDOW_HEARTBEAT"
                and annotations.get(index)
                == "PRESERVED_SEP3_MORNING_UNAVAILABLE_ATTEMPT"
            )
            _require(
                duplicate_is_preserved,
                f"{label} duplicates a market-session review window",
                errors,
            )
        else:
            seen_windows[date_window] = index

        scheduled = _aware_datetime(review.get("scheduled_at"), f"{label}.scheduled_at", errors)
        completed = _aware_datetime(review.get("completed_at"), f"{label}.completed_at", errors)
        if scheduled is not None:
            _require(
                scheduled.astimezone(STOCKHOLM).date().isoformat() == session_date,
                f"{label}.market_session_date does not match scheduled_at",
                errors,
            )
        if scheduled is not None and completed is not None:
            _require(completed >= scheduled, f"{label} completed before it was scheduled", errors)
            late_is_preserved = bool(
                completed - scheduled > timedelta(hours=6)
                and annotations.get(index) == "PRESERVED_LATE_FAILED_ATTEMPT"
            )
            _require(
                completed - scheduled <= timedelta(hours=6) or late_is_preserved,
                f"{label} was recorded more than six hours after its schedule",
                errors,
            )
            _require(
                completed.astimezone(STOCKHOLM).date().isoformat() == session_date
                or late_is_preserved,
                f"{label}.market_session_date does not match completed_at",
                errors,
            )
            if previous_completed is not None:
                _require(
                    completed > previous_completed,
                    f"{label}.completed_at is not strictly chronological",
                    errors,
                )
            previous_completed = completed

        accounts = review.get("accounts")
        _require(
            _account_scope(accounts) == EXPECTED_ACCOUNTS,
            f"{label}.accounts must contain both exact tenant/account pairs",
            errors,
        )
        if isinstance(accounts, list):
            for account_index, account in enumerate(accounts):
                if not isinstance(account, dict):
                    errors.append(f"{label}.accounts[{account_index}] must be an object")
                    continue
                account_label = f"{label}.accounts[{account_index}]"
                for field in (
                    "session_verified",
                    "position_governance_complete",
                    "stop_strategy_complete",
                    "authorization_off",
                ):
                    _require(
                        isinstance(account.get(field), bool),
                        f"{label}.accounts[{account_index}].{field} must be boolean",
                        errors,
                    )
                failed_count = account.get("failed_order_count")
                _require(
                    isinstance(failed_count, int) and failed_count >= 0,
                    f"{label}.accounts[{account_index}].failed_order_count must be a non-negative integer",
                    errors,
                )
                protection_state = _validate_broker_sell_protection(
                    account,
                    label=account_label,
                    as_of=completed,
                    errors=errors,
                )
                if (
                    protection_state.get("explicit")
                    and protection_state.get("assessment_status") == "ASSESSED"
                    and protection_state.get("review_status")
                    == "PROTECTION_REVIEW_REQUIRED"
                ):
                    _require(
                        bool(review.get("blockers")),
                        f"{account_label}.broker_sell_protection review-required state must retain a review blocker",
                        errors,
                    )

        gates = review.get("gates")
        _require(isinstance(gates, dict), f"{label}.gates must be an object", errors)
        if isinstance(gates, dict):
            _require(
                set(gates) == REQUIRED_GATES,
                f"{label}.gates must contain the exact required gate set",
                errors,
            )
            _require(
                all(isinstance(value, bool) for value in gates.values()),
                f"{label}.gates values must be boolean",
                errors,
            )
        _require(isinstance(review.get("blockers"), list), f"{label}.blockers must be a list", errors)
        _require(isinstance(review.get("evidence"), list), f"{label}.evidence must be a list", errors)

        derived_eligible = _review_eligible(review)
        _require(
            review.get("eligible") is derived_eligible,
            f"{label}.eligible does not match its gate evidence",
            errors,
        )

    consecutive_count, consecutive_session_count, complete = _derived_streak(reviews)
    _require(
        payload.get("current_consecutive_eligible_reviews") == consecutive_count,
        "current_consecutive_eligible_reviews does not match the review tail",
        errors,
    )
    _require(
        payload.get("current_regular_session_count") == consecutive_session_count,
        "current_regular_session_count does not match the eligible review tail",
        errors,
    )
    _require(
        payload.get("completion_claim") is complete,
        "completion_claim does not match the derived streak",
        errors,
    )
    _require(
        payload.get("status") == ("COMPLETE" if complete else "ACTIVE_NOT_COMPLETE"),
        "status does not match the derived streak",
        errors,
    )
    if require_complete:
        _require(complete, "ten eligible reviews across five sessions are not complete", errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    ledger = args.ledger if args.ledger.is_absolute() else ROOT / args.ledger
    try:
        payload = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[governance-streak] FAIL: cannot read {ledger}: {exc}")
        return 1
    errors = validate(payload, require_complete=args.require_complete)
    if errors:
        for error in errors:
            print(f"[governance-streak] FAIL: {error}")
        return 1
    reviews = payload.get("reviews")
    latest = reviews[-1] if isinstance(reviews, list) and reviews else None
    protection_parts: list[str] = []
    if isinstance(latest, dict) and isinstance(latest.get("accounts"), list):
        for account in latest["accounts"]:
            if not isinstance(account, dict):
                continue
            state = _broker_sell_protection_state(account)
            tenant = str(account.get("tenant_session_id") or "unknown")
            count = state.get("active_sell_row_count")
            eligible_count = state.get("eligible_position_count")
            covered_count = state.get("covered_position_count")
            zero_count = state.get("zero_sell_material_position_count")
            protection_parts.append(
                f"{tenant}:{state['coverage_status']}"
                f"/active_row_count={count if count is not None else 'unknown'}"
                f"/eligible_positions={eligible_count if eligible_count is not None else 'unknown'}"
                f"/covered_positions={covered_count if covered_count is not None else 'unknown'}"
                f"/zero_sell_material={zero_count if zero_count is not None else 'unknown'}"
            )
    if not protection_parts:
        protection_parts.append(
            "no-current-review:NOT_ASSESSED/active_row_count=unknown/"
            "eligible_positions=unknown/covered_positions=unknown/"
            "zero_sell_material=unknown"
        )
    print(
        "[governance-streak] PASS: "
        f"{payload['current_consecutive_eligible_reviews']}/10 eligible reviews; "
        f"status={payload['status']}; broker SELL protection "
        + ", ".join(protection_parts)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
