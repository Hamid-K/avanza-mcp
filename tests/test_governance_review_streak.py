from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scripts.verify_governance_review_streak import (
    REQUIRED_GATES,
    canonical_review_sha256,
    validate,
)


STOCKHOLM = ZoneInfo("Europe/Stockholm")


def ledger() -> dict:
    return {
        "artifact": "PORTFOLIO_GOVERNANCE_REVIEW_STREAK",
        "version": 1,
        "timezone": "Europe/Stockholm",
        "required_review_count": 10,
        "required_regular_session_count": 5,
        "status": "ACTIVE_NOT_COMPLETE",
        "completion_claim": False,
        "current_consecutive_eligible_reviews": 0,
        "current_regular_session_count": 0,
        "reviews": [],
    }


def review(day: int, window: str, *, eligible: bool = True) -> dict:
    hour = 7 if window == "MORNING" else 16
    scheduled = datetime(2026, 8, day, hour, tzinfo=STOCKHOLM)
    return {
        "review_id": f"2026-08-{day:02d}-{window.lower()}",
        "scheduled_at": scheduled.isoformat(),
        "completed_at": (scheduled + timedelta(minutes=8)).isoformat(),
        "window": window,
        "market_session_date": scheduled.date().isoformat(),
        "market_session_state": "REGULAR_SESSION",
        "accounts": [
            {
                "tenant_session_id": tenant,
                "account_id": account,
                "session_verified": eligible,
                "position_governance_complete": eligible,
                "stop_strategy_complete": eligible,
                "failed_order_count": 0,
                "authorization_off": True,
                "broker_sell_protection": {
                    "assessment_status": "ASSESSED" if eligible else "NOT_ASSESSED",
                    "coverage_status": "NOT_APPLICABLE" if eligible else "NOT_ASSESSED",
                    "review_status": "CURRENT" if eligible else "NOT_ASSESSED",
                    "active_sell_row_count": 0 if eligible else None,
                    "eligible_position_count": 0 if eligible else None,
                    "covered_position_count": 0 if eligible else None,
                    "zero_sell_material_positions": [] if eligible else None,
                    "per_instrument": [] if eligible else None,
                    "strategy_target_mismatch_position_count": (
                        0 if eligible else None
                    ),
                    "verified_non_stop_eligible_position_count": (
                        0 if eligible else None
                    ),
                    "verified_non_stop_eligible_positions": (
                        [] if eligible else None
                    ),
                    "coverage_semantics": (
                        "POSITION_COUNT_AND_PER_ORDERBOOK_ANTAL_ONLY"
                        if eligible
                        else None
                    ),
                    "cross_instrument_antal_aggregated": False if eligible else None,
                },
            }
            for tenant, account in (
                ("personal", "5227886"),
                ("darkcell", "7616265"),
            )
        ],
        "gates": {gate: eligible for gate in REQUIRED_GATES},
        "blockers": [] if eligible else ["position_governance_incomplete"],
        "evidence": ["exact scoped MCP review"] if eligible else [],
        "eligible": eligible,
    }


def annotation(row: dict, index: int, classification: str) -> dict:
    completed = datetime.fromisoformat(row["completed_at"])
    return {
        "annotation_id": f"annotation-{index}-{classification.lower()}",
        "review_index": index,
        "review_id": row["review_id"],
        "canonical_sha256": canonical_review_sha256(row),
        "classification": classification,
        "recorded_at": (completed + timedelta(minutes=1)).isoformat(),
        "reason": "Preserve the failed record without editing its evidence.",
        "preserves_eligible_false": True,
    }


def test_empty_active_streak_is_structurally_valid():
    assert validate(ledger()) == []


def test_legacy_missing_broker_sell_block_is_not_assessed_and_cannot_be_eligible():
    payload = ledger()
    row = review(17, "MORNING")
    for account in row["accounts"]:
        account.pop("broker_sell_protection")
        account["protection_complete"] = True
    payload["reviews"] = [row]
    payload["current_consecutive_eligible_reviews"] = 1
    payload["current_regular_session_count"] = 1

    errors = validate(payload)

    assert "reviews[0].eligible does not match its gate evidence" in errors


def test_legacy_missing_broker_sell_block_remains_structurally_valid_when_ineligible():
    payload = ledger()
    row = review(17, "MORNING")
    for account in row["accounts"]:
        account.pop("broker_sell_protection")
        account["protection_complete"] = True
    row["eligible"] = False
    row["blockers"] = ["broker SELL protection was not assessed"]
    payload["reviews"] = [row]

    assert validate(payload) == []


