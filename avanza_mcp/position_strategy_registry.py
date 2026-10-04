"""Durable, account-scoped strategy plans for every tracked position.

The broker exposes holdings and orders, but it does not retain the investment
thesis, intended horizon, or reviewed aggregate exposure. This registry keeps
that control data locally and compares it with live holdings and order state.
It never authorizes or places a broker order.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from avanza_mcp.utils import scalar_number

REGISTRY_VERSION = 1
POSITION_STRATEGY_RECORDED = "RECORDED"
POSITION_STRATEGY_MISSING = "MISSING"
POSITION_STRATEGY_STALE_MISMATCH = "STALE_MISMATCH"
POSITION_STRATEGY_REGISTRY_UNAVAILABLE = "REGISTRY_UNAVAILABLE"

POSITION_PROTECTION_VALID = "VALID"
POSITION_PROTECTION_MISSING = "MISSING"
POSITION_PROTECTION_INVALID = "INVALID"
POSITION_PROTECTION_CONTRADICTION = "CONTRADICTION"
POSITION_PROTECTION_REPAIR_REQUIRED = "REPAIR_REQUIRED"
POSITION_PROTECTION_REGISTRY_UNAVAILABLE = "REGISTRY_UNAVAILABLE"
POSITION_PROTECTION_CLASSIFICATIONS = frozenset(
    {
        "CALIBRATED_STOP_PROFIT_LADDER",
        "CORE_HOLD_EXCEPTION",
        "MARKER_EXCEPTION",
        "NAMED_EXCEPTION",
        "NON_STOP_ELIGIBLE",
        "REPAIR_REQUIRED",
    }
)
NO_STOP_EXCEPTION_CLASSIFICATIONS = frozenset(
    {
        "CORE_HOLD_EXCEPTION",
        "NAMED_EXCEPTION",
    }
)
NO_STOP_EXCEPTION_EVIDENCE_FIELDS = (
    "decision_at",
    "evidence_as_of",
    "next_review_at",
    "valid_until",
    "gap_risk_statement",
    "evidence_source_ids",
    "protection_choice",
)
NO_STOP_PROTECTION_CHOICES = frozenset(
    {
        "TACTICAL_PROFIT_SLICE",
        "WIDER_CALIBRATED_CORE_ROW",
        "DELIBERATELY_UNPROTECTED_CORE",
    }
)
NON_STOP_ELIGIBLE_EVIDENCE_FIELDS = (
    "evidence_as_of",
    "valid_until",
    "capability_statement",
    "evidence_source_ids",
)

_LIVE_STATE_FIELDS = (
    "account_id",
    "orderbook_id",
    "holding",
    "active_buy_volume",
    "active_sell_volume",
    "active_buy_count",
    "active_sell_count",
    "open_buy_volume",
    "open_sell_volume",
    "open_buy_count",
    "open_sell_count",
)
_STOP_EXPOSURE_FIELDS = {
    "active_buy_volume",
    "active_sell_volume",
    "active_buy_count",
    "active_sell_count",
}
_OPEN_ORDER_EXPOSURE_FIELDS = {
    "open_buy_volume",
    "open_sell_volume",
    "open_buy_count",
    "open_sell_count",
}
_POSITION_AUDIT_EXCEPTION_ALLOWED_FIELDS = {"holding"}
_POSITION_AUDIT_EXCEPTION_KINDS = {
    "USER_CONTROLLED_ALLOCATION",
    "POST_MANUAL_EXIT_DRIFT",
}
_TERMINAL_ORDER_STATUSES = {
    "CANCELLED",
    "CANCELED",
    "DELETED",
    "EXPIRED",
    "FAILED",
    "FAULTY",
    "FELAKTIG",
    "FILLED",
    "REJECTED",
}
_REQUIRED_PLAN_TEXT_FIELDS = (
    "instrument",
    "strategy_class",
    "horizon",
    "thesis",
    "gate",
    "audit_status",
    "recommendation",
    "priority",
    "bucket",
    "stance",
    "next_gate",
    "protection_classification",
    "protection_reason",
)
_OPTIONAL_PLAN_TEXT_FIELDS = (
    "ticker",
    "venue",
)
_PLAN_TEXT_FIELDS = (
    *_REQUIRED_PLAN_TEXT_FIELDS,
    *_OPTIONAL_PLAN_TEXT_FIELDS,
)
_TYPED_PLAN_FIELDS = (
    "no_stop_exception_evidence",
    "non_stop_eligible_evidence",
    "protection_target_antal",
    "retained_core_antal",
)


def _normalize_audit_exception(value: Any) -> dict[str, Any] | None:
    """Validate metadata that explains intentional drift without clearing it."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("audit_exception must be an object or null.")
    kind = _normalized_token(value.get("kind"))
    reason = str(value.get("reason") or "").strip()
    owner = str(value.get("owner") or "").strip()
    review_due = str(value.get("review_due") or "").strip()
    allowed_fields = value.get("allowed_mismatches")
    if kind not in _POSITION_AUDIT_EXCEPTION_KINDS:
        raise ValueError(
            "audit_exception.kind must be USER_CONTROLLED_ALLOCATION or "
            "POST_MANUAL_EXIT_DRIFT."
        )
    if not reason or not owner or not review_due:
        raise ValueError(
            "audit_exception requires reason, owner, and review_due."
        )
    if not isinstance(allowed_fields, list) or not allowed_fields:
        raise ValueError("audit_exception.allowed_mismatches must be a non-empty list.")
    normalized_fields = sorted({_normalized_token(field).lower() for field in allowed_fields})
    if not set(normalized_fields).issubset(_POSITION_AUDIT_EXCEPTION_ALLOWED_FIELDS):
        raise ValueError("audit_exception may acknowledge holding drift only.")
    return {
        "kind": kind,
        "reason": reason,
        "owner": owner,
        "review_due": review_due,
        "allowed_mismatches": normalized_fields,
        "rebaseline_authorized": False,
    }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalized_token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _normalized_number(value: Any) -> float:
    parsed = scalar_number(value)
    return round(float(parsed or 0.0), 8)


def _normalized_count(value: Any) -> int:
    parsed = scalar_number(value)
    if parsed is None or float(parsed) < 0 or not float(parsed).is_integer():
        raise ValueError(f"Expected a non-negative integer count, got {value!r}.")
    return int(parsed)


