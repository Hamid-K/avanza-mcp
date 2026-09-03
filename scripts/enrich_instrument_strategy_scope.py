#!/usr/bin/env python3
"""Add deterministic exact account scope to the private strategy master."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MASTER = ROOT / "output" / "PORTFOLIO_INSTRUMENT_STRATEGY_MASTER_20260731.json"
OBJECTIVE_AUDIT = ROOT / "output" / "PORTFOLIO_OBJECTIVE_COMPLETENESS_AUDIT_20260731.json"
EXPECTED_SCOPE = {
    "Personal": {"tenant_session_id": "personal", "account_id": "5227886"},
    "DarkCell": {"tenant_session_id": "darkcell", "account_id": "7616265"},
}


def _identity_digest(values: set[tuple[str, ...]] | set[str]) -> str:
    encoded = json.dumps(sorted(values), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _objective_identities(
    objective_audit: dict[str, Any],
) -> tuple[set[str], set[tuple[str, str, str]]]:
    instruments: set[str] = set()
    positions: set[tuple[str, str, str]] = set()
    for row in objective_audit.get("instruments", []):
        identity = str(row.get("key") or row.get("ticker") or "").strip()
        if identity:
            instruments.add(identity)
        exposure = row.get("intended_exposure")
        accounts = exposure.get("accounts", []) if isinstance(exposure, dict) else []
        for account in accounts if isinstance(accounts, list) else []:
            positions.add(
                (
                    str(account.get("tenant_session_id") or ""),
                    str(account.get("account_id") or ""),
                    str(account.get("orderbook_id") or ""),
                )
            )
    return instruments, positions


def portfolio_role_for(instrument: dict[str, Any]) -> str:
    """Return an explicit role label from the existing classified strategy."""

    existing = str(instrument.get("portfolio_role") or "").strip()
    if existing:
        return existing
    bucket = str(instrument.get("bucket") or "").strip()
    if bucket:
        return bucket
    classes = instrument.get("strategy_classes") or []
    if classes:
        return str(classes[0])
    accounts = instrument.get("accounts") or []
    for account in accounts:
        plan = account.get("semantic_plan") or {}
        if plan.get("strategy_class"):
            return str(plan["strategy_class"])
    return "UNCLASSIFIED"


def exposure_row(account: dict[str, Any]) -> dict[str, Any]:
    """Copy exposure quantities without treating them as trade authority."""

    holding = account.get("holding", 0)
    buy_volume = account.get("active_buy_volume", 0)
    sell_volume = account.get("active_sell_volume", 0)
    return {
        "account": account.get("account"),
        "tenant_session_id": account.get("tenant_session_id"),
        "account_id": account.get("account_id"),
        "orderbook_id": account.get("orderbook_id"),
        "current_holding": holding,
        "conditional_buy_volume": buy_volume,
        "conditional_sell_volume": sell_volume,
        "conditional_net_volume": buy_volume - sell_volume,
        "current_value_sek": account.get("value_sek", 0),
    }


def enrich(master: dict[str, Any], objective_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    instruments = master.get("instruments")
    if not isinstance(instruments, list):
        raise ValueError("strategy master instruments must be a list")

    seen: set[tuple[str, str, str]] = set()
    instrument_identities: set[str] = set()
    account_rows = 0
    source_rows = {
        str(row.get("key") or row.get("ticker") or ""): row
        for row in (objective_audit or {}).get("instruments", [])
    }
    for instrument in instruments:
        key = str(instrument.get("key") or instrument.get("ticker") or "")
        if not key or key in instrument_identities:
            raise ValueError(f"missing or duplicate instrument identity {key!r}")
        instrument_identities.add(key)
        role = portfolio_role_for(instrument)
        instrument["portfolio_role"] = role
        source = source_rows.get(key, {})
        stop_recovery = copy.deepcopy(source.get("stop_and_recovery_design") or instrument.get("stop_and_recovery_design"))
        review_schedule = copy.deepcopy(source.get("next_review_schedule") or instrument.get("next_review_schedule"))
        if not isinstance(stop_recovery, dict) or not stop_recovery:
            raise ValueError(f"{key}: explicit stop_and_recovery_design is required")
        if not isinstance(review_schedule, dict) or not review_schedule:
            raise ValueError(f"{key}: explicit next_review_schedule is required")
        instrument["stop_and_recovery_design"] = stop_recovery
        instrument["next_review_schedule"] = review_schedule
        orderbook_ids = instrument.get("orderbook_ids")
        if not isinstance(orderbook_ids, list) or len(orderbook_ids) != 1:
            raise ValueError(f"{key}: exact single orderbook_id is required")
        orderbook_id = str(orderbook_ids[0])
        accounts = instrument.get("accounts")
        if not isinstance(accounts, list):
            raise ValueError(f"{key}: accounts must be a list")
        instrument_exposure: list[dict[str, Any]] = []
        for account in accounts:
            label = str(account.get("account") or "")
            scope = EXPECTED_SCOPE.get(label)
            if scope is None:
                raise ValueError(f"{key}: unsupported account label {label!r}")
            account.update(scope)
            account["orderbook_id"] = orderbook_id
            plan = account.get("semantic_plan")
            if isinstance(plan, dict):
                plan["portfolio_role"] = role
                plan["intended_exposure"] = exposure_row(account)
                plan["stop_and_recovery_design"] = {
                    **copy.deepcopy(stop_recovery),
                    "account": account.get("account"),
                    "current_active_buy_volume": account.get("active_buy_volume", 0),
                    "current_active_sell_volume": account.get("active_sell_volume", 0),
                    "authority": "ANALYSIS_ONLY",
                }
            instrument_exposure.append(exposure_row(account))
            identity = (scope["tenant_session_id"], scope["account_id"], orderbook_id)
            if identity in seen:
                raise ValueError(f"duplicate exact account-position scope: {identity}")
            seen.add(identity)
            account_rows += 1
        instrument["intended_exposure"] = {
            "role": role,
            "current_value_sek": instrument.get("current_value_sek", 0),
            "accounts": instrument_exposure,
            "authority": "ANALYSIS_ONLY",
        }

    master["scope"] = [
        {"tenant_session_id": "personal", "account_id": "5227886"},
        {"tenant_session_id": "darkcell", "account_id": "7616265"},
    ]
    validation = dict(master.get("validation") or {})
    validation["unique_instruments"] = len(instrument_identities)
    validation["account_position_rows"] = account_rows
    validation["exact_account_scope"] = master["scope"]
    validation["exact_account_scope_rows"] = account_rows
    validation["exact_account_scope_complete"] = len(seen) == account_rows
    identity_contract = {
        "schema_version": 1,
        "instrument_count": len(instrument_identities),
        "account_position_count": len(seen),
        "instrument_identity_sha256": _identity_digest(instrument_identities),
        "account_position_identity_sha256": _identity_digest(seen),
    }
    if objective_audit is not None:
        objective_instruments, objective_positions = _objective_identities(objective_audit)
        if objective_instruments != instrument_identities:
            raise ValueError("objective audit instrument identities do not match strategy master")
        if objective_positions != seen:
            raise ValueError("objective audit account-position identities do not match strategy master")
        identity_contract["objective_audit_identity_parity"] = True
    validation["dynamic_identity_contract"] = identity_contract
    master["validation"] = validation
    return master


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    args = parser.parse_args()
    path = args.master if args.master.is_absolute() else ROOT / args.master
    master = json.loads(path.read_text(encoding="utf-8"))
    objective_audit = json.loads(OBJECTIVE_AUDIT.read_text(encoding="utf-8"))
    enriched = enrich(master, objective_audit)
    path.write_text(json.dumps(enriched, indent=2) + "\n", encoding="utf-8")
    print(f"[strategy-scope] enriched {path} ({enriched['validation']['exact_account_scope_rows']} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