def test_material_zero_sell_rows_must_be_absent_review_required_and_ineligible():
    payload = ledger()
    row = review(17, "MORNING")
    personal = row["accounts"][0]
    personal["broker_sell_protection"] = {
        "assessment_status": "ASSESSED",
        "coverage_status": "BROKER_SELL_PROTECTION_ABSENT",
        "review_status": "PROTECTION_REVIEW_REQUIRED",
        "active_sell_row_count": 0,
        "eligible_position_count": 1,
        "covered_position_count": 0,
        "strategy_target_mismatch_position_count": 0,
        "verified_non_stop_eligible_position_count": 0,
        "verified_non_stop_eligible_positions": [],
        "zero_sell_material_positions": [
            {"orderbook_id": "1001", "holding": 12}
        ],
        "per_instrument": [
            {
                "orderbook_id": "1001",
                "holding_antal": 12,
                "active_sell_row_count": 0,
                "active_sell_antal": 0,
                "protected_antal": 0,
                "protection_target_antal": None,
                "retained_core_antal": None,
                "strategy_target_coverage_status": "NOT_APPLICABLE",
                "no_stop_exception_evidence_status": "CURRENT",
                "no_stop_exception_decision_current": True,
                "no_stop_exception_protection_choice": (
                    "TACTICAL_PROFIT_SLICE"
                ),
                "no_stop_exception_protection_action_required": True,
                "no_stop_exception_evidence": {
                    "decision_at": "2026-08-17T07:00:00+02:00",
                    "evidence_as_of": "2026-08-17T06:50:00+02:00",
                    "next_review_at": "2026-08-18T07:00:00+02:00",
                    "valid_until": "2026-08-19T07:00:00+02:00",
                    "gap_risk_statement": "No broker SELL row is present.",
                    "evidence_source_ids": ["broker-readback-1001"],
                    "protection_choice": "TACTICAL_PROFIT_SLICE",
                },
                "active_sell_rows": [],
            }
        ],
        "coverage_semantics": "POSITION_COUNT_AND_PER_ORDERBOOK_ANTAL_ONLY",
        "cross_instrument_antal_aggregated": False,
    }
    row["eligible"] = False
    row["blockers"] = ["Personal material position has no active SELL protection"]
    payload["reviews"] = [row]

    assert validate(payload) == []

    personal["broker_sell_protection"]["coverage_status"] = (
        "BROKER_SELL_PROTECTION_PRESENT"
    )
    errors = validate(payload)
    assert any(
        "material zero-SELL rows must be BROKER_SELL_PROTECTION_ABSENT"
        in error
        for error in errors
    )


def test_undercovered_strategy_target_is_review_required_and_streak_ineligible():
    payload = ledger()
    row = review(17, "MORNING")
    personal = row["accounts"][0]
    personal["broker_sell_protection"] = {
        "assessment_status": "ASSESSED",
        "coverage_status": "BROKER_SELL_PROTECTION_PARTIAL",
        "review_status": "PROTECTION_REVIEW_REQUIRED",
        "active_sell_row_count": 1,
        "eligible_position_count": 1,
        "covered_position_count": 1,
        "strategy_target_mismatch_position_count": 1,
        "verified_non_stop_eligible_position_count": 0,
        "verified_non_stop_eligible_positions": [],
        "zero_sell_material_positions": [],
        "per_instrument": [
            {
                "orderbook_id": "1001",
                "holding_antal": 10,
                "active_sell_row_count": 1,
                "active_sell_antal": 3,
                "protected_antal": 3,
                "protection_target_antal": 5,
                "retained_core_antal": 5,
                "strategy_target_coverage_status": "UNDERCOVERED",
                "no_stop_exception_evidence_status": "NOT_APPLICABLE",
                "no_stop_exception_decision_current": False,
                "no_stop_exception_protection_action_required": None,
                "active_sell_rows": [
                    {"stop_loss_id": "sell-1001", "antal": 3}
                ],
            }
        ],
        "coverage_semantics": "POSITION_COUNT_AND_PER_ORDERBOOK_ANTAL_ONLY",
        "cross_instrument_antal_aggregated": False,
    }
    row["eligible"] = False
    row["blockers"] = ["Personal strategy SELL target is undercovered"]
    payload["reviews"] = [row]

    assert validate(payload) == []

    personal["broker_sell_protection"]["review_status"] = "CURRENT"
    errors = validate(payload)
    assert any("coverage/review status contradicts" in error for error in errors)

    target_row = personal["broker_sell_protection"]["per_instrument"][0]
    target_row["strategy_target_coverage_status"] = "NOT_APPLICABLE"
    target_row["protection_target_antal"] = None
    target_row["retained_core_antal"] = None
    personal["broker_sell_protection"][
        "strategy_target_mismatch_position_count"
    ] = 0
    errors = validate(payload)
    assert any(
        "active SELL row cannot have NOT_APPLICABLE strategy target coverage"
        in error
        for error in errors
    )


