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
    if freshness.get("latest_named_exception_recheck_as_of") != "2026-08-06T17:49:03+02:00":
        errors.append("named-exception catalyst recheck timestamp is missing or stale")
    if freshness.get("latest_official_recheck_as_of") != "2026-08-06T17:57:25+02:00":
        errors.append("official issuer recheck timestamp is missing or stale")

    validation = payload.get("validation", {})
    if validation.get("verified_upcoming_rows") != 21:
        errors.append("catalyst register must contain 21 verified-upcoming rows")
    if validation.get("unverified_upcoming_rows") != 1:
        errors.append("catalyst register must retain one unverified-upcoming row")
    if validation.get("event_refresh_rows") != 4:
        errors.append("August 6 event refresh must contain four issuer rows")
    if validation.get("all_unverified_events_wait_for_official_date") is not True:
        errors.append("unverified events must remain WAITING_OFFICIAL_DATE")
    if validation.get("invalid_upcoming_rows") != 0:
        errors.append("upcoming catalyst rows have missing/invalid source fields")
    if validation.get("unverified_not_waiting_rows") != 0:
        errors.append("an unverified catalyst row is promoted beyond WAITING_OFFICIAL_DATE")
    if validation.get("event_authority_errors"):
        errors.append("August 6 event refresh has non-WAITING_RELEASE rows")
    if validation.get("authority_errors"):
        errors.append("catalyst source/authority metadata is inconsistent")
    named = validation.get("latest_named_exception_recheck", {})
    if named.get("authority") != "READ_ONLY_REVIEW_EVIDENCE":
        errors.append("named-exception recheck must remain read-only evidence")
    if {row.get("ticker") for row in named.get("rows", [])} != {"SPCX", "PLTR", "W", "NEM"}:
        errors.append("named-exception recheck must cover SpaceX and the three recent exit names")
    if {row.get("ticker") for row in named.get("lookup_failures", [])} != {"SHOP"}:
        errors.append("named-exception recheck must retain the Shopify lookup failure")
    official = validation.get("latest_official_recheck", {})
    if official.get("authority") != "READ_ONLY_OFFICIAL_ISSUER_REVIEW":
        errors.append("official issuer recheck must remain read-only evidence")
    if {row.get("ticker") for row in official.get("rows", [])} != {"QBTS", "ONTO", "AKAM", "CGNX"}:
        errors.append("official issuer recheck must cover the due August 6 rows")
    if any(row.get("publication_verified") is not False for row in official.get("rows", [])):
        errors.append("official issuer recheck cannot promote unverified publications")

    blocker_ids = {str(row.get("id")) for row in payload.get("completion_blockers", [])}
    for blocker in ("CAT1", "CAT2", "CAT3"):
        if blocker not in blocker_ids:
            errors.append(f"catalyst blocker {blocker} must remain explicit")
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
