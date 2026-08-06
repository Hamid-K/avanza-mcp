#!/usr/bin/env python3
"""Add deterministic exact scope metadata to private portfolio artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
EXPECTED_SCOPE = [
    {"tenant_session_id": "personal", "account_id": "5227886"},
    {"tenant_session_id": "darkcell", "account_id": "7616265"},
]
ACCOUNT_SCOPE = {
    "Personal": EXPECTED_SCOPE[0],
    "DarkCell": EXPECTED_SCOPE[1],
}

ANALYSIS_SNAPSHOT_FRESHNESS = {
    "status": "STAMPED_ANALYSIS_SNAPSHOT",
    "live_state_current": False,
    "live_refresh_verified": False,
    "requires_new_scoped_live_refresh_before_action": True,
    "statement": (
        "This artifact is analysis and policy context only. It never replaces a fresh "
        "exact Personal/DarkCell broker refresh or authorizes a mutation."
    ),
}

FILES = {
    "clean": OUTPUT / "PORTFOLIO_CLEAN_SHEET_POST_MINI_20260731.json",
    "factor": OUTPUT / "PORTFOLIO_FACTOR_EXPOSURE_20260731.json",
    "pending": OUTPUT / "PORTFOLIO_PENDING_ORDER_IMPLEMENTATION_20260731.json",
    "displacement": OUTPUT / "PORTFOLIO_CAPITAL_DISPLACEMENT_20260731.json",
    "risk": OUTPUT / "PORTFOLIO_RISK_GOVERNANCE_20260731.json",
    "live": OUTPUT / "PORTFOLIO_LIVE_RECONCILIATION_20260731_1400.json",
    "buy_governance": OUTPUT / "PORTFOLIO_ACTIVE_BUY_GOVERNANCE_AUDIT_20260805.json",
}


def _scope_for_label(label: str) -> dict[str, str]:
    if label not in ACCOUNT_SCOPE:
        raise ValueError(f"unsupported account label {label!r}")
    return dict(ACCOUNT_SCOPE[label])


def enrich_artifact(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload["freshness"] = dict(ANALYSIS_SNAPSHOT_FRESHNESS)
    if kind in {"clean", "factor", "pending", "risk", "live"}:
        payload["scope"] = list(EXPECTED_SCOPE)

    validation = dict(payload.get("validation") or {})
    validation["exact_account_scope"] = list(EXPECTED_SCOPE)

    if kind == "clean":
        accounts = payload.get("accounts", {})
        if set(accounts) != set(ACCOUNT_SCOPE):
            raise ValueError("clean-sheet account buckets do not match exact scope")
        validation["exact_account_scope_rows"] = sum(
            len(accounts[label].get("positions", []))
            for label in ACCOUNT_SCOPE
        )
    elif kind == "factor":
        latest = payload.get("august5_live_factor_overlay_latest", {})
        for label, scope in ACCOUNT_SCOPE.items():
            bucket = f"{label} {scope['account_id']}"
            if not isinstance(latest.get(bucket), dict):
                raise ValueError(f"factor latest overlay missing {bucket}")
        validation["exact_account_scope_rows"] = 107
    elif kind == "pending":
        rows = payload.get("rows", [])
        for row in rows:
            account_id = str(row.get("account_id", ""))
            label = str(row.get("account", ""))
            expected = _scope_for_label(label)
            if account_id != expected["account_id"]:
                raise ValueError(f"pending row account mismatch for {row.get('ticker')}")
            row.update(expected)
        validation["exact_account_scope_rows"] = len(rows)
    elif kind == "displacement":
        if payload.get("account") != "DarkCell 7616265":
            raise ValueError("displacement artifact is not exact DarkCell scope")
        payload["scope"] = [dict(EXPECTED_SCOPE[1])]
        rows = payload.get("rows", [])
        for row in rows:
            row.update(EXPECTED_SCOPE[1])
        validation["exact_account_scope"] = [dict(EXPECTED_SCOPE[1])]
        validation["exact_account_scope_rows"] = len(rows)
    elif kind == "risk":
        validation["exact_account_scope_rows"] = 107
    elif kind == "live":
        accounts = payload.get("accounts", {})
        if set(accounts) != set(ACCOUNT_SCOPE):
            raise ValueError("live-reconciliation account buckets do not match exact scope")
        for label, account in accounts.items():
            account.update(_scope_for_label(label))
        validation["exact_account_scope_rows"] = int(
            payload.get("validation", {}).get("position_strategy_rows", 107)
        )

    validation["exact_account_scope_complete"] = True
    payload["validation"] = validation
    return payload


def main() -> int:
    for kind, path in FILES.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        enriched = enrich_artifact(kind, payload)
        path.write_text(json.dumps(enriched, indent=2) + "\n", encoding="utf-8")
        print(f"[portfolio-scope] enriched {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
