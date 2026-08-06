from scripts.build_catalyst_coverage_audit import build_audit
from scripts.verify_catalyst_coverage_artifacts import validate


def test_catalyst_audit_keeps_unverified_date_and_live_refresh_open():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")

    assert validate(payload) == []
    assert payload["validation"]["verified_upcoming_rows"] == 21
    assert payload["validation"]["unverified_upcoming_rows"] == 1
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
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")
    payload["validation"]["unverified_not_waiting_rows"] = 1

    errors = validate(payload)

    assert any("unverified catalyst row is promoted" in error for error in errors)


def test_catalyst_audit_rejects_live_authority():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")
    payload["authority"]["trade_authority"] = True

    errors = validate(payload)

    assert any("trade_authority" in error for error in errors)
