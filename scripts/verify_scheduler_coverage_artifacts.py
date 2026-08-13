#!/usr/bin/env python3
"""Verify scheduler status, scope, and archive gaps remain explicit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = ROOT / "output" / "PORTFOLIO_SCHEDULER_COVERAGE_AUDIT_20260806.json"
NON_TERMINAL = {"PENDING", "DUE", "WAITING_RELEASE", "WAITING_REVERSAL", "AWAITING_APPROVAL", "BLOCKED"}
TERMINAL = {"COMPLETED", "REVIEWED_NO_ACTION", "CANCELLED"}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("artifact") != "PORTFOLIO_SCHEDULER_COVERAGE_AUDIT":
        errors.append("scheduler coverage artifact id is missing")
    if payload.get("timezone") != "Europe/Stockholm":
        errors.append("scheduler timezone must be Europe/Stockholm")
    authority = payload.get("authority", {})
    for field in ("scheduler_mutation", "broker_mutation", "paper_mutation", "trade_authority"):
        if authority.get(field) is not False:
            errors.append(f"scheduler authority {field} must remain false")
    freshness = payload.get("freshness", {})
    if freshness.get("live_refresh_verified") is not False:
        errors.append("scheduler audit cannot claim live refresh")
    if freshness.get("requires_new_scoped_live_refresh_before_action") is not True:
        errors.append("scheduler audit must require a new scoped live refresh")

    rows = payload.get("rows", [])
    archived_rows = payload.get("archived_rows", [])
    if not isinstance(rows, list):
        rows = []
    if not isinstance(archived_rows, list):
        archived_rows = []
    ids = [str(row.get("id", "")) for row in rows]
    archived_ids = [str(row.get("id", "")) for row in archived_rows]
    if not rows or any(not row_id for row_id in ids):
        errors.append("scheduler active rows must have IDs")
    if not archived_rows or any(not row_id for row_id in archived_ids):
        errors.append("scheduler archived rows must have IDs")
    combined_ids = ids + archived_ids
    if len(combined_ids) != len(set(combined_ids)):
        errors.append("scheduler row IDs must be unique across active and archive sections")
    for row in rows:
        status = str(row.get("status", ""))
        if status not in NON_TERMINAL and status not in TERMINAL:
            errors.append(f"invalid scheduler status: {status}")
        if status in NON_TERMINAL and (not row.get("last_checked") or not row.get("next_check")):
            errors.append(f"non-terminal row lacks Last checked or Next check: {row.get('id')}")
    for row in archived_rows:
        status = str(row.get("status", ""))
        if status not in TERMINAL:
            errors.append(f"archived scheduler row must be terminal: {row.get('id')} ({status})")
        if not row.get("final_evidence"):
            errors.append(f"archived scheduler row lacks final evidence: {row.get('id')}")

    validation = payload.get("validation", {})
    if validation.get("active_section_rows") != len(rows):
        errors.append("scheduler active-row count does not match parsed rows")
    if validation.get("completed_archive_rows") != len(archived_rows):
        errors.append("scheduler archive-row count does not match parsed rows")
    active_non_terminal_count = sum(row.get("status") in NON_TERMINAL for row in rows)
    active_terminal_count = sum(row.get("status") in TERMINAL for row in rows)
    active_invalid_status_count = len(rows) - active_non_terminal_count - active_terminal_count
    archived_invalid_status_count = sum(row.get("status") not in TERMINAL for row in archived_rows)
    if validation.get("non_terminal_rows") != active_non_terminal_count:
        errors.append("scheduler non-terminal count does not match parsed rows")
    if validation.get("terminal_rows_in_active_section") != active_terminal_count:
        errors.append("scheduler terminal count does not match parsed rows")
    if validation.get("invalid_status_rows") != active_invalid_status_count:
        errors.append("scheduler invalid-status count does not match parsed rows")
    if validation.get("invalid_archive_status_rows") != archived_invalid_status_count:
        errors.append("scheduler archive invalid-status count does not match parsed rows")
    active_approval_c_count = sum(row_id.startswith("C-") for row_id in ids)
    archived_approval_c_count = sum(row_id.startswith("C-") for row_id in archived_ids)
    if validation.get("canonical_approval_c_active_rows") != active_approval_c_count:
        errors.append("active Approval C count does not match parsed rows")
    if validation.get("canonical_approval_c_archived_rows") != archived_approval_c_count:
        errors.append("archived Approval C count does not match parsed rows")
    if (
        validation.get("canonical_approval_c_rows") != active_approval_c_count + archived_approval_c_count
        or active_approval_c_count + archived_approval_c_count != 18
    ):
        errors.append("canonical Approval C queue must contain exactly 18 rows")
    if validation.get("daily_buyback_rows") != 1:
        errors.append("daily buyback coverage must contain exactly one scheduler row")
    if validation.get("all_non_terminal_rows_have_checks") is not True:
        errors.append("not every non-terminal scheduler row has a check window")

    terminal_count = active_terminal_count
    blocker_ids = {str(row.get("id")) for row in payload.get("completion_blockers", [])}
    if terminal_count > 0 and "SCH1" not in blocker_ids:
        errors.append("terminal rows in the active section require SCH1")
    if "SCH3" not in blocker_ids:
        errors.append("live scheduler review blocker SCH3 must remain explicit")
    if terminal_count > 0 and payload.get("status") != "VALIDATED_CONTRACT_WITH_ARCHIVE_GAP":
        errors.append("scheduler status must expose the active/archive gap")
    if terminal_count == 0 and payload.get("status") != "VALIDATED_STAMPED_REVIEW_LEDGER":
        errors.append("scheduler status must report a stamped review ledger when active rows are non-terminal")
    proposal = payload.get("archive_proposal", {})
    if terminal_count > 0:
        if proposal.get("status") != "AWAITING_USER_SCHEDULER_AUTHORITY":
            errors.append("scheduler archive proposal must await explicit user authority")
        if proposal.get("destination") != "Completed Archive":
            errors.append("scheduler archive proposal destination is missing")
        if proposal.get("preserve_planned_action_semantics") is not True:
            errors.append("scheduler archive proposal must preserve planned action semantics")
        proposal_authority = proposal.get("authority", {})
        for field in ("scheduler_mutation", "broker_mutation", "paper_mutation", "trade_authority"):
            if proposal_authority.get(field) is not False:
                errors.append(f"scheduler archive proposal authority {field} must remain false")
        terminal_ids = {str(row.get("id")) for row in rows if row.get("status") in TERMINAL}
        proposed_ids = {str(row.get("id")) for row in proposal.get("rows", [])}
        if proposed_ids != terminal_ids:
            errors.append("scheduler archive proposal rows do not match terminal active rows")
        if any(not row.get("planned_review") for row in proposal.get("rows", [])):
            errors.append("scheduler archive proposal must preserve each planned review/action")
    else:
        if proposal.get("status") != "NOT_REQUIRED":
            errors.append("scheduler archive proposal must be NOT_REQUIRED when no terminal row is active")
        if proposal.get("row_ids") or proposal.get("rows"):
            errors.append("scheduler archive proposal must be empty when no terminal row is active")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    path = args.audit if args.audit.is_absolute() else ROOT / args.audit
    if not path.exists():
        print(f"[scheduler-coverage] missing: {path}")
        return 2
    errors = validate(_read(path))
    if errors:
        for error in errors:
            print(f"[scheduler-coverage] FAIL: {error}")
        return 1
    print("[scheduler-coverage] PASS: status contract and scheduler gaps are explicit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