def _aware_timestamp(value: Any) -> datetime | None:
    """Parse an ISO timestamp only when it carries an explicit UTC offset."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _normalized_source_ids(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    normalized = [item.strip() for item in value]
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        return None
    return normalized


def _no_stop_exception_evidence_evaluation(
    value: Any,
    *,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Validate a closed, current no-stop decision without inferring prose."""

    if value is None:
        return "MISSING", None, list(NO_STOP_EXCEPTION_EVIDENCE_FIELDS)
    if not isinstance(value, dict):
        return "INVALID", None, ["no_stop_exception_evidence must be an object"]

    missing = [field for field in NO_STOP_EXCEPTION_EVIDENCE_FIELDS if field not in value]
    unknown = sorted(set(value) - set(NO_STOP_EXCEPTION_EVIDENCE_FIELDS))
    issues = [f"unexpected field: {field}" for field in unknown]
    if missing:
        return "MISSING", None, [*missing, *issues]

    normalized = {
        "decision_at": (
            value.get("decision_at", "").strip()
            if isinstance(value.get("decision_at"), str)
            else ""
        ),
        "evidence_as_of": (
            value.get("evidence_as_of", "").strip()
            if isinstance(value.get("evidence_as_of"), str)
            else ""
        ),
        "next_review_at": (
            value.get("next_review_at", "").strip()
            if isinstance(value.get("next_review_at"), str)
            else ""
        ),
        "valid_until": (
            value.get("valid_until", "").strip()
            if isinstance(value.get("valid_until"), str)
            else ""
        ),
        "gap_risk_statement": (
            value.get("gap_risk_statement", "").strip()
            if isinstance(value.get("gap_risk_statement"), str)
            else ""
        ),
        "evidence_source_ids": _normalized_source_ids(value.get("evidence_source_ids")),
        "protection_choice": (
            _normalized_token(value.get("protection_choice"))
            if isinstance(value.get("protection_choice"), str)
            else ""
        ),
    }
    timestamps = {
        field: _aware_timestamp(normalized[field])
        for field in ("decision_at", "evidence_as_of", "next_review_at", "valid_until")
    }
    for field, parsed in timestamps.items():
        if parsed is None:
            issues.append(f"{field} must be a timezone-aware ISO timestamp")
    if not normalized["gap_risk_statement"]:
        issues.append("gap_risk_statement must be nonblank")
    if normalized["evidence_source_ids"] is None:
        issues.append("evidence_source_ids must be a nonempty unique string array")
    if normalized["protection_choice"] not in NO_STOP_PROTECTION_CHOICES:
        issues.append(
            "protection_choice must be TACTICAL_PROFIT_SLICE, "
            "WIDER_CALIBRATED_CORE_ROW, or DELIBERATELY_UNPROTECTED_CORE"
        )

    reference_now = now or datetime.now(timezone.utc)
    evidence_as_of = timestamps["evidence_as_of"]
    decision_at = timestamps["decision_at"]
    next_review_at = timestamps["next_review_at"]
    valid_until = timestamps["valid_until"]
    if evidence_as_of is not None and decision_at is not None:
        if evidence_as_of > decision_at:
            issues.append("evidence_as_of must be at or before decision_at")
    if decision_at is not None and decision_at > reference_now:
        issues.append("decision_at must not be future-dated")
    if evidence_as_of is not None and evidence_as_of > reference_now:
        issues.append("evidence_as_of must not be future-dated")
    if decision_at is not None and next_review_at is not None:
        if next_review_at <= decision_at:
            issues.append("next_review_at must be after decision_at")
    if next_review_at is not None and valid_until is not None:
        if valid_until < next_review_at:
            issues.append("valid_until must be at or after next_review_at")
    if issues:
        return "INVALID", normalized, issues

    elapsed = []
    if next_review_at is not None and next_review_at <= reference_now:
        elapsed.append("next_review_at has elapsed")
    if valid_until is not None and valid_until <= reference_now:
        elapsed.append("valid_until has elapsed")
    if elapsed:
        return "EXPIRED", normalized, elapsed
    return "CURRENT", normalized, []