def test_current_no_stop_label_rejects_elapsed_review_timestamp():
    payload = ledger()
    row = review(17, "MORNING")
    personal = row["accounts"][0]
    personal["broker_sell_protection"] = {
        "assessment_status": "ASSESSED",
        "coverage_status": "BROKER_SELL_PROTECTION_ABSENT",
        "review_status": "PROTECTION_REVIEW_REQUIRED",
        "active_sell_row_count": 0,
        "eligible_position_count": 1,
        "covered_position_count": 0,
        "strategy_target_mismatch_position_count": 0,
        "verified_non_stop_eligible_position_count": 0,
        "verified_non_stop_eligible_positions": [],
        "zero_sell_material_positions": ["1001"],
        "per_instrument": [
            {
                "orderbook_id": "1001",
                "holding_antal": 12,
                "active_sell_row_count": 0,
                "active_sell_antal": 0,
                "protected_antal": 0,
                "protection_target_antal": None,
                "retained_core_antal": None,
                "strategy_target_coverage_status": "NOT_APPLICABLE",
                "no_stop_exception_evidence_status": "CURRENT",
                "no_stop_exception_decision_current": True,
                "no_stop_exception_protection_choice": (
                    "DELIBERATELY_UNPROTECTED_CORE"
                ),
                "no_stop_exception_protection_action_required": False,
                "no_stop_exception_evidence": {
                    "decision_at": "2026-08-17T06:30:00+02:00",
                    "evidence_as_of": "2026-08-17T06:20:00+02:00",
                    "next_review_at": "2026-08-17T07:05:00+02:00",
                    "valid_until": "2026-08-17T08:00:00+02:00",
                    "gap_risk_statement": "No broker SELL row is present.",
                    "evidence_source_ids": ["broker-readback-1001"],
                    "protection_choice": "DELIBERATELY_UNPROTECTED_CORE",
                },
                "active_sell_rows": [],
            }
        ],
        "coverage_semantics": "POSITION_COUNT_AND_PER_ORDERBOOK_ANTAL_ONLY",
        "cross_instrument_antal_aggregated": False,
    }
    row["eligible"] = False
    row["blockers"] = ["Personal material position has no active SELL"]
    payload["reviews"] = [row]

    errors = validate(payload)
    assert any("next_review_at has elapsed" in error for error in errors)

    evidence = personal["broker_sell_protection"]["per_instrument"][0][
        "no_stop_exception_evidence"
    ]
    evidence["next_review_at"] = "2026-08-18T07:00:00+02:00"
    evidence["valid_until"] = "2026-08-19T07:00:00+02:00"
    evidence["gap_risk_statement"] = 123
    errors = validate(payload)
    assert any("gap_risk_statement must be nonblank" in error for error in errors)


def test_expired_non_stop_capability_evidence_cannot_validate_eligible_review():
    payload = ledger()
    row = review(17, "MORNING")
    personal = row["accounts"][0]["broker_sell_protection"]
    personal["verified_non_stop_eligible_position_count"] = 1
    personal["verified_non_stop_eligible_positions"] = [
        {
            "orderbook_id": "1001",
            "non_stop_eligible_evidence_status": "CURRENT",
            "non_stop_eligible_evidence": {
                "evidence_as_of": "2026-08-10T07:00:00+02:00",
                "valid_until": "2026-08-17T07:05:00+02:00",
                "capability_statement": "Stops were unavailable.",
                "evidence_source_ids": ["broker-capability-1001"],
            },
        }
    ]
    payload["reviews"] = [row]
    payload["current_consecutive_eligible_reviews"] = 1
    payload["current_regular_session_count"] = 1

    errors = validate(payload)
    assert any("valid_until has elapsed" in error for error in errors)

    evidence = personal["verified_non_stop_eligible_positions"][0][
        "non_stop_eligible_evidence"
    ]
    evidence["valid_until"] = "2026-08-25T07:05:00+02:00"
    evidence["capability_statement"] = 123
    errors = validate(payload)
    assert any("capability_statement must be nonblank" in error for error in errors)


def test_streak_rejects_missing_gate_and_false_eligibility_claim():
    payload = ledger()
    row = review(17, "MORNING")
    row["gates"].pop("raw_failed_orders")
    payload["reviews"] = [row]
    payload["current_consecutive_eligible_reviews"] = 1
    payload["current_regular_session_count"] = 1

    errors = validate(payload)

    assert "reviews[0].gates must contain the exact required gate set" in errors
    assert "reviews[0].eligible does not match its gate evidence" in errors


def test_ten_paired_eligible_reviews_complete_five_session_streak():
    payload = ledger()
    payload["reviews"] = [
        review(day, window)
        for day in range(17, 22)
        for window in ("MORNING", "EVENING")
    ]
    payload.update(
        {
            "status": "COMPLETE",
            "completion_claim": True,
            "current_consecutive_eligible_reviews": 10,
            "current_regular_session_count": 5,
        }
    )

    assert validate(payload, require_complete=True) == []


