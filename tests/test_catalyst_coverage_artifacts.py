from copy import deepcopy

from scripts import build_catalyst_coverage_audit as builder
from scripts.verify_catalyst_coverage_artifacts import validate


def test_catalyst_audit_accepts_verified_publication_migration_and_keeps_refresh_open():
    payload = builder.build_audit(generated_at="2026-08-20T12:00:00+02:00")

    assert validate(payload) == []
    assert payload["validation"]["verified_upcoming_rows"] == 21
    assert payload["validation"]["unverified_upcoming_rows"] == 0
    assert payload["validation"]["stale_unverified_rows"] == 0
    assert payload["validation"]["publication_state_current"] is True
    assert "CAT2" not in {row["id"] for row in payload["completion_blockers"]}
    assert payload["validation"]["technical_refresh_rows"] == 14
    assert any(
        row["ticker"] == "QBTS"
        for row in payload["validation"]["technical_lookup_failures"]
    )
    assert payload["freshness"]["latest_live_recheck_as_of"] == "2026-08-06T17:34:14+02:00"
    assert payload["freshness"]["latest_named_exception_recheck_as_of"] == "2026-08-06T17:49:03+02:00"
    latest = payload["validation"]["latest_live_recheck"]
    assert latest["authority"] == "READ_ONLY_REVIEW_EVIDENCE"
    assert {row["ticker"] for row in latest["rows"]} == {"AKAM", "QBTS", "RGTI", "CRWV"}
    assert {row["ticker"] for row in latest["lookup_failures"]} == {"ONTO", "NET", "GMED", "IONQ"}
    named = payload["validation"]["latest_named_exception_recheck"]
    assert named["authority"] == "READ_ONLY_REVIEW_EVIDENCE"
    assert {row["ticker"] for row in named["rows"]} == {"SPCX", "PLTR", "W", "NEM"}
    assert {row["ticker"] for row in named["lookup_failures"]} == {"SHOP"}
    official = payload["validation"]["latest_official_recheck"]
    assert payload["freshness"]["latest_official_recheck_as_of"] == "2026-08-06T17:57:25+02:00"
    assert official["authority"] == "READ_ONLY_OFFICIAL_ISSUER_REVIEW"
    assert {row["ticker"] for row in official["rows"]} == {"QBTS", "ONTO", "AKAM", "CGNX"}
    assert all(row["publication_verified"] is False for row in official["rows"])


def test_catalyst_audit_rejects_unverified_event_promotion():
    payload = builder.build_audit(generated_at="2026-08-20T12:00:00+02:00")
    payload["validation"]["unverified_not_waiting_rows"] = 1

    errors = validate(payload)

    assert any("unverified catalyst row is promoted" in error for error in errors)


def test_catalyst_audit_rejects_live_authority():
    payload = builder.build_audit(generated_at="2026-08-20T12:00:00+02:00")
    payload["authority"]["trade_authority"] = True

    errors = validate(payload)

    assert any("trade_authority" in error for error in errors)


def test_catalyst_audit_keeps_legitimate_unverified_date_fail_closed():
    catalyst = deepcopy(builder.read(builder.CATALYST))
    catalyst["unverified_upcoming"] = [
        {
            "ticker": "NEW",
            "event": "Issuer event date not yet published",
            "when_stockholm": "Date pending issuer confirmation",
            "status": "WAITING_OFFICIAL_DATE",
            "source": "https://example.test/investor-relations",
        }
    ]

    payload = builder.build_audit(
        generated_at="2026-08-20T12:00:00+02:00",
        catalyst_payload=catalyst,
    )

    assert validate(payload) == []
    assert payload["validation"]["unverified_upcoming_rows"] == 1
    assert "CAT2" in {row["id"] for row in payload["completion_blockers"]}


def test_catalyst_audit_blocks_unverified_row_superseded_by_publication():
    catalyst = deepcopy(builder.read(builder.CATALYST))
    catalyst["unverified_upcoming"] = [
        {
            "ticker": "SOUN",
            "event": "Stale estimated event",
            "when_stockholm": "Estimated",
            "status": "WAITING_OFFICIAL_DATE",
            "source": "https://example.test/estimate",
        }
    ]

    payload = builder.build_audit(
        generated_at="2026-08-20T12:00:00+02:00",
        catalyst_payload=catalyst,
    )

    assert payload["status"] == "BLOCKED_METADATA_OR_STATUS"
    assert payload["validation"]["stale_unverified_rows"] == 1
    assert "CAT7" in {row["id"] for row in payload["completion_blockers"]}
    assert any("verified publication" in error for error in validate(payload))
