from scripts.build_scheduler_coverage_audit import build_audit
from scripts.verify_scheduler_coverage_artifacts import validate


def test_scheduler_audit_preserves_canonical_queue_and_exposes_archive_gap():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")

    assert validate(payload) == []
    assert payload["validation"]["canonical_approval_c_rows"] == 18
    assert payload["validation"]["terminal_rows_in_active_section"] == 5
    assert payload["status"] == "VALIDATED_CONTRACT_WITH_ARCHIVE_GAP"
    assert payload["archive_proposal"]["status"] == "AWAITING_USER_SCHEDULER_AUTHORITY"
    assert len(payload["archive_proposal"]["rows"]) == 5
    assert payload["archive_proposal"]["preserve_planned_action_semantics"] is True


def test_scheduler_audit_rejects_unknown_status():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")
    payload["rows"][0]["status"] = "DONE"

    errors = validate(payload)

    assert any("invalid scheduler status" in error for error in errors)


def test_scheduler_audit_rejects_missing_live_blocker():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")
    payload["completion_blockers"] = [row for row in payload["completion_blockers"] if row["id"] != "SCH3"]

    errors = validate(payload)

    assert any("SCH3" in error for error in errors)


def test_scheduler_audit_rejects_incomplete_archive_proposal():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")
    payload["archive_proposal"]["rows"] = []

    errors = validate(payload)

    assert any("archive proposal rows do not match" in error for error in errors)
