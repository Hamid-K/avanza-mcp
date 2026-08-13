from scripts.build_scheduler_coverage_audit import build_audit
from scripts.verify_scheduler_coverage_artifacts import validate


def test_scheduler_audit_preserves_canonical_queue_across_active_and_archive():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")

    assert validate(payload) == []
    assert payload["validation"]["canonical_approval_c_rows"] == 18
    assert payload["validation"]["canonical_approval_c_active_rows"] == 14
    assert payload["validation"]["canonical_approval_c_archived_rows"] == 4
    assert payload["validation"]["completed_archive_rows"] == 5
    assert payload["validation"]["terminal_rows_in_active_section"] == 0
    assert payload["status"] == "VALIDATED_STAMPED_REVIEW_LEDGER"
    assert payload["archive_proposal"]["status"] == "NOT_REQUIRED"
    assert payload["archive_proposal"]["rows"] == []
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


def test_scheduler_audit_rejects_missing_archived_canonical_row():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")
    payload["archived_rows"] = [
        row for row in payload["archived_rows"] if row["id"] != "C-P-FFIV-20260727"
    ]

    errors = validate(payload)

    assert any("canonical Approval C queue" in error for error in errors)


def test_scheduler_audit_rejects_non_terminal_archive_row():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")
    payload["archived_rows"][0]["status"] = "WAITING_REVERSAL"

    errors = validate(payload)

    assert any("archived scheduler row must be terminal" in error for error in errors)


def test_scheduler_audit_rejects_duplicate_id_across_active_and_archive():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")
    payload["archived_rows"][0]["id"] = payload["rows"][0]["id"]

    errors = validate(payload)

    assert any("unique across active and archive" in error for error in errors)


def test_scheduler_audit_rejects_stale_terminal_count_metadata():
    payload = build_audit(generated_at="2026-08-06T12:00:00+02:00")
    payload["validation"]["terminal_rows_in_active_section"] = 1

    errors = validate(payload)

    assert any("terminal count does not match" in error for error in errors)
