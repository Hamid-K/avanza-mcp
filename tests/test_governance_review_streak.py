from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scripts.verify_governance_review_streak import REQUIRED_GATES, validate


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


def test_empty_active_streak_is_structurally_valid():
    assert validate(ledger()) == []


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
