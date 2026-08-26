#!/usr/bin/env python3
"""Attach current economic exposure decisions to an R17 buyback artifact.

The full-history replay answers which sale lots remain unmatched. This script
answers a different question: whether the current account-position is an
intentional hold, a named/non-stop exception, or still requires repair. It is
strictly local and never accesses or mutates broker state.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / ".avanza_position_strategy.json"
HOLD_PROTECTION_CLASSES = {"CORE_HOLD_EXCEPTION", "MARKER_EXCEPTION"}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _registry_position(
    registry: dict[str, Any], account_id: str, orderbook_id: str
) -> dict[str, Any] | None:
    position = (
        registry.get("accounts", {})
        .get(account_id, {})
        .get("positions", {})
        .get(orderbook_id)
    )
    return position if isinstance(position, dict) else None


def _has_reviewable_hold_plan(position: dict[str, Any] | None, expected: str) -> bool:
    if not isinstance(position, dict):
        return False
    return (
        position.get("protection_classification") == expected
        and all(
            bool(str(position.get(field) or "").strip())
            for field in (
                "instrument",
                "strategy_class",
                "thesis",
                "audit_status",
                "stance",
                "protection_reason",
                "next_gate",
            )
        )
    )


def classify_row(
    row: dict[str, Any],
    registry: dict[str, Any],
    registry_updated_at: str | None,
) -> dict[str, Any]:
    """Return the fail-closed economic classification for one exact row."""

    account_id = str(row.get("account_id") or "")
    orderbook_id = str(row.get("orderbook_id") or "")
    protection = str(row.get("current_protection_classification") or "")
    buyback_state = str(row.get("buyback_coverage_state") or "")
    position = _registry_position(registry, account_id, orderbook_id)

    decision = "REPAIR_REQUIRED"
    source = "R17_FULL_HISTORY_AND_POSITION_STRATEGY_RECONCILIATION"
    reason = "The exact row retains unresolved economic recovery or protection work."
    next_review = str(row.get("exact_next_gate") or "").strip()

    if protection == "NAMED_EXCEPTION":
        decision = "NAMED_EXCEPTION"
        source = "CURRENT_NAMED_EXCEPTION"
        reason = (
            str((position or {}).get("protection_reason") or "").strip()
            or "The current row remains governed by its named-instrument exception."
        )
        next_review = str((position or {}).get("next_gate") or next_review).strip()
    elif protection == "NON_STOP_ELIGIBLE_FUND":
        decision = "NON_STOP_ELIGIBLE"
        source = "CURRENT_NON_STOP_ELIGIBLE_CLASSIFICATION"
        reason = (
            str((position or {}).get("protection_reason") or "").strip()
            or "The instrument is verified as non-stop-eligible."
        )
        next_review = str((position or {}).get("next_gate") or next_review).strip()
    elif (
        buyback_state == "LEDGER_ONLY"
        and protection in HOLD_PROTECTION_CLASSES
        and float(row.get("active_buy_volume", 0) or 0) == 0
        and float(row.get("sale_attributed_active_buy_quantity", 0) or 0) == 0
        and row.get("target_rebuild_quantity") is None
        and _has_reviewable_hold_plan(position, protection)
    ):
        decision = "INTENTIONAL_MARKER_OR_CORE_HOLD"
        source = "CURRENT_REVIEWED_POSITION_STRATEGY"
        reason = str(position.get("protection_reason") or "").strip()
        next_review = str(position.get("next_gate") or "").strip()
    elif protection == "REPAIR_REQUIRED":
        reason = (
            str((position or {}).get("protection_reason") or "").strip()
            or "The current position-protection plan remains REPAIR_REQUIRED."
        )
        next_review = str((position or {}).get("next_gate") or next_review).strip()
    elif buyback_state == "LADDER_GAP":
        reason = (
            "The full-history replay retains open sold quantity and no supported "
            "stock-specific percentage ladder or valid terminal decision closes it."
        )
    elif buyback_state == "LEDGER_ONLY":
        reason = (
            "The sold-cycle ledger is closed, but the current position plan is missing "
            "the evidence required for an intentional hold classification."
        )

    result = dict(row)
    result["low_exposure_decision"] = decision
    result["economic_resolution"] = {
        "state": decision,
        "source": source,
        "registry_updated_at": registry_updated_at,
        "position_audit_status": (position or {}).get("audit_status"),
        "position_bucket": (position or {}).get("bucket"),
        "strategy_class": (position or {}).get("strategy_class"),
        "reason": reason,
        "next_review": next_review,
    }
    return result


def enrich_payload(
    payload: dict[str, Any],
    registry: dict[str, Any],
    *,
    generated_at: str,
    source_path: str,
) -> dict[str, Any]:
    if payload.get("artifact") != "PORTFOLIO_BUYBACK_LIVE_COVERAGE":
        raise ValueError("input is not a live buyback coverage artifact")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("input buyback coverage rows must be non-empty")

    result = dict(payload)
    registry_updated_at = registry.get("updated_at")
    enriched_rows = [
        classify_row(row, registry, registry_updated_at)
        for row in rows
        if isinstance(row, dict)
    ]
    if len(enriched_rows) != len(rows):
        raise ValueError("input buyback coverage contains a non-object row")

    result["schema_version"] = max(int(payload.get("schema_version", 0) or 0), 4)
    result["generated_at"] = generated_at
    result["superseded"] = False
    result["supersedes"] = source_path
    result["rows"] = enriched_rows
    sources = list(payload.get("source_artifacts", []))
    for source in (source_path, ".avanza_position_strategy.json"):
        if source not in sources:
            sources.append(source)
    result["source_artifacts"] = sources
    result["economic_classification"] = {
        "authority": "LOCAL_REVIEW_ONLY",
        "classified_at": generated_at,
        "registry_updated_at": registry_updated_at,
        "contract": (
            "A completed sold-cycle may be an intentional hold only when the exact "
            "current registry plan is reviewed and complete. Open sold quantity, "
            "unsupported percentages and position-protection repairs remain fail-closed."
        ),
        "broker_mutation": False,
    }

    summary = dict(payload.get("summary", {}))
    low_counts = Counter(row["low_exposure_decision"] for row in enriched_rows)
    summary["low_exposure_decision_counts"] = {
        state: low_counts.get(state, 0)
        for state in (
            "BUILD_REVIEW",
            "INTENTIONAL_MARKER_OR_CORE_HOLD",
            "EXIT_OR_NO_REENTRY_REVIEW",
            "NAMED_EXCEPTION",
            "NON_STOP_ELIGIBLE",
            "REPAIR_REQUIRED",
        )
    }
    summary["economically_unresolved_rows"] = low_counts.get("REPAIR_REQUIRED", 0)
    summary["economically_resolved_rows"] = len(enriched_rows) - summary["economically_unresolved_rows"]
    result["summary"] = summary

    blockers = [
        str(value)
        for value in payload.get("blockers", [])
        if "exact governed rows still need an evidence-backed economic" not in str(value)
    ]
    unresolved = summary["economically_unresolved_rows"]
    below = int(summary.get("below_20000_sek_rows", 0) or 0)
    blockers.insert(
        1,
        f"{unresolved} exact governed rows still need an evidence-backed economic build, "
        f"hold or exit outcome; {below} current live positions are below 20,000 SEK.",
    )
    result["blockers"] = blockers
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--generated-at")
    args = parser.parse_args()

    generated_at = args.generated_at or datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(timespec="seconds")
    payload = _load_json(args.input)
    registry = _load_json(args.registry)
    try:
        source_path = str(args.input.resolve().relative_to(ROOT))
    except ValueError:
        source_path = str(args.input)
    result = enrich_payload(payload, registry, generated_at=generated_at, source_path=source_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "rows": len(result["rows"]),
                "low_exposure_decision_counts": result["summary"]["low_exposure_decision_counts"],
                "broker_mutation": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
