"""A preserved cross-midnight failure is history, never eligible progress."""

from datetime import datetime, timedelta

import pytest

from scripts.verify_governance_review_streak import validate
from tests.test_governance_review_streak import annotation, ledger, review


def late_failed_ledger():
    payload = ledger()
    row = review(17, "EVENING", eligible=False)
    row["completed_at"] = (datetime.fromisoformat(row["scheduled_at"]) + timedelta(hours=18)).isoformat()
    payload["reviews"] = [row]
    payload["timing_annotations"] = [annotation(row, 0, "PRESERVED_LATE_FAILED_ATTEMPT")]
    return payload


def test_preserved_cross_midnight_failure_is_structurally_valid_not_complete():
    payload = late_failed_ledger()
    assert validate(payload) == []
    assert validate(payload, require_complete=True)
    assert payload["reviews"][0]["eligible"] is False
    assert payload["current_consecutive_eligible_reviews"] == 0


@pytest.mark.parametrize("change", ["missing", "hash", "eligible", "scheduled_date"])
def test_cross_midnight_failure_cannot_bypass_annotation_contract(change):
    payload = late_failed_ledger()
    if change == "missing":
        payload.pop("timing_annotations")
    elif change == "hash":
        payload["timing_annotations"][0]["canonical_sha256"] = "0" * 64
    elif change == "eligible":
        payload["reviews"][0]["eligible"] = True
    else:
        payload["reviews"][0]["market_session_date"] = "2026-08-18"
    assert validate(payload)
