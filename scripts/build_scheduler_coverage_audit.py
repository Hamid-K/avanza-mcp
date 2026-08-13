#!/usr/bin/env python3
"""Build a read-only audit of the durable scheduler ledger."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = ROOT / "INSTRUCTIONS" / "SCHEDULER.md"
OUTPUT = ROOT / "output" / "PORTFOLIO_SCHEDULER_COVERAGE_AUDIT_20260806.json"
NON_TERMINAL = {"PENDING", "DUE", "WAITING_RELEASE", "WAITING_REVERSAL", "AWAITING_APPROVAL", "BLOCKED"}
TERMINAL = {"COMPLETED", "REVIEWED_NO_ACTION", "CANCELLED"}
STATUS_PATTERN = re.compile(r"^`?([A-Z_]+)`?$")


def _strip_code(value: str) -> str:
    return value.strip().strip("`").strip()


def parse_active_rows(text: str) -> tuple[str, list[dict[str, Any]]]:
    start = text.index("## Active Schedule")
    end = text.index("## Completed Archive")
    active = text[start:end]
    last_updated_match = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2} CEST)", text)
    last_updated = last_updated_match.group(1) if last_updated_match else "UNSPECIFIED"
    rows: list[dict[str, Any]] = []
    for line in active.splitlines():
        if not line.startswith("| `"):
            continue
        fields = [field.strip() for field in line.strip().strip("|").split("|")]
        if len(fields) != 9:
            rows.append({"parse_status": "INVALID_COLUMN_COUNT", "raw": line})
            continue
        rows.append(
            {
                "id": _strip_code(fields[0]),
                "due_window": fields[1],
                "trigger": fields[2],
                "account": fields[3],
                "instrument": fields[4],
                "planned_review": fields[5],
                "status": _strip_code(fields[6]),
                "last_checked": fields[7],
                "next_check": fields[8],
            }
        )
    return last_updated, rows


def parse_archived_rows(text: str) -> list[dict[str, Any]]:
    start = text.index("## Completed Archive") + len("## Completed Archive")
    archive = text[start:]
    next_section = re.search(r"^##\s+", archive, re.MULTILINE)
    if next_section:
        archive = archive[: next_section.start()]

    rows: list[dict[str, Any]] = []
    for line in archive.splitlines():
        if not line.startswith("| `"):
            continue
        fields = [field.strip() for field in line.strip().strip("|").split("|")]
        if len(fields) != 4:
            rows.append({"parse_status": "INVALID_COLUMN_COUNT", "raw": line})
            continue
        rows.append(
            {
                "id": _strip_code(fields[0]),
                "account_instrument": fields[1],
                "status": _strip_code(fields[2]),
                "final_evidence": fields[3],
            }
        )
    return rows


def build_audit(*, generated_at: str | None = None) -> dict[str, Any]:
    text = SCHEDULER.read_text(encoding="utf-8")
    last_updated, rows = parse_active_rows(text)
    archived_rows = parse_archived_rows(text)
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", ""))
        status_counts[status] = status_counts.get(status, 0) + 1
    archive_status_counts: dict[str, int] = {}
    for row in archived_rows:
        status = str(row.get("status", ""))
        archive_status_counts[status] = archive_status_counts.get(status, 0) + 1
    terminal_rows = [row for row in rows if row.get("status") in TERMINAL]
    non_terminal_rows = [row for row in rows if row.get("status") in NON_TERMINAL]
    invalid_status_rows = [
        row for row in rows if row.get("status") not in NON_TERMINAL and row.get("status") not in TERMINAL
    ]
    invalid_archive_status_rows = [row for row in archived_rows if row.get("status") not in TERMINAL]
    active_approval_c_rows = [row for row in rows if str(row.get("id", "")).startswith("C-")]
    archived_approval_c_rows = [row for row in archived_rows if str(row.get("id", "")).startswith("C-")]
    generated_at = generated_at or datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(timespec="seconds")
    blockers = []
    if terminal_rows:
        blockers.append(
            {
                "id": "SCH1",
                "type": "TERMINAL_ROWS_IN_ACTIVE_SECTION",
                "count": len(terminal_rows),
                "condition_to_close": "Move terminal rows to Completed Archive only after preserving final evidence and without changing planned action semantics.",
            }
        )
    if invalid_status_rows or invalid_archive_status_rows:
        blockers.append(
            {
                "id": "SCH2",
                "type": "INVALID_SCHEDULER_STATUS",
                "count": len(invalid_status_rows) + len(invalid_archive_status_rows),
                "condition_to_close": "Replace each invalid status with a defined non-terminal or terminal scheduler status and retain evidence.",
            }
        )
    blockers.append(
        {
            "id": "SCH3",
            "type": "LIVE_REFRESH_REQUIRED",
            "condition_to_close": "After exact Personal and DarkCell live refresh, scan every non-terminal row and verify publication, exchange timezone, DST, holiday/early-close, and technical evidence.",
        }
    )
    archive_proposal = {
        "status": "AWAITING_USER_SCHEDULER_AUTHORITY" if terminal_rows else "NOT_REQUIRED",
        "authority": {
            "scheduler_mutation": False,
            "broker_mutation": False,
            "paper_mutation": False,
            "trade_authority": False,
        },
        "preserve_planned_action_semantics": True,
        "destination": "Completed Archive",
        "row_ids": [row.get("id") for row in terminal_rows],
        "rows": terminal_rows,
        "instruction": (
            "Move only these terminal rows to Completed Archive after explicit scheduler authority; "
            "preserve final status, evidence, planned review/action, and completion timing."
        ),
    }
    return {
        "artifact": "PORTFOLIO_SCHEDULER_COVERAGE_AUDIT",
        "generated_at": generated_at,
        "timezone": "Europe/Stockholm",
        "source": "INSTRUCTIONS/SCHEDULER.md",
        "authority": {
            "scheduler_mutation": False,
            "broker_mutation": False,
            "paper_mutation": False,
            "trade_authority": False,
        },
        "status": "VALIDATED_CONTRACT_WITH_ARCHIVE_GAP" if terminal_rows else "VALIDATED_STAMPED_REVIEW_LEDGER",
        "freshness": {
            "source_last_updated": last_updated,
            "live_refresh_verified": False,
            "requires_new_scoped_live_refresh_before_action": True,
        },
        "rows": rows,
        "archived_rows": archived_rows,
        "validation": {
            "active_section_rows": len(rows),
            "completed_archive_rows": len(archived_rows),
            "non_terminal_rows": len(non_terminal_rows),
            "terminal_rows_in_active_section": len(terminal_rows),
            "invalid_status_rows": len(invalid_status_rows),
            "invalid_archive_status_rows": len(invalid_archive_status_rows),
            "status_counts": status_counts,
            "archive_status_counts": archive_status_counts,
            "canonical_approval_c_active_rows": len(active_approval_c_rows),
            "canonical_approval_c_archived_rows": len(archived_approval_c_rows),
            "canonical_approval_c_rows": len(active_approval_c_rows) + len(archived_approval_c_rows),
            "daily_buyback_rows": sum(str(row.get("id", "")).startswith("BUYBACK-COVERAGE-DAILY") for row in rows),
            "all_non_terminal_rows_have_checks": all(
                bool(row.get("last_checked")) and bool(row.get("next_check")) for row in non_terminal_rows
            ),
        },
        "completion_blockers": blockers,
        "archive_proposal": archive_proposal,
        "notes": [
            "Scheduler dates are wake-up conditions, not publication proof or trade authorization.",
            "Canonical Approval C integrity spans active non-terminal rows and terminal rows preserved in Completed Archive.",
            "No scheduler row was moved, deleted, completed, or otherwise mutated by this audit.",
        ],
    }


def main() -> int:
    payload = build_audit()
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[scheduler-coverage] wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
