#!/usr/bin/env python3
"""Add deterministic exact scope metadata to private portfolio artifacts."""

from __future__ import annotations

import argparse
import hashlib
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


def _identity_digest(values: set[tuple[str, ...]] | set[str]) -> str:
    encoded = json.dumps(sorted(values), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def strategy_identity_contract(master: dict[str, Any]) -> dict[str, Any]:
    """Derive the current strategy universe from exact identities, not constants."""

    rows = master.get("instruments")
    if not isinstance(rows, list) or not rows:
        raise ValueError("strategy master has no instrument identities")
    instrument_names = {str(row.get("instrument") or "").strip() for row in rows}
    account_positions = {
        (
            str(account.get("tenant_session_id") or ""),
            str(account.get("account_id") or ""),
            str(account.get("orderbook_id") or ""),
        )
        for row in rows
        for account in row.get("accounts", [])
    }
    if "" in instrument_names or any(not all(identity) for identity in account_positions):
        raise ValueError("strategy master contains an incomplete exact identity")
    return {
        "schema_version": 1,
        "instrument_count": len(instrument_names),
        "account_position_count": len(account_positions),
        "instrument_name_sha256": _identity_digest(instrument_names),
        "account_position_identity_sha256": _identity_digest(account_positions),
    }


def _scope_for_label(label: str) -> dict[str, str]:
    if label not in ACCOUNT_SCOPE:
        raise ValueError(f"unsupported account label {label!r}")
    return dict(ACCOUNT_SCOPE[label])


def enrich_artifact(
    kind: str,
    payload: dict[str, Any],
    *,
    identity_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload["freshness"] = dict(ANALYSIS_SNAPSHOT_FRESHNESS)
    if kind in {"clean", "factor", "pending", "risk", "live"}:
        payload["scope"] = list(EXPECTED_SCOPE)

    validation = dict(payload.get("validation") or {})
    validation["exact_account_scope"] = list(EXPECTED_SCOPE)
    if identity_contract is not None:
        validation["dynamic_identity_contract"] = dict(identity_contract)

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
        instrument_rows = payload.get("instrument_postfill", [])
        if not isinstance(instrument_rows, list):
            raise ValueError("factor instrument_postfill must be a list")
        validation["unique_instruments"] = len(instrument_rows)
        if identity_contract is not None:
            if len(instrument_rows) != identity_contract["instrument_count"]:
                raise ValueError("factor instrument identities do not match strategy contract")
            names = {str(row.get("instrument") or "").strip() for row in instrument_rows}
            if _identity_digest(names) != identity_contract["instrument_name_sha256"]:
                raise ValueError("factor instrument identity digest does not match strategy contract")
            validation["account_position_rows"] = identity_contract["account_position_count"]
            validation["exact_account_scope_rows"] = identity_contract["account_position_count"]
        else:
            validation["exact_account_scope_rows"] = validation.get("account_position_rows", 0)
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
        if identity_contract is not None:
            validation["exact_account_scope_rows"] = identity_contract["account_position_count"]
    elif kind == "live":
        accounts = payload.get("accounts", {})
        if set(accounts) != set(ACCOUNT_SCOPE):
            raise ValueError("live-reconciliation account buckets do not match exact scope")
        for label, account in accounts.items():
            account.update(_scope_for_label(label))
        observed_rows = int(payload.get("validation", {}).get("position_strategy_rows", 0))
        if identity_contract is not None and observed_rows != identity_contract["account_position_count"]:
            raise ValueError("live strategy rows do not match strategy identity contract")
        validation["exact_account_scope_rows"] = observed_rows

    validation["exact_account_scope_complete"] = True
    payload["validation"] = validation
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy-master",
        type=Path,
        default=OUTPUT / "PORTFOLIO_INSTRUMENT_STRATEGY_MASTER_20260731.json",
    )
    args = parser.parse_args()
    master = json.loads(args.strategy_master.read_text(encoding="utf-8"))
    identity_contract = strategy_identity_contract(master)
    for kind, path in FILES.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        enriched = enrich_artifact(kind, payload, identity_contract=identity_contract)
        path.write_text(json.dumps(enriched, indent=2) + "\n", encoding="utf-8")
        print(f"[portfolio-scope] enriched {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
