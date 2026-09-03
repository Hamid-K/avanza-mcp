#!/usr/bin/env python3
"""Validate authoritative portfolio-control overlays without trading authority."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FACTOR = ROOT / "output" / "PORTFOLIO_FACTOR_EXPOSURE_20260731.json"
DEFAULT_PENDING = ROOT / "output" / "PORTFOLIO_PENDING_ORDER_IMPLEMENTATION_20260731.json"
DEFAULT_DISPLACEMENT = ROOT / "output" / "PORTFOLIO_CAPITAL_DISPLACEMENT_20260731.json"
DEFAULT_RISK = ROOT / "output" / "PORTFOLIO_RISK_GOVERNANCE_20260731.json"
DEFAULT_STRATEGY = ROOT / "output" / "PORTFOLIO_INSTRUMENT_STRATEGY_MASTER_20260731.json"
EXPECTED_SCOPE = [
    {"tenant_session_id": "personal", "account_id": "5227886"},
    {"tenant_session_id": "darkcell", "account_id": "7616265"},
]


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _identity_digest(values: set[tuple[str, ...]] | set[str]) -> str:
    encoded = json.dumps(sorted(values), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _strategy_identity_contract(master: dict[str, Any]) -> dict[str, Any]:
    rows = master.get("instruments", [])
    names = {str(row.get("instrument") or "").strip() for row in rows}
    positions = {
        (
            str(account.get("tenant_session_id") or ""),
            str(account.get("account_id") or ""),
            str(account.get("orderbook_id") or ""),
        )
        for row in rows
        for account in row.get("accounts", [])
    }
    return {
        "schema_version": 1,
        "instrument_count": len(names),
        "account_position_count": len(positions),
        "instrument_name_sha256": _identity_digest(names),
        "account_position_identity_sha256": _identity_digest(positions),
    }


def _identity_contract_errors(
    factor: dict[str, Any],
    *,
    strategy_master: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    validation = factor.get("validation", {})
    contract = validation.get("dynamic_identity_contract")
    if not isinstance(contract, dict) or contract.get("schema_version") != 1:
        return ["factor dynamic identity contract is missing or invalid"]
    rows = factor.get("instrument_postfill", [])
    names = {
        str(row.get("instrument") or "").strip()
        for row in rows
        if isinstance(row, dict)
    }
    if not rows:
        errors.append("factor dynamic instrument identity set is empty")
    if contract.get("instrument_count") != len(rows) or len(names) != len(rows):
        errors.append("factor dynamic instrument identity count mismatch")
    if contract.get("instrument_name_sha256") != _identity_digest(names):
        errors.append("factor dynamic instrument identity digest mismatch")
    if validation.get("unique_instruments") != len(rows):
        errors.append("factor validation instrument count does not match its identities")
    if validation.get("account_position_rows") != contract.get("account_position_count"):
        errors.append("factor validation account-row count does not match its identity contract")
    if strategy_master is not None and contract != _strategy_identity_contract(strategy_master):
        errors.append("factor dynamic identity contract does not match the strategy master")
    return errors


def _read_only(payload: dict[str, Any], label: str, errors: list[str]) -> None:
    for field in ("broker_mutation", "trade_authority"):
        if payload.get(field) is not False:
            errors.append(f"{label}: {field} is not false")


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
    factor: dict[str, Any],
    pending: dict[str, Any],
    displacement: dict[str, Any],
    risk: dict[str, Any],
    strategy_master: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []

    latest_factor = factor.get("august5_live_factor_overlay_latest", {})
    scope = risk.get("scope")
    if scope != EXPECTED_SCOPE:
        errors.append("risk artifact exact tenant/account scope is not Personal 5227886 plus DarkCell 7616265")
    for label in ("Personal 5227886", "DarkCell 7616265"):
        if not isinstance(latest_factor.get(label), dict):
            errors.append(f"factor latest overlay is missing exact account bucket {label}")
    live_scope = str(pending.get("validation", {}).get("current_live_scope", ""))
    if "Personal 5227886" not in live_scope or "DarkCell 7616265" not in live_scope:
        errors.append("pending-order validation does not state both exact account scopes")
    _read_only(latest_factor, "factor latest overlay", errors)
    for label, payload in (
        ("factor", factor),
        ("pending-order", pending),
        ("displacement", displacement),
        ("risk", risk),
    ):
        _analysis_snapshot(payload, label, errors)
    if factor.get("current_governance_overlay", {}).get("supersedes_dynamic_factor_values") is not True:
        errors.append("factor artifact does not declare its current overlay authoritative")
    errors.extend(_identity_contract_errors(factor, strategy_master=strategy_master))
    limits = latest_factor.get("limits", {})
    checks = (
        ("Personal 5227886", "AI_SEMI_DATACENTER", "combined_AI_SEMI_DATACENTER_soft_cap_percent", "Personal AI/semi/data-center"),
        ("DarkCell 7616265", "AI_SEMI_DATACENTER", "DarkCell_AI_SEMI_DATACENTER_soft_cap_percent", "DarkCell AI/semi/data-center"),
        ("combined", "AI_SEMI_DATACENTER", "combined_AI_SEMI_DATACENTER_soft_cap_percent", "combined AI/semi/data-center"),
        ("DarkCell 7616265", "SPECULATIVE_HIGH_BETA", "DarkCell_SPECULATIVE_HIGH_BETA_soft_cap_percent", "DarkCell speculative/high-beta"),
        ("combined", "SPECULATIVE_HIGH_BETA", "combined_SPECULATIVE_HIGH_BETA_soft_cap_percent", "combined speculative/high-beta"),
        ("Personal 5227886", "CRYPTO_LINKED", "CRYPTO_LINKED_review_threshold_percent", "Personal crypto-linked"),
        ("DarkCell 7616265", "CRYPTO_LINKED", "CRYPTO_LINKED_review_threshold_percent", "DarkCell crypto-linked"),
        ("combined", "CRYPTO_LINKED", "CRYPTO_LINKED_review_threshold_percent", "combined crypto-linked"),
    )
    for account, bucket, limit_key, label in checks:
        value = latest_factor.get(account, {}).get("postfill_account_denominator_percent", {}).get(bucket)
        limit = limits.get(limit_key)
        if not isinstance(value, (int, float)) or not isinstance(limit, (int, float)):
            errors.append(f"factor latest overlay: missing {label} limit check")
        elif value > limit + 1e-6:
            errors.append(f"factor latest overlay: {label} exceeds {limit}%")

    pending_overlay = pending.get("current_governance_overlay", {})
    _read_only(pending_overlay, "pending-order overlay", errors)
    if pending_overlay.get("supersedes_dynamic_active_counts_and_dispositions") is not True:
        errors.append("pending-order artifact does not identify its current overlay")
    pending_validation = pending.get("validation", {})
    if pending_validation.get("active_rows") != 54 or pending_validation.get("unique_stop_ids") != 54:
        errors.append("pending-order active/unique stop coverage is not 54/54")
    if pending_validation.get("buy_rows") != 46 or pending_validation.get("sell_rows") != 8:
        errors.append("pending-order BUY/SELL split is not 46/8")
    if pending_validation.get("generic_implementation_rows") != 0:
        errors.append("pending-order artifact reports generic implementation rows")
    if pending_validation.get("all_strategy_intents_recorded") is not True:
        errors.append("pending-order strategy intent coverage is incomplete")

    displacement_overlay = displacement.get("current_governance_overlay", {})
    _read_only(displacement_overlay, "displacement overlay", errors)
    if displacement_overlay.get("supersedes_dynamic_reserve_and_commitment_values") is not True:
        errors.append("displacement artifact does not identify its current overlay")
    if displacement_overlay.get("candidate_before_cancellation_remains_binding") is not True:
        errors.append("candidate-before-cancellation rule is not binding")
    rows = displacement.get("rows", [])
    ids = [str(row.get("stop_loss_id", "")) for row in rows]
    if len(rows) != 23 or len(set(ids)) != len(ids):
        errors.append("displacement inventory must contain 23 unique stop rows")

    risk_overlay = risk.get("current_governance_overlay", {})
    _read_only(risk_overlay, "risk overlay", errors)
    if risk.get("authorization") != "ANALYSIS_AND_POLICY_ONLY":
        errors.append("risk artifact authorization is not analysis-only")
    if risk_overlay.get("current_sell_activation") != "NO_NEW_SELL_ROW_QUALIFIES":
        errors.append("risk overlay has an unexpected sell activation state")
    if risk_overlay.get("hard_churn_brake_active") is not True:
        errors.append("risk overlay does not keep the hard churn brake active")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factor", type=Path, default=DEFAULT_FACTOR)
    parser.add_argument("--pending", type=Path, default=DEFAULT_PENDING)
    parser.add_argument("--displacement", type=Path, default=DEFAULT_DISPLACEMENT)
    parser.add_argument("--risk", type=Path, default=DEFAULT_RISK)
    parser.add_argument("--strategy-master", type=Path, default=DEFAULT_STRATEGY)
    args = parser.parse_args()
    errors = validate(
        _read(args.factor),
        _read(args.pending),
        _read(args.displacement),
        _read(args.risk),
        _read(args.strategy_master),
    )
    if errors:
        for error in errors:
            print(f"[portfolio-controls] FAIL: {error}")
        return 1
    print("[portfolio-controls] PASS: authoritative factor, order, displacement, and risk overlays are fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