def test_failed_latest_regular_session_review_resets_streak():
    payload = ledger()
    payload["reviews"] = [
        review(day, window)
        for day in range(17, 22)
        for window in ("MORNING", "EVENING")
    ]
    payload["reviews"].append(review(24, "MORNING", eligible=False))

    assert validate(payload) == []
    assert "ten eligible reviews" in validate(payload, require_complete=True)[0]


def test_holiday_review_is_labeled_but_does_not_reset_regular_session_tail():
    payload = ledger()
    first = review(17, "MORNING")
    holiday = review(18, "MORNING", eligible=False)
    holiday["market_session_state"] = "HOLIDAY"
    payload["reviews"] = [first, holiday]
    payload["current_consecutive_eligible_reviews"] = 1
    payload["current_regular_session_count"] = 1

    assert validate(payload) == []


def test_streak_rejects_late_retroactive_record():
    payload = ledger()
    row = review(17, "MORNING")
    row["completed_at"] = (
        datetime.fromisoformat(row["scheduled_at"]) + timedelta(hours=7)
    ).isoformat()
    payload["reviews"] = [row]
    payload["current_consecutive_eligible_reviews"] = 1
    payload["current_regular_session_count"] = 1

    assert "recorded more than six hours" in " ".join(validate(payload))


def test_hash_bound_annotation_preserves_late_failed_attempt():
    payload = ledger()
    row = review(17, "MORNING", eligible=False)
    row["completed_at"] = (
        datetime.fromisoformat(row["scheduled_at"]) + timedelta(hours=7)
    ).isoformat()
    payload["reviews"] = [row]
    payload["timing_annotations"] = [
        annotation(row, 0, "PRESERVED_LATE_FAILED_ATTEMPT")
    ]

    assert validate(payload) == []


def test_timing_annotation_fails_closed_after_review_tampering():
    payload = ledger()
    row = review(17, "MORNING", eligible=False)
    row["completed_at"] = (
        datetime.fromisoformat(row["scheduled_at"]) + timedelta(hours=7)
    ).isoformat()
    payload["reviews"] = [row]
    payload["timing_annotations"] = [
        annotation(row, 0, "PRESERVED_LATE_FAILED_ATTEMPT")
    ]
    row["blockers"].append("later mutation")

    errors = validate(payload)
    assert any("canonical_sha256 does not match" in error for error in errors)
    assert any("recorded more than six hours" in error for error in errors)


def test_timing_annotation_cannot_cover_an_eligible_review():
    payload = ledger()
    row = review(17, "MORNING")
    row["completed_at"] = (
        datetime.fromisoformat(row["scheduled_at"]) + timedelta(hours=7)
    ).isoformat()
    payload["reviews"] = [row]
    payload["current_consecutive_eligible_reviews"] = 1
    payload["current_regular_session_count"] = 1
    payload["timing_annotations"] = [
        annotation(row, 0, "PRESERVED_LATE_FAILED_ATTEMPT")
    ]

    errors = validate(payload)
    assert any("cannot annotate an eligible review" in error for error in errors)
    assert any("recorded more than six hours" in error for error in errors)


def test_completion_claim_cannot_be_set_early():
    payload = ledger()
    payload["completion_claim"] = True

    assert "completion_claim does not match" in " ".join(validate(payload))


def test_duplicate_session_window_is_rejected():
    payload = ledger()
    first = review(17, "MORNING")
    second = deepcopy(first)
    second["review_id"] = "duplicate-window"
    second["completed_at"] = (
        datetime.fromisoformat(first["completed_at"]) + timedelta(minutes=1)
    ).isoformat()
    payload["reviews"] = [first, second]
    payload["current_consecutive_eligible_reviews"] = 2
    payload["current_regular_session_count"] = 1

    assert "duplicates a market-session review window" in " ".join(
        validate(payload)
    )


def test_hash_bound_annotations_preserve_out_of_window_duplicate_attempts():
    payload = ledger()
    first = review(17, "MORNING", eligible=False)
    first["scheduled_at"] = datetime(2026, 8, 17, 2, 26, tzinfo=STOCKHOLM).isoformat()
    first["completed_at"] = datetime(2026, 8, 17, 2, 31, tzinfo=STOCKHOLM).isoformat()
    first["market_session_state"] = "UNKNOWN"
    second = review(17, "MORNING", eligible=False)
    second["review_id"] = "2026-08-17-morning-scheduled"
    payload["reviews"] = [first, second]
    payload["timing_annotations"] = [
        annotation(first, 0, "PRESERVED_OUT_OF_WINDOW_HEARTBEAT"),
        annotation(second, 1, "PRESERVED_SEP3_MORNING_UNAVAILABLE_ATTEMPT"),
    ]

    assert validate(payload) == []
