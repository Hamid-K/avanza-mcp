#!/usr/bin/env python3
"""Build a read-only catalyst freshness and publication-status audit."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CATALYST = ROOT / "output" / "PORTFOLIO_CATALYST_EVIDENCE_20260731.json"
EVENT_REFRESH = ROOT / "output" / "PORTFOLIO_AUG6_EVENT_EVIDENCE_REFRESH_20260806_0237.json"
TECHNICAL_REFRESH = ROOT / "output" / "PORTFOLIO_CATALYST_TECHNICAL_REFRESH_20260806.json"
OUTPUT = ROOT / "output" / "PORTFOLIO_CATALYST_COVERAGE_AUDIT_20260806.json"
ALLOWED_UPCOMING = {"PENDING", "WAITING_RELEASE", "WAITING_REVERSAL"}
ALLOWED_EVENT_REFRESH = {"WAITING_RELEASE", "WAITING_REVERSAL"}


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_audit(*, generated_at: str | None = None) -> dict[str, Any]:
    catalyst = read(CATALYST)
    event_refresh = read(EVENT_REFRESH)
    technical_refresh = read(TECHNICAL_REFRESH) if TECHNICAL_REFRESH.exists() else {}
    named_exception_recheck = technical_refresh.get("latest_named_exception_recheck", {})
    upcoming = catalyst.get("verified_upcoming", [])
    unverified = catalyst.get("unverified_upcoming", [])
    completed = catalyst.get("completed_reviews", [])
    event_rows = event_refresh.get("rows", [])
    official_recheck = event_refresh.get("latest_official_recheck", {})
    generated_at = generated_at or datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(timespec="seconds")

    invalid_upcoming = [
        row
        for row in upcoming
        if row.get("status") not in ALLOWED_UPCOMING
        or not all(row.get(field) for field in ("ticker", "event", "when_stockholm", "source"))
    ]
    unverified_not_waiting = [
        row for row in unverified if row.get("status") != "WAITING_OFFICIAL_DATE"
    ]
    event_authority_errors = []
    for row in event_rows:
        if row.get("event_state") not in ALLOWED_EVENT_REFRESH:
            event_authority_errors.append(row.get("ticker"))
    sec = catalyst.get("sec_source_identity_audit", {})
    governance = catalyst.get("current_governance_overlay", {})
    authority_errors = []
    if governance.get("broker_mutation") is not False:
        authority_errors.append("current_governance_overlay.broker_mutation")
    if governance.get("trade_authority") is not False:
        authority_errors.append("current_governance_overlay.trade_authority")
    if sec.get("status") != "PASS" or sec.get("identity_mismatches_remaining") != 0:
        authority_errors.append("sec_source_identity_audit")
    if sec.get("trade_authority") is not False:
        authority_errors.append("sec_source_identity_audit.trade_authority")

    blockers = [
        {
            "id": "CAT1",
            "type": "LIVE_CATALYST_REFRESH_REQUIRED",
            "condition_to_close": "Refresh both exact accounts and current market/evidence sources before treating any catalyst row as current or promotion-ready.",
        },
        {
            "id": "CAT2",
            "type": "UNVERIFIED_EVENT_DATE",
            "ticker": "SOUN",
            "condition_to_close": "Official SoundHound IR must publish or confirm the event date; TradingView estimates cannot complete the gate.",
        },
        {
            "id": "CAT3",
            "type": "POST_RELEASE_REVIEW_REQUIRED",
            "condition_to_close": "For each due event, verify actual publication, guidance, thesis, quote/spread, regular-session reversal, factors, capacity, friction, and exact order/error state before any proposal.",
        },
    ]
    if invalid_upcoming:
        blockers.append(
            {
                "id": "CAT4",
                "type": "INVALID_CATALYST_REGISTER_ROW",
                "count": len(invalid_upcoming),
                "condition_to_close": "Repair every upcoming catalyst row with an explicit issuer source, status, and Stockholm timing.",
            }
        )
    if unverified_not_waiting:
        blockers.append(
            {
                "id": "CAT5",
                "type": "UNVERIFIED_ROW_PROMOTION",
                "count": len(unverified_not_waiting),
                "condition_to_close": "Keep every unverified event in WAITING_OFFICIAL_DATE until issuer evidence is present.",
            }
        )
    if event_authority_errors or authority_errors:
        blockers.append(
            {
                "id": "CAT6",
                "type": "CATALYST_AUTHORITY_METADATA_ERROR",
                "condition_to_close": "Restore read-only authority flags and source-identity status before using event evidence.",
            }
        )

    return {
        "artifact": "PORTFOLIO_CATALYST_COVERAGE_AUDIT",
        "generated_at": generated_at,
        "timezone": "Europe/Stockholm",
        "sources": [
            "output/PORTFOLIO_CATALYST_EVIDENCE_20260731.json",
            "output/PORTFOLIO_AUG6_EVENT_EVIDENCE_REFRESH_20260806_0237.json",
        ] + (["output/PORTFOLIO_CATALYST_TECHNICAL_REFRESH_20260806.json"] if technical_refresh else []),
        "authority": {
            "broker_mutation": False,
            "registry_mutation": False,
            "scheduler_mutation": False,
            "trade_authority": False,
        },
        "status": "VALIDATED_FAIL_CLOSED" if not (invalid_upcoming or unverified_not_waiting or event_authority_errors or authority_errors) else "BLOCKED_METADATA_OR_STATUS",
        "freshness": {
            "current_live_verified": False,
            "catalyst_register_as_of": catalyst.get("generated_at"),
            "event_refresh_as_of": event_refresh.get("as_of"),
            "technical_refresh_as_of": technical_refresh.get("generated_at"),
            "latest_live_recheck_as_of": technical_refresh.get("latest_live_recheck", {}).get("generated_at"),
            "latest_named_exception_recheck_as_of": named_exception_recheck.get("generated_at"),
            "latest_official_recheck_as_of": official_recheck.get("as_of"),
            "requires_new_scoped_live_refresh_before_action": True,
        },
        "validation": {
            "verified_upcoming_rows": len(upcoming),
            "unverified_upcoming_rows": len(unverified),
            "completed_review_rows": len(completed),
            "event_refresh_rows": len(event_rows),
            "technical_refresh_rows": len(technical_refresh.get("rows", [])),
            "technical_lookup_failures": technical_refresh.get("lookup_failures", []),
            "latest_live_recheck": technical_refresh.get("latest_live_recheck", {}),
            "latest_named_exception_recheck": named_exception_recheck,
            "latest_official_recheck": official_recheck,
            "invalid_upcoming_rows": len(invalid_upcoming),
            "unverified_not_waiting_rows": len(unverified_not_waiting),
            "event_authority_errors": event_authority_errors,
            "authority_errors": authority_errors,
            "all_unverified_events_wait_for_official_date": not unverified_not_waiting,
        },
        "completion_blockers": blockers,
        "notes": [
            "A calendar date, scanner estimate, or delayed technical label is not publication evidence.",
            "This audit does not promote, demote, place, cancel, edit, or authorize any broker row.",
        ],
    }


def main() -> int:
    payload = build_audit()
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[catalyst-coverage] wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