def _non_stop_eligible_evidence_evaluation(
    value: Any,
    *,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Validate dated, sourced evidence that the broker cannot use a stop."""

    if value is None:
        return "MISSING", None, list(NON_STOP_ELIGIBLE_EVIDENCE_FIELDS)
    if not isinstance(value, dict):
        return "INVALID", None, ["non_stop_eligible_evidence must be an object"]

    missing = [field for field in NON_STOP_ELIGIBLE_EVIDENCE_FIELDS if field not in value]
    unknown = sorted(set(value) - set(NON_STOP_ELIGIBLE_EVIDENCE_FIELDS))
    issues = [f"unexpected field: {field}" for field in unknown]
    if missing:
        return "MISSING", None, [*missing, *issues]

    normalized = {
        "evidence_as_of": (
            value.get("evidence_as_of", "").strip()
            if isinstance(value.get("evidence_as_of"), str)
            else ""
        ),
        "valid_until": (
            value.get("valid_until", "").strip()
            if isinstance(value.get("valid_until"), str)
            else ""
        ),
        "capability_statement": (
            value.get("capability_statement", "").strip()
            if isinstance(value.get("capability_statement"), str)
            else ""
        ),
        "evidence_source_ids": _normalized_source_ids(value.get("evidence_source_ids")),
    }
    evidence_as_of = _aware_timestamp(normalized["evidence_as_of"])
    valid_until = _aware_timestamp(normalized["valid_until"])
    if evidence_as_of is None:
        issues.append("evidence_as_of must be a timezone-aware ISO timestamp")
    if valid_until is None:
        issues.append("valid_until must be a timezone-aware ISO timestamp")
    if not normalized["capability_statement"]:
        issues.append("capability_statement must be nonblank")
    if normalized["evidence_source_ids"] is None:
        issues.append("evidence_source_ids must be a nonempty unique string array")
    reference_now = now or datetime.now(timezone.utc)
    if evidence_as_of is not None and evidence_as_of > reference_now:
        issues.append("evidence_as_of must not be future-dated")
    if evidence_as_of is not None and valid_until is not None:
        if valid_until <= evidence_as_of:
            issues.append("valid_until must be after evidence_as_of")
    if issues:
        return "INVALID", normalized, issues
    if valid_until is not None and valid_until <= reference_now:
        return "EXPIRED", normalized, ["valid_until has elapsed"]
    return "CURRENT", normalized, []


def _position_protection_evaluation(
    plan: dict[str, Any] | None,
    live_state: dict[str, Any],
) -> tuple[str, list[str]]:
    """Validate explicit protection semantics against the exact live row."""

    plan = plan if isinstance(plan, dict) else {}
    classification = _normalized_token(plan.get("protection_classification"))
    reason = str(plan.get("protection_reason") or "").strip()
    if not classification or not reason:
        missing = []
        if not classification:
            missing.append("protection_classification")
        if not reason:
            missing.append("protection_reason")
        return POSITION_PROTECTION_MISSING, missing
    if classification not in POSITION_PROTECTION_CLASSIFICATIONS:
        return POSITION_PROTECTION_INVALID, [
            f"unsupported protection_classification {classification!r}"
        ]

    fingerprint = position_strategy_live_fingerprint(live_state)
    holding = float(fingerprint["holding"])
    active_sell_volume = float(fingerprint["active_sell_volume"])
    active_sell_count = int(fingerprint["active_sell_count"])
    has_active_sell = active_sell_volume > 0 or active_sell_count > 0
    missing: list[str] = []
    invalid: list[str] = []
    contradictions: list[str] = []

    no_stop_value = plan.get("no_stop_exception_evidence")
    no_stop_required = bool(
        holding > 1
        and not has_active_sell
        and classification in NO_STOP_EXCEPTION_CLASSIFICATIONS
    )
    if no_stop_required or no_stop_value is not None:
        evidence_status, _, evidence_issues = (
            _no_stop_exception_evidence_evaluation(no_stop_value)
        )
        if evidence_status == "MISSING":
            missing.extend(
                f"no_stop_exception_evidence.{issue}" for issue in evidence_issues
            )
        elif evidence_status in {"INVALID", "EXPIRED"}:
            invalid.extend(
                f"no_stop_exception_evidence.{issue}" for issue in evidence_issues
            )
        if (
            no_stop_value is not None
            and classification not in NO_STOP_EXCEPTION_CLASSIFICATIONS
        ):
            invalid.append(
                "no_stop_exception_evidence is allowed only for "
                "CORE_HOLD_EXCEPTION or NAMED_EXCEPTION"
            )

    non_stop_value = plan.get("non_stop_eligible_evidence")
    if classification == "NON_STOP_ELIGIBLE" or non_stop_value is not None:
        eligibility_status, _, eligibility_issues = (
            _non_stop_eligible_evidence_evaluation(non_stop_value)
        )
        if eligibility_status == "MISSING":
            missing.extend(
                f"non_stop_eligible_evidence.{issue}" for issue in eligibility_issues
            )
        elif eligibility_status in {"INVALID", "EXPIRED"}:
            invalid.extend(
                f"non_stop_eligible_evidence.{issue}" for issue in eligibility_issues
            )
        if non_stop_value is not None and classification != "NON_STOP_ELIGIBLE":
            invalid.append(
                "non_stop_eligible_evidence is allowed only for NON_STOP_ELIGIBLE"
            )

    target_raw = plan.get("protection_target_antal")
    retained_raw = plan.get("retained_core_antal")
    target_supplied = target_raw is not None
    retained_supplied = retained_raw is not None
    target = scalar_number(target_raw) if not isinstance(target_raw, bool) else None
    retained = scalar_number(retained_raw) if not isinstance(retained_raw, bool) else None

    if classification == "CALIBRATED_STOP_PROFIT_LADDER":
        if active_sell_volume <= 0 or active_sell_count <= 0:
            contradictions.append(
                "CALIBRATED_STOP_PROFIT_LADDER requires active SELL volume and count"
            )
        if not target_supplied:
            missing.append("protection_target_antal")
        elif target is None or float(target) <= 0:
            invalid.append("protection_target_antal must be a positive number")
        if not retained_supplied:
            missing.append("retained_core_antal")
        elif retained is None or float(retained) < 0:
            invalid.append("retained_core_antal must be a non-negative number")
        if target is not None and retained is not None:
            normalized_target = _normalized_number(target)
            normalized_retained = _normalized_number(retained)
            if _normalized_number(normalized_target + normalized_retained) != holding:
                contradictions.append(
                    "protection_target_antal plus retained_core_antal must equal live holding"
                )
            if active_sell_volume < normalized_target:
                contradictions.append(
                    "active SELL Antal is below protection_target_antal"
                )
            elif active_sell_volume > normalized_target:
                contradictions.append(
                    "active SELL Antal exceeds protection_target_antal"
                )
    elif classification in {
        "CORE_HOLD_EXCEPTION",
        "MARKER_EXCEPTION",
        "NAMED_EXCEPTION",
        "NON_STOP_ELIGIBLE",
    }:
        if has_active_sell:
            contradictions.append(
                f"{classification} cannot coexist with an active SELL stop"
            )

    if classification != "CALIBRATED_STOP_PROFIT_LADDER" and (
        target_supplied or retained_supplied
    ):
        invalid.append(
            "protection_target_antal and retained_core_antal are allowed only for "
            "CALIBRATED_STOP_PROFIT_LADDER"
        )

    if classification == "MARKER_EXCEPTION" and holding > 1:
        contradictions.append("MARKER_EXCEPTION requires live holding at or below one")

    no_stop_evidence_status, normalized_no_stop, _ = (
        _no_stop_exception_evidence_evaluation(no_stop_value)
        if no_stop_value is not None
        else ("NOT_APPLICABLE", None, [])
    )
    protection_choice = (
        str((normalized_no_stop or {}).get("protection_choice") or "")
        if no_stop_evidence_status == "CURRENT"
        else ""
    )

    if missing:
        return POSITION_PROTECTION_MISSING, missing
    if invalid:
        return POSITION_PROTECTION_INVALID, invalid
    if contradictions:
        return POSITION_PROTECTION_CONTRADICTION, contradictions
    if (
        no_stop_required
        and protection_choice
        in {"TACTICAL_PROFIT_SLICE", "WIDER_CALIBRATED_CORE_ROW"}
    ):
        return POSITION_PROTECTION_REPAIR_REQUIRED, [
            f"{protection_choice} requires an exact calibrated target and active broker SELL row"
        ]
    if classification == "REPAIR_REQUIRED":
        return POSITION_PROTECTION_REPAIR_REQUIRED, []
    return POSITION_PROTECTION_VALID, []


def _broker_sell_protection_evaluation(row: dict[str, Any]) -> dict[str, Any]:
    """Measure actual broker SELL protection without changing plan validity.

    A reviewed exception may be classification-valid while still having no
    broker protection. Material scope is deliberately narrow and deterministic:
    a live holding above one unit, excluding only an explicit
    NON_STOP_ELIGIBLE classification. This excludes one-unit markers while
    keeping named exceptions, missing plans, and invalid classifications in the
    fail-closed broker-protection scope.
    """

    fingerprint = position_strategy_live_fingerprint(row)
    plan = row.get("position_strategy")
    plan = plan if isinstance(plan, dict) else {}
    classification = _normalized_token(plan.get("protection_classification"))
    holding = float(fingerprint["holding"])
    active_sell_volume = float(fingerprint["active_sell_volume"])
    active_sell_count = int(fingerprint["active_sell_count"])
    non_stop_evidence_status, _, non_stop_evidence_issues = (
        _non_stop_eligible_evidence_evaluation(
            plan.get("non_stop_eligible_evidence")
        )
        if classification == "NON_STOP_ELIGIBLE"
        else ("NOT_APPLICABLE", None, [])
    )
    verified_non_stop_eligible = bool(
        classification == "NON_STOP_ELIGIBLE"
        and non_stop_evidence_status == "CURRENT"
    )
    material_stop_eligible = holding > 1 and not verified_non_stop_eligible
    has_active_sell = active_sell_volume > 0 and active_sell_count > 0
    protected_holding = (
        min(holding, active_sell_volume)
        if material_stop_eligible and has_active_sell
        else 0.0
    )
    fully_protected = bool(
        material_stop_eligible
        and has_active_sell
        and active_sell_volume >= holding
    )
    zero_sell_material = bool(material_stop_eligible and not has_active_sell)

    evidence_applicable = bool(
        zero_sell_material
        and classification in NO_STOP_EXCEPTION_CLASSIFICATIONS
    )
    evidence_status, _, evidence_issues = (
        _no_stop_exception_evidence_evaluation(
            plan.get("no_stop_exception_evidence")
        )
        if evidence_applicable
        else ("NOT_APPLICABLE", None, [])
    )
    protection_choice = _normalized_token(
        (plan.get("no_stop_exception_evidence") or {}).get("protection_choice")
        if isinstance(plan.get("no_stop_exception_evidence"), dict)
        else None
    )
    protection_action_required = bool(
        evidence_applicable
        and protection_choice
        in {"TACTICAL_PROFIT_SLICE", "WIDER_CALIBRATED_CORE_ROW"}
        and not has_active_sell
    )

    target = scalar_number(plan.get("protection_target_antal"))
    retained = scalar_number(plan.get("retained_core_antal"))
    if has_active_sell and classification != "CALIBRATED_STOP_PROFIT_LADDER":
        target_coverage_status = "MISSING"
    elif classification == "CALIBRATED_STOP_PROFIT_LADDER" and (
        target is None or retained is None
    ):
        target_coverage_status = "MISSING"
    elif classification == "CALIBRATED_STOP_PROFIT_LADDER" and (
        active_sell_volume < _normalized_number(target)
    ):
        target_coverage_status = "UNDERCOVERED"
    elif classification == "CALIBRATED_STOP_PROFIT_LADDER" and (
        active_sell_volume > _normalized_number(target)
    ):
        target_coverage_status = "OVERCOVERED"
    elif classification == "CALIBRATED_STOP_PROFIT_LADDER":
        target_coverage_status = "MATCHED"
    else:
        target_coverage_status = "NOT_APPLICABLE"

    if not material_stop_eligible:
        status = "EXCLUDED"
    elif not has_active_sell:
        status = "MISSING"
    elif fully_protected:
        status = "FULL"
    else:
        status = "PARTIAL"

    return {
        "status": status,
        "material_stop_eligible": material_stop_eligible,
        "broker_sell_present": bool(material_stop_eligible and has_active_sell),
        "broker_sell_protected": bool(material_stop_eligible and has_active_sell),
        "broker_sell_fully_protected": fully_protected,
        "strategy_target_coverage_status": target_coverage_status,
        "strategy_target_coverage_complete": (
            target_coverage_status in {"MATCHED", "NOT_APPLICABLE"}
        ),
        "protection_target_antal": (
            _normalized_number(target) if target is not None else None
        ),
        "retained_core_antal": (
            _normalized_number(retained) if retained is not None else None
        ),
        "broker_protection_review_required": zero_sell_material,
        "held_antal": holding,
        "active_sell_row_count": active_sell_count,
        "active_sell_antal": active_sell_volume,
        "protected_antal": _normalized_number(protected_holding),
        "unprotected_antal": _normalized_number(
            holding - protected_holding if material_stop_eligible else 0.0
        ),
        "no_stop_exception_evidence_status": evidence_status,
        "no_stop_exception_evidence_complete": (
            evidence_status == "CURRENT" if evidence_applicable else None
        ),
        "no_stop_exception_evidence_issues": evidence_issues,
        "no_stop_exception_decision_current": evidence_status == "CURRENT",
        "no_stop_exception_protection_choice": protection_choice or None,
        "no_stop_exception_protection_action_required": (
            protection_action_required if evidence_applicable else None
        ),
        "non_stop_eligible_verified": verified_non_stop_eligible,
        "non_stop_eligible_evidence_status": non_stop_evidence_status,
        "non_stop_eligible_evidence_complete": (
            non_stop_evidence_status == "CURRENT"
            if classification == "NON_STOP_ELIGIBLE"
            else None
        ),
        "non_stop_eligible_evidence_issues": non_stop_evidence_issues,
    }


def _row_account_id(row: dict[str, Any]) -> str:
    return str(
        row.get("account_id")
        or row.get("Account ID")
        or row.get("accountId")
        or ""
    ).strip()


def _row_orderbook_id(row: dict[str, Any]) -> str:
    return str(
        row.get("orderbook_id")
        or row.get("order_book_id")
        or row.get("Order Book ID")
        or row.get("orderBookId")
        or ""
    ).strip()


def _row_stock(row: dict[str, Any]) -> str:
    return str(
        row.get("stock")
        or row.get("Stock")
        or row.get("instrument")
        or row.get("instrument_name")
        or ""
    ).strip()


def _row_side(row: dict[str, Any]) -> str:
    return _normalized_token(row.get("side") or row.get("Side"))


def _row_volume(row: dict[str, Any]) -> float:
    return _normalized_number(row.get("volume", row.get("Volume")))


def _row_stop_loss_id(row: dict[str, Any]) -> str:
    return str(
        row.get("stop_loss_id")
        or row.get("Stop Loss ID")
        or row.get("stopLossId")
        or row.get("id")
        or ""
    ).strip()


def _is_active_stop(row: dict[str, Any]) -> bool:
    return _normalized_token(row.get("status") or row.get("Status")) == "ACTIVE"


def _is_active_open_order(row: dict[str, Any]) -> bool:
    status = _normalized_token(row.get("status") or row.get("Status"))
    return status not in _TERMINAL_ORDER_STATUSES


def position_strategy_live_fingerprint(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize the live holdings and aggregate order state used for drift."""

    return {
        "account_id": _row_account_id(row),
        "orderbook_id": _row_orderbook_id(row),
        "holding": _normalized_number(row.get("holding", row.get("volume"))),
        "active_buy_volume": _normalized_number(row.get("active_buy_volume")),
        "active_sell_volume": _normalized_number(row.get("active_sell_volume")),
        "active_buy_count": _normalized_count(row.get("active_buy_count", 0)),
        "active_sell_count": _normalized_count(row.get("active_sell_count", 0)),
        "open_buy_volume": _normalized_number(row.get("open_buy_volume")),
        "open_sell_volume": _normalized_number(row.get("open_sell_volume")),
        "open_buy_count": _normalized_count(row.get("open_buy_count", 0)),
        "open_sell_count": _normalized_count(row.get("open_sell_count", 0)),
    }


def _position_percent(row: dict[str, Any], *keys: str) -> float | None:
    """Read a rendered percentage without treating missing data as zero."""

    for key in keys:
        value = row.get(key)
        if value is None or str(value).strip() == "":
            continue
        cleaned = str(value).strip().replace("%", "").replace(",", "")
        parsed = scalar_number(cleaned)
        if parsed is not None:
            return float(parsed)
    return None


def build_event_protection_screen(
    positions: Iterable[dict[str, Any]],
    strategy_positions: Iterable[dict[str, Any]],
    *,
    material_move_threshold_percent: float = 5.0,
) -> dict[str, Any]:
    """Build a read-only event/protection triage screen.

    The threshold is a surfacing aid, not a universal stop or sell rule. The
    screen forces a plan-level event decision when a holding is event-sensitive
    or makes a material move without active SELL protection, while preserving
    the distinction between a review flag and trade authorization.
    """

    threshold = abs(float(material_move_threshold_percent))
    strategy_by_orderbook = {
        str(row.get("orderbook_id") or ""): row
        for row in strategy_positions
        if row.get("orderbook_id")
    }
    event_tokens = (
        "EVENT",
        "EARNINGS",
        "REPORT",
        "GUIDANCE",
        "CATALYST",
        "AFTER-CLOSE",
        "BEFORE-OPEN",
        "POST-EVENT",
        "PUBLICATION",
    )
    decision_tokens = (
        "HOLD",
        "REDUCE",
        "SELL",
        "TRIM",
        "RECLAIM",
        "AVOID",
        "ADD",
        "REVIEW",
    )
    rows: list[dict[str, Any]] = []
    for position in positions:
        orderbook_id = str(
            position.get("orderbook_id") or position.get("Order Book ID") or ""
        ).strip()
        strategy_row = strategy_by_orderbook.get(orderbook_id, {})
        plan = strategy_row.get("position_strategy") or {}
        day_percent = _position_percent(position, "Day %", "day_percent")
        profit_percent = _position_percent(position, "Profit %", "profit_percent")
        holding = _normalized_number(
            position.get("volume", position.get("Volume", 0))
        )
        active_sell_volume = _normalized_number(
            strategy_row.get("active_sell_volume")
        )
        active_sell_count = _normalized_count(
            strategy_row.get("active_sell_count", 0)
        )
        plan_text = " ".join(
            str(plan.get(field) or "")
            for field in ("audit_status", "bucket", "gate", "recommendation", "stance", "next_gate")
        ).upper()
        event_sensitive = any(token in plan_text for token in event_tokens)
        explicit_event_decision = event_sensitive and any(
            token in plan_text for token in decision_tokens
        )
        material_move = (
            day_percent is not None and abs(day_percent) >= threshold
        )
        profitable_without_sell = (
            holding > 0
            and active_sell_volume <= 0
            and profit_percent is not None
            and profit_percent > 0
        )
        protection_review_required = holding > 0 and active_sell_volume <= 0 and (
            material_move or event_sensitive
        )
        if not protection_review_required:
            continue
        rows.append(
            {
                "account_id": str(strategy_row.get("account_id") or ""),
                "orderbook_id": orderbook_id,
                "stock": str(position.get("stock") or position.get("Stock") or ""),
                "holding": holding,
                "day_percent": day_percent,
                "profit_percent": profit_percent,
                "active_sell_volume": active_sell_volume,
                "active_sell_count": active_sell_count,
                "event_sensitive": event_sensitive,
                "material_move": material_move,
                "profitable_without_sell": profitable_without_sell,
                "explicit_event_decision": explicit_event_decision,
                "decision_required": not explicit_event_decision or material_move,
                "audit_status": plan.get("audit_status"),
                "bucket": plan.get("bucket"),
                "next_gate": plan.get("next_gate"),
            }
        )

    rows.sort(
        key=lambda row: (
            not row["material_move"],
            not row["profitable_without_sell"],
            -(abs(row["day_percent"]) if row["day_percent"] is not None else 0.0),
            row["stock"],
        )
    )
    return {
        "authority": "READ_ONLY_TRIAGE",
        "broker_mutation": False,
        "trade_authority": False,
        "material_move_threshold_percent": threshold,
        "rows": rows,
        "material_move_count": sum(row["material_move"] for row in rows),
        "profitable_without_sell_count": sum(
            row["profitable_without_sell"] for row in rows
        ),
        "decision_required_count": sum(row["decision_required"] for row in rows),
        "notes": [
            "A material move is a review trigger, not a universal stop or sell rule.",
            "Event gaps, core retention, tactical slices, and risk-off decisions remain instrument-specific.",
        ],
    }


def build_position_strategy_live_states(
    account_id: str,
    positions: Iterable[dict[str, Any]],
    stoplosses: Iterable[dict[str, Any]],
    open_orders: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build one exact live state per account/orderbook.

    The tracked set is the union of held instruments, active stop rows, and
    non-terminal regular orders. This also catches an order left behind after
    a position reaches zero. Individual active SELL rows are returned as
    read-only evidence but deliberately remain outside the persisted live
    fingerprint.
    """

    account_token = str(account_id or "").strip()
    if not account_token:
        raise ValueError("account_id is required.")

    states: dict[str, dict[str, Any]] = {}

    def ensure(orderbook_id: str, stock: str = "") -> dict[str, Any]:
        token = str(orderbook_id or "").strip()
        if not token:
            raise ValueError("Cannot audit a position/order without orderbook_id.")
        state = states.setdefault(
            token,
            {
                "account_id": account_token,
                "orderbook_id": token,
                "stock": str(stock or "").strip(),
                "holding": 0.0,
                "active_buy_volume": 0.0,
                "active_sell_volume": 0.0,
                "active_buy_count": 0,
                "active_sell_count": 0,
                "active_sell_rows": [],
                "open_buy_volume": 0.0,
                "open_sell_volume": 0.0,
                "open_buy_count": 0,
                "open_sell_count": 0,
            },
        )
        if not state["stock"] and stock:
            state["stock"] = str(stock).strip()
        return state

    for position in positions:
        if _row_account_id(position) not in {"", account_token}:
            continue
        state = ensure(_row_orderbook_id(position), _row_stock(position))
        state["holding"] = _normalized_number(
            state["holding"] + _normalized_number(position.get("volume"))
        )

    for stoploss in stoplosses:
        if _row_account_id(stoploss) not in {"", account_token}:
            continue
        if not _is_active_stop(stoploss):
            continue
        side = _row_side(stoploss)
        if side not in {"BUY", "SELL"}:
            continue
        state = ensure(_row_orderbook_id(stoploss), _row_stock(stoploss))
        volume_key = "active_buy_volume" if side == "BUY" else "active_sell_volume"
        count_key = "active_buy_count" if side == "BUY" else "active_sell_count"
        state[volume_key] = _normalized_number(
            state[volume_key] + _row_volume(stoploss)
        )
        state[count_key] += 1
        if side == "SELL":
            state["active_sell_rows"].append(
                {
                    "stop_loss_id": _row_stop_loss_id(stoploss) or None,
                    "antal": _row_volume(stoploss),
                }
            )

    for order in open_orders:
        if _row_account_id(order) not in {"", account_token}:
            continue
        if not _is_active_open_order(order):
            continue
        side = _row_side(order)
        if side not in {"BUY", "SELL"}:
            continue
        state = ensure(_row_orderbook_id(order), _row_stock(order))
        volume_key = "open_buy_volume" if side == "BUY" else "open_sell_volume"
        count_key = "open_buy_count" if side == "BUY" else "open_sell_count"
        state[volume_key] = _normalized_number(state[volume_key] + _row_volume(order))
        state[count_key] += 1

    for state in states.values():
        state["active_sell_rows"].sort(
            key=lambda row: (str(row.get("stop_loss_id") or ""), row["antal"])
        )
    return [states[key] for key in sorted(states)]


def _empty_registry() -> dict[str, Any]:
    now = _utc_timestamp()
    return {
        "version": REGISTRY_VERSION,
        "created_at": now,
        "updated_at": now,
        "accounts": {},
    }


class PositionStrategyRegistry:
    """Atomic file-backed registry for reviewed per-position strategy plans."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.load_error = ""
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_registry()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("registry root must be an object")
            if int(payload.get("version", -1)) != REGISTRY_VERSION:
                raise ValueError(
                    f"unsupported registry version {payload.get('version')!r}"
                )
            if not isinstance(payload.get("accounts"), dict):
                raise ValueError("registry accounts must be an object")
            return payload
        except Exception as exc:
            self.load_error = str(exc)
            return _empty_registry()

    def _ensure_writable(self) -> None:
        if self.load_error:
            raise RuntimeError(
                "Position strategy registry could not be loaded; refusing to "
                f"overwrite it: {self.load_error}"
            )

    def _save_locked(self) -> None:
        self._ensure_writable()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data["updated_at"] = _utc_timestamp()
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_path = Path(handle.name)
        try:
            with handle:
                json.dump(self._data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _account_positions_locked(
        self,
        account_id: str,
        *,
        create: bool = False,
    ) -> dict[str, dict[str, Any]]:
        token = str(account_id or "").strip()
        if not token:
            raise ValueError("account_id is required.")
        accounts = self._data.setdefault("accounts", {})
        account = accounts.get(token)
        if not isinstance(account, dict):
            if not create:
                return {}
            account = {"positions": {}}
            accounts[token] = account
        positions = account.get("positions")
        if not isinstance(positions, dict):
            if not create:
                return {}
            positions = {}
            account["positions"] = positions
        return positions

    def health(self) -> dict[str, Any]:
        with self._lock:
            accounts = self._data.get("accounts")
            accounts = accounts if isinstance(accounts, dict) else {}
            entry_count = sum(
                len(account.get("positions", {}))
                for account in accounts.values()
                if isinstance(account, dict)
                and isinstance(account.get("positions"), dict)
            )
            return {
                "available": not bool(self.load_error),
                "load_error": self.load_error or None,
                "path": str(self.path),
                "version": REGISTRY_VERSION,
                "entry_count": entry_count,
                "updated_at": self._data.get("updated_at"),
            }

    def ensure_writable(self) -> None:
        with self._lock:
            self._ensure_writable()

    def lookup(self, account_id: str, orderbook_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._account_positions_locked(account_id).get(
                str(orderbook_id or "").strip()
            )
            return deepcopy(entry) if isinstance(entry, dict) else None

    def _entry_from_candidate(
        self,
        candidate: dict[str, Any],
        *,
        tenant_session_id: str | None,
        source: str,
        existing: dict[str, Any] | None,
    ) -> dict[str, Any]:
        live_state = candidate.get("live_state")
        if not isinstance(live_state, dict):
            raise ValueError("Every registry candidate requires a live_state.")
        live_fingerprint = position_strategy_live_fingerprint(live_state)
        fingerprint = live_fingerprint
        missing_identity = [
            field
            for field in ("account_id", "orderbook_id")
            if not fingerprint.get(field)
        ]
        if missing_identity:
            raise ValueError(
                "Cannot register position strategy without "
                + ", ".join(missing_identity)
                + "."
            )

        requested_exception = _normalize_audit_exception(
            candidate.get("audit_exception")
        )
        audit_exception = requested_exception
        if bool(candidate.get("preserve_audit_exception_fingerprint", False)):
            if not isinstance(existing, dict):
                raise ValueError(
                    "preserve_audit_exception_fingerprint requires an existing "
                    "reviewed registry entry."
                )
            existing_exception_payload = existing.get("audit_exception")
            existing_exception = _normalize_audit_exception(
                existing_exception_payload
            )
            if existing_exception is None:
                raise ValueError(
                    "preserve_audit_exception_fingerprint requires an existing "
                    "holding-only audit_exception."
                )
            if (
                not isinstance(existing_exception_payload, dict)
                or existing_exception_payload.get("rebaseline_authorized")
                is not False
                or set(existing_exception.get("allowed_mismatches", [])) != {"holding"}
            ):
                raise ValueError(
                    "The existing audit_exception must explicitly allow holding "
                    "drift only and set rebaseline_authorized=false."
                )
            recorded_fingerprint = position_strategy_live_fingerprint(existing)
            drift_fields = [
                field
                for field in _LIVE_STATE_FIELDS
                if recorded_fingerprint.get(field) != live_fingerprint.get(field)
            ]
            if drift_fields != ["holding"]:
                rendered = ", ".join(drift_fields) if drift_fields else "none"
                raise ValueError(
                    "preserve_audit_exception_fingerprint requires exact "
                    "holding-only live drift; found: " + rendered + "."
                )
            if (
                requested_exception is not None
                and requested_exception != existing_exception
            ):
                raise ValueError(
                    "preserve_audit_exception_fingerprint cannot change the "
                    "existing audit_exception."
                )
            fingerprint = recorded_fingerprint
            audit_exception = existing_exception

        plan: dict[str, Any] = {}
        for field in _REQUIRED_PLAN_TEXT_FIELDS:
            value = str(candidate.get(field) or "").strip()
            if not value:
                raise ValueError(f"{field} is required.")
            plan[field] = value
        for field in _OPTIONAL_PLAN_TEXT_FIELDS:
            value = str(candidate.get(field) or "").strip()
            plan[field] = value or None
        no_stop_status, normalized_no_stop, _ = (
            _no_stop_exception_evidence_evaluation(
                candidate.get("no_stop_exception_evidence")
            )
            if candidate.get("no_stop_exception_evidence") is not None
            else ("NOT_APPLICABLE", None, [])
        )
        if candidate.get("no_stop_exception_evidence") is not None and (
            no_stop_status != "CURRENT"
        ):
            _, _, issues = _no_stop_exception_evidence_evaluation(
                candidate.get("no_stop_exception_evidence")
            )
            raise ValueError(
                "Invalid no_stop_exception_evidence: " + "; ".join(issues) + "."
            )
        plan["no_stop_exception_evidence"] = (
            normalized_no_stop
            if no_stop_status != "NOT_APPLICABLE"
            else None
        )
        non_stop_status, normalized_non_stop, _ = (
            _non_stop_eligible_evidence_evaluation(
                candidate.get("non_stop_eligible_evidence")
            )
            if candidate.get("non_stop_eligible_evidence") is not None
            else ("NOT_APPLICABLE", None, [])
        )
        if candidate.get("non_stop_eligible_evidence") is not None and (
            non_stop_status != "CURRENT"
        ):
            _, _, issues = _non_stop_eligible_evidence_evaluation(
                candidate.get("non_stop_eligible_evidence")
            )
            raise ValueError(
                "Invalid non_stop_eligible_evidence: " + "; ".join(issues) + "."
            )
        plan["non_stop_eligible_evidence"] = (
            normalized_non_stop
            if non_stop_status != "NOT_APPLICABLE"
            else None
        )
        target = candidate.get("protection_target_antal")
        retained = candidate.get("retained_core_antal")
        plan["protection_target_antal"] = (
            _normalized_number(target)
            if not isinstance(target, bool) and scalar_number(target) is not None
            else None
        )
        plan["retained_core_antal"] = (
            _normalized_number(retained)
            if not isinstance(retained, bool) and scalar_number(retained) is not None
            else None
        )
        priority = plan["priority"].upper()
        if priority not in {"A", "B", "C", "D", "E"}:
            raise ValueError("priority must be one of A, B, C, D, or E.")
        plan["priority"] = priority
        plan["protection_classification"] = _normalized_token(
            plan["protection_classification"]
        )
        protection_status, protection_issues = _position_protection_evaluation(
            plan,
            live_fingerprint,
        )
        if protection_status in {
            POSITION_PROTECTION_MISSING,
            POSITION_PROTECTION_INVALID,
            POSITION_PROTECTION_CONTRADICTION,
        }:
            raise ValueError(
                "Invalid protection plan: " + "; ".join(protection_issues) + "."
            )

        now = _utc_timestamp()
        return {
            **fingerprint,
            **plan,
            "broker_instrument": _row_stock(live_state),
            "proposed_correction": (
                str(candidate.get("proposed_correction")).strip()
                if candidate.get("proposed_correction") is not None
                else None
            ),
            "audit_exception": audit_exception,
            "source_snapshot_at": (
                str(candidate.get("source_snapshot_at") or "").strip() or None
            ),
            "tenant_session_id": str(tenant_session_id or "").strip() or None,
            "source": str(source or "UNKNOWN").strip().upper(),
            "recorded_at": (
                existing.get("recorded_at")
                if isinstance(existing, dict) and existing.get("recorded_at")
                else now
            ),
            "updated_at": now,
        }

    def _prepare_many_locked(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        tenant_session_id: str | None,
        source: str,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        self._ensure_writable()
        prepared: list[tuple[str, str, dict[str, Any]]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in rows:
            live_state = candidate.get("live_state")
            if not isinstance(live_state, dict):
                raise ValueError("Every registry candidate requires a live_state.")
            fingerprint = position_strategy_live_fingerprint(live_state)
            key = (fingerprint["account_id"], fingerprint["orderbook_id"])
            if key in seen:
                raise ValueError(
                    "Duplicate registry candidate for account "
                    f"{key[0]} orderbook {key[1]}."
                )
            seen.add(key)
            current = self._account_positions_locked(key[0]).get(key[1])
            entry = self._entry_from_candidate(
                candidate,
                tenant_session_id=tenant_session_id,
                source=source,
                existing=current,
            )
            prepared.append((key[0], key[1], entry))
        return prepared

    def preview_many_existing(
        self,
        candidates: Iterable[dict[str, Any]],
        *,
        tenant_session_id: str | None,
        source: str,
    ) -> list[dict[str, Any]]:
        """Validate a reviewed batch without changing the registry file."""

        rows = list(candidates)
        with self._lock:
            prepared = self._prepare_many_locked(
                rows,
                tenant_session_id=tenant_session_id,
                source=source,
            )
            return [deepcopy(entry) for _, _, entry in prepared]

    def register_many_existing(
        self,
        candidates: Iterable[dict[str, Any]],
        *,
        tenant_session_id: str | None,
        source: str,
    ) -> list[dict[str, Any]]:
        """Validate every reviewed plan, then persist in one atomic write."""

        rows = list(candidates)
        with self._lock:
            prepared = self._prepare_many_locked(
                rows,
                tenant_session_id=tenant_session_id,
                source=source,
            )

            for account_id, orderbook_id, entry in prepared:
                self._account_positions_locked(account_id, create=True)[
                    orderbook_id
                ] = entry
            if prepared:
                self._save_locked()
            return [deepcopy(entry) for _, _, entry in prepared]

    def enrich(self, live_state: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(live_state)
        fingerprint = position_strategy_live_fingerprint(live_state)
        if self.load_error:
            enriched.update(
                {
                    "position_strategy_status": POSITION_STRATEGY_REGISTRY_UNAVAILABLE,
                    "position_strategy_mismatches": [],
                    "position_strategy": None,
                    "position_protection_status": POSITION_PROTECTION_REGISTRY_UNAVAILABLE,
                    "position_protection_issues": [],
                }
            )
            return enriched

        entry = self.lookup(
            fingerprint["account_id"],
            fingerprint["orderbook_id"],
        )
        if entry is None:
            enriched.update(
                {
                    "position_strategy_status": POSITION_STRATEGY_MISSING,
                    "position_strategy_mismatches": [],
                    "position_strategy": None,
                    "position_protection_status": POSITION_PROTECTION_MISSING,
                    "position_protection_issues": ["position_strategy"],
                }
            )
            return enriched

        mismatches = [
            field
            for field in _LIVE_STATE_FIELDS
            if entry.get(field) != fingerprint.get(field)
        ]
        protection_status, protection_issues = _position_protection_evaluation(
            entry,
            live_state,
        )
        enriched.update(
            {
                "position_strategy_status": (
                    POSITION_STRATEGY_RECORDED
                    if not mismatches
                    else POSITION_STRATEGY_STALE_MISMATCH
                ),
                "position_strategy_mismatches": mismatches,
                "position_protection_status": protection_status,
                "position_protection_issues": protection_issues,
                "position_strategy": {
                    field: entry.get(field)
                    for field in (
                        *_PLAN_TEXT_FIELDS,
                        *_TYPED_PLAN_FIELDS,
                        "proposed_correction",
                        "audit_exception",
                        "source",
                        "source_snapshot_at",
                        "recorded_at",
                        "updated_at",
                    )
                },
                "recorded_live_state": {
                    field: entry.get(field) for field in _LIVE_STATE_FIELDS
                },
            }
        )
        exception = entry.get("audit_exception")
        mismatch_fields = set(mismatches)
        allowed_fields = (
            set(exception.get("allowed_mismatches", []))
            if isinstance(exception, dict)
            else set()
        )
        enriched["position_strategy_exception_status"] = (
            "ACKNOWLEDGED_INTENTIONAL_DRIFT"
            if exception and mismatch_fields and mismatch_fields.issubset(allowed_fields)
            else None
        )
        return enriched

    def reconcile_account(
        self,
        account_id: str,
        positions: Iterable[dict[str, Any]],
        stoplosses: Iterable[dict[str, Any]],
        open_orders: Iterable[dict[str, Any]],
        *,
        prune_stale: bool = False,
    ) -> dict[str, Any]:
        account_token = str(account_id or "").strip()
        states = build_position_strategy_live_states(
            account_token,
            positions,
            stoplosses,
            open_orders,
        )
        enriched = [self.enrich(state) for state in states]
        for row in enriched:
            row["broker_sell_protection"] = _broker_sell_protection_evaluation(
                row
            )
        missing = [
            row
            for row in enriched
            if row.get("position_strategy_status") == POSITION_STRATEGY_MISSING
        ]
        mismatches = [
            row
            for row in enriched
            if row.get("position_strategy_status")
            == POSITION_STRATEGY_STALE_MISMATCH
        ]
        unavailable = [
            row
            for row in enriched
            if row.get("position_strategy_status")
            == POSITION_STRATEGY_REGISTRY_UNAVAILABLE
        ]
        recorded = [
            row
            for row in enriched
            if row.get("position_strategy_status") == POSITION_STRATEGY_RECORDED
        ]
        live_ids = {row["orderbook_id"] for row in states}
        with self._lock:
            planned_ids = set(self._account_positions_locked(account_token))
        stale_ids = sorted(planned_ids - live_ids)
        pruned_ids: list[str] = []
        if prune_stale and stale_ids:
            with self._lock:
                self._ensure_writable()
                account_positions = self._account_positions_locked(account_token)
                for orderbook_id in stale_ids:
                    del account_positions[orderbook_id]
                self._save_locked()
                pruned_ids = stale_ids
                stale_ids = []

        holding_drift = [
            row
            for row in mismatches
            if "holding" in row.get("position_strategy_mismatches", [])
        ]
        stop_drift = [
            row
            for row in mismatches
            if _STOP_EXPOSURE_FIELDS.intersection(
                row.get("position_strategy_mismatches", [])
            )
        ]
        open_order_drift = [
            row
            for row in mismatches
            if _OPEN_ORDER_EXPOSURE_FIELDS.intersection(
                row.get("position_strategy_mismatches", [])
            )
        ]
        acknowledged_mismatches = [
            row
            for row in mismatches
            if row.get("position_strategy_exception_status")
            == "ACKNOWLEDGED_INTENTIONAL_DRIFT"
        ]
        unresolved_mismatches = [
            row for row in mismatches if row not in acknowledged_mismatches
        ]
        protection_missing = [
            row
            for row in enriched
            if row.get("position_protection_status") == POSITION_PROTECTION_MISSING
        ]
        protection_invalid = [
            row
            for row in enriched
            if row.get("position_protection_status") == POSITION_PROTECTION_INVALID
        ]
        protection_contradictions = [
            row
            for row in enriched
            if row.get("position_protection_status")
            == POSITION_PROTECTION_CONTRADICTION
        ]
        protection_repairs = [
            row
            for row in enriched
            if row.get("position_protection_status")
            == POSITION_PROTECTION_REPAIR_REQUIRED
        ]
        protection_unavailable = [
            row
            for row in enriched
            if row.get("position_protection_status")
            == POSITION_PROTECTION_REGISTRY_UNAVAILABLE
        ]
        protection_classification_complete = not (
            protection_missing
            or protection_invalid
            or protection_contradictions
            or protection_repairs
            or protection_unavailable
        )
        material_stop_eligible = [
            row
            for row in enriched
            if row["broker_sell_protection"]["material_stop_eligible"]
        ]
        broker_sell_present = [
            row
            for row in material_stop_eligible
            if row["broker_sell_protection"]["broker_sell_present"]
        ]
        broker_sell_fully_protected = [
            row
            for row in material_stop_eligible
            if row["broker_sell_protection"]["broker_sell_fully_protected"]
        ]
        zero_sell_material = [
            row
            for row in material_stop_eligible
            if row["broker_sell_protection"][
                "broker_protection_review_required"
            ]
        ]
        no_stop_evidence_incomplete = [
            row
            for row in zero_sell_material
            if row["broker_sell_protection"].get(
                "no_stop_exception_evidence_complete"
            )
            is False
        ]
        non_stop_evidence_incomplete = [
            row
            for row in enriched
            if _normalized_token(
                (row.get("position_strategy") or {}).get(
                    "protection_classification"
                )
            )
            == "NON_STOP_ELIGIBLE"
            and row["broker_sell_protection"].get(
                "non_stop_eligible_evidence_complete"
            )
            is not True
        ]
        strategy_target_mismatches = [
            row
            for row in material_stop_eligible
            if row["broker_sell_protection"].get("broker_sell_present")
            and row["broker_sell_protection"].get(
                "strategy_target_coverage_status"
            )
            != "MATCHED"
        ]
        active_sell_row_count = sum(
            int(row.get("active_sell_count") or 0) for row in enriched
        )
        active_sell_positions = [
            row
            for row in enriched
            if int(row.get("active_sell_count") or 0) > 0
            or float(row.get("active_sell_volume") or 0.0) > 0
        ]
        position_coverage_percent = (
            round(
                100.0
                * len(broker_sell_present)
                / len(material_stop_eligible),
                4,
            )
            if material_stop_eligible
            else None
        )
        broker_sell_presence_complete = not zero_sell_material
        broker_sell_protection_complete = (
            len(broker_sell_fully_protected) == len(material_stop_eligible)
        )
        strategy_target_coverage_complete = not strategy_target_mismatches
        if not material_stop_eligible:
            broker_coverage_status = "NOT_APPLICABLE"
        elif zero_sell_material:
            broker_coverage_status = "BROKER_SELL_PROTECTION_ABSENT"
        elif broker_sell_protection_complete:
            broker_coverage_status = "BROKER_SELL_PROTECTION_PRESENT"
        else:
            broker_coverage_status = "BROKER_SELL_PROTECTION_PARTIAL"
        broker_review_status = (
            "PROTECTION_REVIEW_REQUIRED"
            if zero_sell_material or strategy_target_mismatches
            else "CURRENT"
        )
        protection_complete = bool(
            protection_classification_complete
            and broker_sell_presence_complete
            and strategy_target_coverage_complete
        )
        strict_fingerprint_complete = not (
            missing or mismatches or unavailable or stale_ids
        )
        protection_classification_governance_complete = bool(
            protection_classification_complete
            and not missing
            and not unavailable
            and not stale_ids
            and not unresolved_mismatches
        )
        governance_complete = bool(
            protection_classification_governance_complete
            and broker_sell_presence_complete
            and strategy_target_coverage_complete
        )
        complete = bool(strict_fingerprint_complete and protection_complete)
        return {
            "account_id": account_token,
            "complete": complete,
            "review_required": not complete,
            "strict_fingerprint_complete": strict_fingerprint_complete,
            "governance_complete": governance_complete,
            "governance_review_eligible": governance_complete,
            "protection_classification_complete": (
                protection_classification_complete
            ),
            "protection_classification_governance_complete": (
                protection_classification_governance_complete
            ),
            "broker_sell_protected": broker_sell_presence_complete,
            "broker_sell_presence_complete": broker_sell_presence_complete,
            "broker_sell_protection_complete": (
                broker_sell_protection_complete
            ),
            "strategy_target_coverage_complete": (
                strategy_target_coverage_complete
            ),
            "broker_protection_review_required": bool(zero_sell_material),
            "protection_review_required": not protection_complete,
            "protection_complete": protection_complete,
            "broker_sell_protection": {
                "assessment_status": "ASSESSED",
                "coverage_status": broker_coverage_status,
                "review_status": broker_review_status,
                "eligible_position_count": len(material_stop_eligible),
                "covered_position_count": len(broker_sell_present),
                "material_stop_eligible_position_count": len(
                    material_stop_eligible
                ),
                "positions_with_active_sell_count": len(
                    broker_sell_present
                ),
                "fully_protected_position_count": len(
                    broker_sell_fully_protected
                ),
                "strategy_target_matched_position_count": sum(
                    row["broker_sell_protection"].get(
                        "strategy_target_coverage_status"
                    )
                    == "MATCHED"
                    for row in material_stop_eligible
                ),
                "strategy_target_mismatch_position_count": len(
                    strategy_target_mismatches
                ),
                "active_sell_position_count": len(active_sell_positions),
                "active_sell_row_count": active_sell_row_count,
                "active_sell_positions": [
                    {
                        "account_id": row.get("account_id"),
                        "orderbook_id": row.get("orderbook_id"),
                        "stock": row.get("stock"),
                        "held_antal": row.get("holding"),
                        "active_sell_row_count": row.get(
                            "active_sell_count"
                        ),
                        "active_sell_antal": row.get("active_sell_volume"),
                        "protection_target_antal": row[
                            "broker_sell_protection"
                        ]["protection_target_antal"],
                        "retained_core_antal": row["broker_sell_protection"][
                            "retained_core_antal"
                        ],
                        "strategy_target_coverage_status": row[
                            "broker_sell_protection"
                        ]["strategy_target_coverage_status"],
                        "active_sell_rows": deepcopy(
                            row.get("active_sell_rows") or []
                        ),
                    }
                    for row in active_sell_positions
                ],
                "position_coverage_percent": position_coverage_percent,
                "zero_sell_material_positions": [
                    {
                        "orderbook_id": row.get("orderbook_id"),
                        "holding_antal": row["broker_sell_protection"][
                            "held_antal"
                        ],
                    }
                    for row in zero_sell_material
                ],
                "per_instrument": [
                    {
                        "orderbook_id": row.get("orderbook_id"),
                        "holding_antal": row["broker_sell_protection"][
                            "held_antal"
                        ],
                        "active_sell_row_count": row[
                            "broker_sell_protection"
                        ]["active_sell_row_count"],
                        "active_sell_antal": row[
                            "broker_sell_protection"
                        ]["active_sell_antal"],
                        "protected_antal": row[
                            "broker_sell_protection"
                        ]["protected_antal"],
                        "protection_target_antal": row[
                            "broker_sell_protection"
                        ]["protection_target_antal"],
                        "retained_core_antal": row["broker_sell_protection"][
                            "retained_core_antal"
                        ],
                        "strategy_target_coverage_status": row[
                            "broker_sell_protection"
                        ]["strategy_target_coverage_status"],
                        "no_stop_exception_evidence_status": row[
                            "broker_sell_protection"
                        ]["no_stop_exception_evidence_status"],
                        "no_stop_exception_decision_current": row[
                            "broker_sell_protection"
                        ]["no_stop_exception_decision_current"],
                        "no_stop_exception_protection_choice": row[
                            "broker_sell_protection"
                        ]["no_stop_exception_protection_choice"],
                        "no_stop_exception_protection_action_required": row[
                            "broker_sell_protection"
                        ]["no_stop_exception_protection_action_required"],
                        "no_stop_exception_evidence": deepcopy(
                            (row.get("position_strategy") or {}).get(
                                "no_stop_exception_evidence"
                            )
                        ),
                        "active_sell_rows": deepcopy(
                            row.get("active_sell_rows") or []
                        ),
                    }
                    for row in material_stop_eligible
                ],
                "material_positions": [
                    {
                        "account_id": row.get("account_id"),
                        "orderbook_id": row.get("orderbook_id"),
                        "stock": row.get("stock"),
                        "status": row["broker_sell_protection"]["status"],
                        "held_antal": row["broker_sell_protection"][
                            "held_antal"
                        ],
                        "active_sell_row_count": row[
                            "broker_sell_protection"
                        ]["active_sell_row_count"],
                        "active_sell_antal": row["broker_sell_protection"][
                            "active_sell_antal"
                        ],
                        "protected_antal": row["broker_sell_protection"][
                            "protected_antal"
                        ],
                        "unprotected_antal": row[
                            "broker_sell_protection"
                        ]["unprotected_antal"],
                        "protection_target_antal": row[
                            "broker_sell_protection"
                        ]["protection_target_antal"],
                        "retained_core_antal": row["broker_sell_protection"][
                            "retained_core_antal"
                        ],
                        "strategy_target_coverage_status": row[
                            "broker_sell_protection"
                        ]["strategy_target_coverage_status"],
                        "active_sell_rows": deepcopy(
                            row.get("active_sell_rows") or []
                        ),
                    }
                    for row in material_stop_eligible
                ],
                "verified_non_stop_eligible_position_count": sum(
                    row["broker_sell_protection"].get(
                        "non_stop_eligible_verified"
                    )
                    is True
                    for row in enriched
                ),
                "verified_non_stop_eligible_positions": [
                    {
                        "account_id": row.get("account_id"),
                        "orderbook_id": row.get("orderbook_id"),
                        "stock": row.get("stock"),
                        "holding_antal": row.get("holding"),
                        "non_stop_eligible_evidence_status": row[
                            "broker_sell_protection"
                        ]["non_stop_eligible_evidence_status"],
                        "non_stop_eligible_evidence": deepcopy(
                            (row.get("position_strategy") or {}).get(
                                "non_stop_eligible_evidence"
                            )
                        ),
                    }
                    for row in enriched
                    if row["broker_sell_protection"].get(
                        "non_stop_eligible_verified"
                    )
                    is True
                ],
                "coverage_semantics": (
                    "POSITION_COUNT_AND_PER_ORDERBOOK_ANTAL_ONLY"
                ),
                "cross_instrument_antal_aggregated": False,
            },
            "zero_sell_material_position_count": len(zero_sell_material),
            "zero_sell_material_positions": [
                {
                    "account_id": row.get("account_id"),
                    "orderbook_id": row.get("orderbook_id"),
                    "stock": row.get("stock"),
                    "held_antal": row.get("holding"),
                    "active_sell_row_count": row.get("active_sell_count"),
                    "active_sell_antal": row.get("active_sell_volume"),
                    "protection_classification": (
                        (row.get("position_strategy") or {}).get(
                            "protection_classification"
                        )
                    ),
                    "position_protection_status": row.get(
                        "position_protection_status"
                    ),
                    "no_stop_exception_evidence_status": row[
                        "broker_sell_protection"
                    ]["no_stop_exception_evidence_status"],
                    "no_stop_exception_evidence_complete": row[
                        "broker_sell_protection"
                    ]["no_stop_exception_evidence_complete"],
                    "no_stop_exception_evidence_issues": row[
                        "broker_sell_protection"
                    ]["no_stop_exception_evidence_issues"],
                    "no_stop_exception_decision_current": row[
                        "broker_sell_protection"
                    ]["no_stop_exception_decision_current"],
                    "no_stop_exception_protection_choice": row[
                        "broker_sell_protection"
                    ]["no_stop_exception_protection_choice"],
                    "no_stop_exception_protection_action_required": row[
                        "broker_sell_protection"
                    ]["no_stop_exception_protection_action_required"],
                    "no_stop_exception_evidence": deepcopy(
                        (row.get("position_strategy") or {}).get(
                            "no_stop_exception_evidence"
                        )
                    ),
                }
                for row in zero_sell_material
            ],
            "no_stop_exception_evidence_incomplete_count": len(
                no_stop_evidence_incomplete
            ),
            "no_stop_exception_evidence_incomplete_orderbook_ids": [
                row["orderbook_id"] for row in no_stop_evidence_incomplete
            ],
            "non_stop_eligible_evidence_incomplete_count": len(
                non_stop_evidence_incomplete
            ),
            "non_stop_eligible_evidence_incomplete_orderbook_ids": [
                row["orderbook_id"] for row in non_stop_evidence_incomplete
            ],
            "strategy_target_coverage_mismatch_count": len(
                strategy_target_mismatches
            ),
            "strategy_target_coverage_mismatch_orderbook_ids": [
                row["orderbook_id"] for row in strategy_target_mismatches
            ],
            "row_count": len(enriched),
            "planned_count": len(planned_ids) - len(pruned_ids),
            "recorded_count": len(recorded),
            "missing_count": len(missing),
            "mismatch_count": len(mismatches),
            "registry_unavailable_count": len(unavailable),
            "stale_plan_count": len(stale_ids),
            "holding_drift_count": len(holding_drift),
            "stop_exposure_drift_count": len(stop_drift),
            "open_order_drift_count": len(open_order_drift),
            "acknowledged_mismatch_count": len(acknowledged_mismatches),
            "unresolved_mismatch_count": len(unresolved_mismatches),
            "protection_missing_count": len(protection_missing),
            "protection_invalid_count": len(protection_invalid),
            "protection_contradiction_count": len(protection_contradictions),
            "protection_repair_required_count": len(protection_repairs),
            "protection_registry_unavailable_count": len(protection_unavailable),
            "acknowledged_mismatch_orderbook_ids": [
                row["orderbook_id"] for row in acknowledged_mismatches
            ],
            "unresolved_mismatch_orderbook_ids": [
                row["orderbook_id"] for row in unresolved_mismatches
            ],
            "protection_missing_orderbook_ids": [
                row["orderbook_id"] for row in protection_missing
            ],
            "protection_invalid_orderbook_ids": [
                row["orderbook_id"] for row in protection_invalid
            ],
            "protection_contradiction_orderbook_ids": [
                row["orderbook_id"] for row in protection_contradictions
            ],
            "protection_repair_required_orderbook_ids": [
                row["orderbook_id"] for row in protection_repairs
            ],
            "protection_registry_unavailable_orderbook_ids": [
                row["orderbook_id"] for row in protection_unavailable
            ],
            "missing_orderbook_ids": [row["orderbook_id"] for row in missing],
            "mismatched_orderbook_ids": [
                row["orderbook_id"] for row in mismatches
            ],
            "stale_plan_orderbook_ids": stale_ids,
            "pruned_count": len(pruned_ids),
            "pruned_orderbook_ids": pruned_ids,
            "positions": enriched,
            "registry": self.health(),
        }
