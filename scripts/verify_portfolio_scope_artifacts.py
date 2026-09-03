#!/usr/bin/env python3
"""Validate exact scope and authority metadata across portfolio artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
DEFAULT_STRATEGY = OUTPUT / "PORTFOLIO_INSTRUMENT_STRATEGY_MASTER_20260731.json"
EXPECTED_SCOPE = [
    {"tenant_session_id": "personal", "account_id": "5227886"},
    {"tenant_session_id": "darkcell", "account_id": "7616265"},
]
EXPECTED_ACCOUNT = {
    "Personal": EXPECTED_SCOPE[0],
    "DarkCell": EXPECTED_SCOPE[1],
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _analysis_snapshot(payload: dict[str, Any], label: str, errors: list[str]) -> None:
    freshness = payload.get("freshness", {})
    if freshness.get("status") != "STAMPED_ANALYSIS_SNAPSHOT":
        errors.append(f"{label}: freshness status is not stamped analysis snapshot")
    if freshness.get("live_state_current") is not False:
        errors.append(f"{label}: live state is not explicitly non-current")
    if freshness.get("live_refresh_verified") is not False:
        errors.append(f"{label}: live refresh is not explicitly unverified")
    if freshness.get("requires_new_scoped_live_refresh_before_action") is not True:
        errors.append(f"{label}: new scoped live refresh is not required before action")


def validate(
    clean: dict[str, Any],
    factor: dict[str, Any],
    pending: dict[str, Any],
    displacement: dict[str, Any],
    risk: dict[str, Any],
    live: dict[str, Any],
    strategy_master: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    factor_contract = factor.get("validation", {}).get("dynamic_identity_contract")
    if not isinstance(factor_contract, dict) or factor_contract.get("schema_version") != 1:
        errors.append("factor: dynamic identity contract is missing or invalid")
        factor_contract = None

    for label, payload in (
        ("clean", clean),
        ("factor", factor),
        ("pending", pending),
        ("risk", risk),
        ("live", live),
    ):
        _analysis_snapshot(payload, label, errors)
        if payload.get("scope") != EXPECTED_SCOPE:
            errors.append(f"{label}: exact scope is not Personal/DarkCell")
        validation = payload.get("validation", {})
        if validation.get("exact_account_scope") != EXPECTED_SCOPE:
            errors.append(f"{label}: embedded exact scope is missing")
        if validation.get("exact_account_scope_complete") is not True:
            errors.append(f"{label}: exact scope is not complete")
        if factor_contract is not None and validation.get("dynamic_identity_contract") != factor_contract:
            errors.append(f"{label}: dynamic identity contract does not match factor scope")

    _analysis_snapshot(displacement, "displacement", errors)
    if factor_contract is not None and displacement.get("validation", {}).get("dynamic_identity_contract") != factor_contract:
        errors.append("displacement: dynamic identity contract does not match factor scope")

    if displacement.get("scope") != [EXPECTED_SCOPE[1]]:
        errors.append("displacement: exact DarkCell scope is missing")
    if displacement.get("validation", {}).get("exact_account_scope") != [EXPECTED_SCOPE[1]]:
        errors.append("displacement: embedded DarkCell scope is missing")

    if set(clean.get("accounts", {})) != set(EXPECTED_ACCOUNT):
        errors.append("clean: account buckets are not Personal and DarkCell")
    if set(live.get("accounts", {})) != set(EXPECTED_ACCOUNT):
        errors.append("live: account buckets are not Personal and DarkCell")
    for label, account in live.get("accounts", {}).items():
        expected = EXPECTED_ACCOUNT.get(label)
        if expected is None or account.get("account_id") != expected["account_id"] or account.get("tenant_session_id") != expected["tenant_session_id"]:
            errors.append(f"live: account bucket {label} lacks exact scope")

    for row in pending.get("rows", []):
        expected = EXPECTED_ACCOUNT.get(row.get("account"))
        if expected is None or row.get("account_id") != expected["account_id"] or row.get("tenant_session_id") != expected["tenant_session_id"]:
            errors.append(f"pending: row {row.get('ticker')} lacks exact account scope")
    for row in displacement.get("rows", []):
        if row.get("account_id") != "7616265" or row.get("tenant_session_id") != "darkcell":
            errors.append(f"displacement: row {row.get('ticker')} lacks exact DarkCell scope")

    if clean.get("validation", {}).get("live_authorization_personal") is not False or clean.get("validation", {}).get("live_authorization_darkcell") is not False:
        errors.append("clean: live authorization is not explicitly off")
    if live.get("validation", {}).get("broker_mutation") is not False:
        errors.append("live: broker mutation is not false")
    if live.get("validation", {}).get("live_authorization_personal") is not False or live.get("validation", {}).get("live_authorization_darkcell") is not False:
        errors.append("live: authorization is not explicitly off")
    if factor_contract is not None:
        clean_rows = sum(
            len(account.get("positions", []))
            for account in clean.get("accounts", {}).values()
            if isinstance(account, dict)
        )
        if clean_rows != factor_contract.get("account_position_count"):
            errors.append("clean: account-position identities do not match the dynamic contract")
        live_rows = live.get("validation", {}).get("position_strategy_rows")
        if live_rows != factor_contract.get("account_position_count"):
            errors.append("live: strategy rows do not match the dynamic identity contract")
        if strategy_master is not None:
            try:
                from scripts.enrich_portfolio_artifact_scope import strategy_identity_contract
            except ModuleNotFoundError:
                from enrich_portfolio_artifact_scope import strategy_identity_contract
            if factor_contract != strategy_identity_contract(strategy_master):
                errors.append("portfolio artifact identity contract does not match strategy master")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--strategy-master", type=Path, default=DEFAULT_STRATEGY)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    names = {
        "clean": "PORTFOLIO_CLEAN_SHEET_POST_MINI_20260731.json",
        "factor": "PORTFOLIO_FACTOR_EXPOSURE_20260731.json",
        "pending": "PORTFOLIO_PENDING_ORDER_IMPLEMENTATION_20260731.json",
        "displacement": "PORTFOLIO_CAPITAL_DISPLACEMENT_20260731.json",
        "risk": "PORTFOLIO_RISK_GOVERNANCE_20260731.json",
        "live": "PORTFOLIO_LIVE_RECONCILIATION_20260731_1400.json",
    }
    payloads = {kind: _read(output / name) for kind, name in names.items()}
    errors = validate(
        **payloads,
        strategy_master=_read(args.strategy_master),
    )
    if errors:
        for error in errors:
            print(f"[portfolio-scope] FAIL: {error}")
        return 1
    print("[portfolio-scope] PASS: exact account scope and authority metadata are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
