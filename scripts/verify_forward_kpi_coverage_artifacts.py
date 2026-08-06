#!/usr/bin/env python3
"""Verify that forward KPI evidence remains explicit and fail-closed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = ROOT / "output" / "PORTFOLIO_FORWARD_KPI_COVERAGE_AUDIT_20260806.json"


def validate(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("artifact") != "PORTFOLIO_FORWARD_KPI_COVERAGE_AUDIT":
        errors.append("forward KPI artifact id is missing")
    if payload.get("timezone") != "Europe/Stockholm":
        errors.append("forward KPI timezone must be Europe/Stockholm")
    if payload.get("status") != "INCOMPLETE_OUTCOME_EVIDENCE":
        errors.append("forward KPI audit must remain explicitly incomplete")
    for field in ("broker_mutation", "registry_mutation", "scheduler_mutation", "trade_authority"):
        if payload.get("authority", {}).get(field) is not False:
            errors.append(f"forward KPI authority {field} must remain false")

    freshness = payload.get("freshness", {})
    if freshness.get("live_refresh_verified") is not False:
        errors.append("forward KPI audit cannot claim current live verification")
    if freshness.get("requires_new_scoped_live_refresh_before_action") is not True:
        errors.append("forward KPI audit must require a new scoped refresh")
    if freshness.get("descriptive_live_observation_only") is not True:
        errors.append("forward KPI audit must identify the live observation as descriptive only")

    validation = payload.get("validation", {})
    if validation.get("scorecard_measure_count") != 12:
        errors.append("forward KPI scorecard must contain 12 measures")
    if validation.get("source_lineage_complete") != "12/12 measures":
        errors.append("forward KPI source lineage must report 12/12 measures")
    if validation.get("completed_forward_scorecard_measures") != 0:
        errors.append("forward KPI audit must not infer completed forward scores")
    for field in ("forward_outcome_proven", "one_session_outcome_complete", "risk_adjusted_result_scored", "benchmark_comparison_scored"):
        if validation.get(field) is not False:
            errors.append(f"forward KPI {field} must remain false")
    if validation.get("hard_churn_brake_active") is not True:
        errors.append("forward KPI audit must preserve the hard churn brake")
    if validation.get("frozen_starting_holdings_replay_required") is not True:
        errors.append("forward KPI audit must preserve the frozen replay requirement")
    if validation.get("descriptive_live_observation_account_count") != 2:
        errors.append("forward KPI audit must retain both exact account observations")
    if validation.get("descriptive_live_observation_scored") is not False:
        errors.append("forward KPI live observation must not be scored")

    measures = payload.get("measures", [])
    if len(measures) != 12:
        errors.append("forward KPI measure rows must contain 12 entries")
    if any(not row.get("id") or not row.get("status") or not row.get("next_required") for row in measures):
        errors.append("every forward KPI measure needs status and next evidence")
    blocker_ids = {str(row.get("id")) for row in payload.get("completion_blockers", [])}
    for blocker in ("KPI1", "KPI2", "KPI3", "KPI4"):
        if blocker not in blocker_ids:
            errors.append(f"forward KPI blocker {blocker} must remain explicit")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    path = args.audit if args.audit.is_absolute() else ROOT / args.audit
    if not path.exists():
        print(f"[forward-kpi] missing: {path}")
        return 2
    errors = validate(json.loads(path.read_text(encoding="utf-8")))
    if errors:
        for error in errors:
            print(f"[forward-kpi] FAIL: {error}")
        return 1
    print("[forward-kpi] PASS: outcome evidence remains explicit and fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
