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


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _aware_datetime(value: Any, label: str, errors: list[str]) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
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
                completed.astimezone(STOCKHOLM).date().isoformat() == session_date,
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
    print(
        "[governance-streak] PASS: "
        f"{payload['current_consecutive_eligible_reviews']}/10 eligible reviews; "
        f"status={payload['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
