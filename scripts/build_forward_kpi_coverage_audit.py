#!/usr/bin/env python3
"""Build a read-only audit of forward outcome/KPI evidence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
RESET = ROOT / "output" / "PORTFOLIO_RESET_OUTCOME_BENCHMARK_20260731.json"
PIPELINE = ROOT / "output" / "PORTFOLIO_T1_INTRADAY_RETURN_PIPELINE_20260803_1253.json"
COST = ROOT / "output" / "PORTFOLIO_DAILY_COST_ATTRIBUTION_20260804.json"
FRICTION = ROOT / "output" / "PORTFOLIO_ROLLING_20_SESSION_FRICTION_REFRESH_20260803_2144.json"
LIVE_OBSERVATION = ROOT / "output" / "PORTFOLIO_FORWARD_KPI_LIVE_OBSERVATION_20260806.json"
OUTPUT = ROOT / "output" / "PORTFOLIO_FORWARD_KPI_COVERAGE_AUDIT_20260806.json"


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_audit(*, generated_at: str | None = None) -> dict[str, Any]:
    reset = read(RESET)
    pipeline = read(PIPELINE)
    cost = read(COST)
    friction = read(FRICTION)
    live_observation = read(LIVE_OBSERVATION) if LIVE_OBSERVATION.exists() else None
    scorecard = reset.get("prospective_scorecard", [])
    schedule = reset.get("review_schedule", [])
    pipeline_decision = pipeline.get("decision", {})
    friction_decision = friction.get("operating_decision", {})
    generated_at = generated_at or datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(timespec="seconds")

    measure_rows = []
    for measure in scorecard:
        measure_id = measure.get("id")
        if measure_id == "RESET-05":
            status = "HISTORICAL_BRAKE_BASELINE_ONLY"
            next_required = "Refresh executed transactions, exact fees, spread, FX, slippage and current capital after the live bridge refresh."
        elif measure_id == "RESET-10":
            status = "PROTOCOL_DEFINED_NO_CURRENT_SCORE"
            next_required = "Reconcile every post-reset mutation tuple, preflight, readback and authorization lifecycle from raw live evidence."
        else:
            status = "DEFINED_NOT_CURRENTLY_SCORED"
            next_required = "Capture aligned account values, benchmark references, transactions and current holdings for the stated measurement window."
        measure_rows.append(
            {
                "id": measure_id,
                "measure": measure.get("measure"),
                "windows": measure.get("windows", []),
                "status": status,
                "next_required": next_required,
            }
        )

    return {
        "artifact": "PORTFOLIO_FORWARD_KPI_COVERAGE_AUDIT",
        "generated_at": generated_at,
        "timezone": "Europe/Stockholm",
        "sources": [
            "output/PORTFOLIO_RESET_OUTCOME_BENCHMARK_20260731.json",
            "output/PORTFOLIO_T1_INTRADAY_RETURN_PIPELINE_20260803_1253.json",
            "output/PORTFOLIO_DAILY_COST_ATTRIBUTION_20260804.json",
            "output/PORTFOLIO_ROLLING_20_SESSION_FRICTION_REFRESH_20260803_2144.json",
            *(["output/PORTFOLIO_FORWARD_KPI_LIVE_OBSERVATION_20260806.json"] if live_observation else []),
        ],
        "authority": {
            "broker_mutation": False,
            "registry_mutation": False,
            "scheduler_mutation": False,
            "trade_authority": False,
        },
        "status": "INCOMPLETE_OUTCOME_EVIDENCE",
        "freshness": {
            "status": "STAMPED_ANALYSIS_SNAPSHOT",
            "baseline_as_of": reset.get("generated_at"),
            "live_state_current": False,
            "live_refresh_verified": False,
            "requires_new_scoped_live_refresh_before_action": True,
            "statement": "Definitions and historical controls are preserved, but no current forward outcome or benchmark score is inferred from stale or mixed-session evidence.",
            "descriptive_live_observation_as_of": live_observation.get("as_of") if live_observation else None,
            "descriptive_live_observation_only": live_observation is not None,
        },
        "validation": {
            "scorecard_measure_count": len(scorecard),
            "source_lineage_complete": reset.get("validation", {}).get("scorecard_source_lineage_complete"),
            "completed_forward_scorecard_measures": 0,
            "forward_outcome_proven": False,
            "one_session_outcome_complete": pipeline_decision.get("one_session_outcome_complete") is True,
            "risk_adjusted_result_scored": pipeline_decision.get("risk_adjusted_result_scored") is True,
            "benchmark_comparison_scored": pipeline_decision.get("benchmark_comparison_scored") is True,
            "hard_churn_brake_active": friction_decision.get("state") == "HARD_BRAKE_ACTIVE",
            "frozen_starting_holdings_replay_required": any(
                "frozen starting-holdings replay" in str(item).lower()
                for item in cost.get("limitations", [])
            ),
            "descriptive_live_observation_account_count": len(live_observation.get("accounts", [])) if live_observation else 0,
            "descriptive_live_observation_scored": False,
        },
        "current_live_observation": live_observation,
        "measures": measure_rows,
        "scheduled_checkpoints": [
            {
                "date": row.get("date"),
                "state": row.get("state"),
                "work": row.get("work"),
                "evidence_status": "NOT_PROVEN_FROM_CURRENT_SCOPED_EVIDENCE",
            }
            for row in schedule
        ],
        "completion_blockers": [
            {
                "id": "KPI1",
                "type": "FORWARD_OUTCOME_NOT_PROVEN",
                "condition_to_close": "Capture aligned one-, three-, and five-session account/broker observations and score the specified return, benchmark, drawdown, participation, and decision-outcome measures.",
            },
            {
                "id": "KPI2",
                "type": "BENCHMARK_AND_RISK_SCORE_NOT_PROVEN",
                "condition_to_close": "Use stable matching ACWI, QQQ and USDSEK observations with cash-flow-adjusted account values; do not score mixed-session or delayed snapshots.",
            },
            {
                "id": "KPI3",
                "type": "FROZEN_REPLAY_REQUIRED",
                "condition_to_close": "Complete the frozen starting-holdings path with corporate actions and dividends before attributing residual performance to selection, timing, or process.",
            },
            {
                "id": "KPI4",
                "type": "CURRENT_LIVE_REFRESH_REQUIRED",
                "condition_to_close": "Refresh both exact tenant/account scopes and reconcile current holdings, transactions, fees, orders, stops, factors and capital before any forward score is treated as current.",
            },
        ],
        "control_decision": {
            "status": "DEFINED_BUT_NOT_SCORED",
            "hard_churn_brake": friction_decision.get("state"),
            "broker_mutation": False,
        },
    }


def main() -> int:
    payload = build_audit()
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[forward-kpi] wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
