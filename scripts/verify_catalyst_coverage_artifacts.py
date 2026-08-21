#!/usr/bin/env python3
"""Verify catalyst freshness, status, and authority metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = ROOT / "output" / "PORTFOLIO_CATALYST_COVERAGE_AUDIT_20260806.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("artifact") != "PORTFOLIO_CATALYST_COVERAGE_AUDIT":
        errors.append("catalyst coverage artifact id is missing")
    if payload.get("timezone") != "Europe/Stockholm":
        errors.append("catalyst timezone must be Europe/Stockholm")
    for field in ("broker_mutation", "registry_mutation", "scheduler_mutation", "trade_authority"):
        if payload.get("authority", {}).get(field) is not False:
            errors.append(f"catalyst authority {field} must remain false")
    freshness = payload.get("freshness", {})
    if freshness.get("current_live_verified") is not False:
        errors.append("catalyst coverage cannot claim current live verification")
    if freshness.get("requires_new_scoped_live_refresh_before_action") is not True:
        errors.append("catalyst coverage must require a new scoped live refresh")
    validation = payload.get("validation", {})
    verified_count = validation.get("verified_upcoming_rows")
    unverified_count = validation.get("unverified_upcoming_rows")
    if not isinstance(verified_count, int) or verified_count < 0:
        errors.append("verified-upcoming catalyst count must be a non-negative integer")
    if not isinstance(unverified_count, int) or unverified_count < 0:
        errors.append("unverified-upcoming catalyst count must be a non-negative integer")
    if not isinstance(validation.get("event_refresh_rows"), int) or validation.get("event_refresh_rows") < 0:
        errors.append("event refresh row count must be a non-negative integer")
    if validation.get("all_unverified_events_wait_for_official_date") is not True:
        errors.append("unverified events must remain WAITING_OFFICIAL_DATE")
    if validation.get("invalid_upcoming_rows") != 0:
        errors.append("upcoming catalyst rows have missing/invalid source fields")
    if validation.get("unverified_not_waiting_rows") != 0:
        errors.append("an unverified catalyst row is promoted beyond WAITING_OFFICIAL_DATE")
    if validation.get("stale_unverified_rows") != 0:
        errors.append("a verified publication remains incorrectly classified as unverified")
    if validation.get("publication_state_current") is not True:
        errors.append("catalyst publication state is not current")
    if validation.get("event_authority_errors"):
        errors.append("August 6 event refresh has non-WAITING_RELEASE rows")
    if validation.get("authority_errors"):
        errors.append("catalyst source/authority metadata is inconsistent")
    named = validation.get("latest_named_exception_recheck", {})
    if named and named.get("authority") != "READ_ONLY_REVIEW_EVIDENCE":
        errors.append("named-exception recheck must remain read-only evidence")
    official = validation.get("latest_official_recheck", {})
    if official and official.get("authority") != "READ_ONLY_OFFICIAL_ISSUER_REVIEW":
        errors.append("official issuer recheck must remain read-only evidence")

    blocker_ids = {str(row.get("id")) for row in payload.get("completion_blockers", [])}
    for blocker in ("CAT1", "CAT3"):
        if blocker not in blocker_ids:
            errors.append(f"catalyst blocker {blocker} must remain explicit")
    if isinstance(unverified_count, int) and unverified_count > 0 and "CAT2" not in blocker_ids:
        errors.append("catalyst blocker CAT2 must remain explicit while unverified dates exist")
    if unverified_count == 0 and "CAT2" in blocker_ids:
        errors.append("catalyst blocker CAT2 must be absent when no unverified dates remain")
    if "CAT7" in blocker_ids:
        errors.append("stale publication-state blocker CAT7 remains open")
    if payload.get("status") != "VALIDATED_FAIL_CLOSED":
        errors.append("catalyst audit must remain valid but fail-closed while evidence is pending")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    path = args.audit if args.audit.is_absolute() else ROOT / args.audit
    if not path.exists():
        print(f"[catalyst-coverage] missing: {path}")
        return 2
    errors = validate(_read(path))
    if errors:
        for error in errors:
            print(f"[catalyst-coverage] FAIL: {error}")
        return 1
    print("[catalyst-coverage] PASS: publication/status distinctions and authority are fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
