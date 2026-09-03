#!/usr/bin/env python3
"""Validate the static instrument-strategy master without authorizing trades."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MASTER = ROOT / "output" / "PORTFOLIO_INSTRUMENT_STRATEGY_MASTER_20260731.json"
DEFAULT_OBJECTIVE_AUDIT = ROOT / "output" / "PORTFOLIO_OBJECTIVE_COMPLETENESS_AUDIT_20260731.json"

TOP_REQUIRED = (
    "ticker",
    "instrument",
    "portfolio_role",
    "intended_exposure",
    "stop_and_recovery_design",
    "next_review_schedule",
    "primary_factor",
    "decision",
    "catalyst",
    "add_gate",
    "sell_gate",
    "invalidation",
    "risk_budget_rule",
    "friction_rule",
    "loss_recovery_rule",
    "next_review",
)

ACCOUNT_PLAN_REQUIRED = (
    "ticker",
    "instrument",
    "portfolio_role",
    "intended_exposure",
    "stop_and_recovery_design",
    "strategy_class",
    "catalyst",
    "thesis",
    "add_gate",
    "sell_gate",
    "invalidation",
    "risk_budget_rule",
    "friction_rule",
    "loss_recovery_rule",
    "next_gate",
    "recommendation",
    "audit_status",
)

GENERIC_RECOMMENDATIONS = {"keep", "monitor", "review"}
INTENTIONAL_BLANK_TICKER_KEYS = {"AVANZA_ZERO", "ETH_XBT"}
EXPECTED_SCOPE = {
    "Personal": {"tenant_session_id": "personal", "account_id": "5227886"},
    "DarkCell": {"tenant_session_id": "darkcell", "account_id": "7616265"},
}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _identity_digest(values: set[tuple[str, ...]] | set[str]) -> str:
    encoded = json.dumps(sorted(values), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _objective_identity_sets(
    audit: dict[str, Any],
) -> tuple[set[str], set[tuple[str, str, str]]]:
    instruments: set[str] = set()
    positions: set[tuple[str, str, str]] = set()
    for row in audit.get("instruments", []):
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


def validate(master: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    instruments = master.get("instruments")
    if not isinstance(instruments, list):
        return ["instruments must be a list"]

    identities = [str(row.get("key") or row.get("ticker") or "") for row in instruments]
    if len(set(identities)) != len(identities):
        errors.append("instrument identity keys are not unique")

    account_rows = 0
    account_identities: set[tuple[str, str, str]] = set()
    for index, row in enumerate(instruments):
        identity = str(row.get("key") or "")
        label = row.get("ticker") or row.get("instrument") or f"row {index}"
        for field in TOP_REQUIRED:
            if field == "ticker" and identity in INTENTIONAL_BLANK_TICKER_KEYS:
                continue
            if not _present(row.get(field)):
                errors.append(f"{label}: missing top-level strategy field {field}")
        exposure = row.get("intended_exposure")
        if not isinstance(exposure, dict) or not isinstance(exposure.get("accounts"), list) or not exposure.get("accounts"):
            errors.append(f"{label}: intended_exposure must contain account rows")
        stop_recovery = row.get("stop_and_recovery_design")
        if not isinstance(stop_recovery, dict) or not all(
            _present(stop_recovery.get(field))
            for field in ("current_design", "future_sell_rule", "future_recovery_rule", "gap_halt_limit")
        ):
            errors.append(f"{label}: stop_and_recovery_design is incomplete")
        schedule = row.get("next_review_schedule")
        if not isinstance(schedule, dict) or not _present(schedule.get("date")) or schedule.get("timezone") != "Europe/Stockholm":
            errors.append(f"{label}: next_review_schedule is incomplete")

        accounts = row.get("accounts")
        if not isinstance(accounts, list) or not accounts:
            errors.append(f"{label}: accounts must contain at least one position row")
            continue

        account_rows += len(accounts)
        for account_index, account in enumerate(accounts):
            prefix = f"{label} account[{account_index}]"
            if not _present(account.get("account")):
                errors.append(f"{prefix}: missing account identity")
            account_label = str(account.get("account") or "")
            expected_scope = EXPECTED_SCOPE.get(account_label)
            if expected_scope is None:
                errors.append(f"{prefix}: unsupported exact account scope label")
            else:
                if account.get("tenant_session_id") != expected_scope["tenant_session_id"]:
                    errors.append(f"{prefix}: tenant_session_id does not match account scope")
                if str(account.get("account_id")) != expected_scope["account_id"]:
                    errors.append(f"{prefix}: account_id does not match account scope")
            orderbook_ids = row.get("orderbook_ids")
            if not isinstance(orderbook_ids, list) or len(orderbook_ids) != 1:
                errors.append(f"{prefix}: exact orderbook_ids source is not single-valued")
            elif str(account.get("orderbook_id")) != str(orderbook_ids[0]):
                errors.append(f"{prefix}: orderbook_id does not match instrument source")
            identity_tuple = (
                str(account.get("tenant_session_id") or ""),
                str(account.get("account_id") or ""),
                str(account.get("orderbook_id") or ""),
            )
            if identity_tuple in account_identities:
                errors.append(f"{prefix}: duplicate exact account-position identity")
            account_identities.add(identity_tuple)
            if "holding" not in account:
                errors.append(f"{prefix}: missing holding")
            plan = account.get("semantic_plan")
            if not isinstance(plan, dict):
                errors.append(f"{prefix}: missing semantic_plan")
                continue
            for field in ACCOUNT_PLAN_REQUIRED:
                if field == "ticker" and identity in INTENTIONAL_BLANK_TICKER_KEYS:
                    continue
                if not _present(plan.get(field)):
                    errors.append(f"{prefix}: missing semantic strategy field {field}")
            plan_exposure = plan.get("intended_exposure")
            if not isinstance(plan_exposure, dict) or not {"current_holding", "conditional_buy_volume", "conditional_sell_volume"}.issubset(plan_exposure):
                errors.append(f"{prefix}: intended_exposure quantity fields are incomplete")
            if not isinstance(plan.get("stop_and_recovery_design"), dict) or not _present(plan["stop_and_recovery_design"].get("current_design")):
                errors.append(f"{prefix}: stop_and_recovery_design is missing")
            recommendation = str(plan.get("recommendation", "")).strip().lower()
            if recommendation in GENERIC_RECOMMENDATIONS:
                errors.append(f"{prefix}: generic recommendation is not allowed")
            if str(plan.get("next_gate", "")).strip().lower() in GENERIC_RECOMMENDATIONS:
                errors.append(f"{prefix}: generic next_gate is not allowed")

    validation = master.get("validation", {})
    if validation.get("unique_instruments") != len(instruments):
        errors.append("embedded validation instrument count does not match the instrument identities")
    if validation.get("account_position_rows") != account_rows:
        errors.append("embedded validation account-row count does not match the account identities")
    if validation.get("thin_strategy_fields") != []:
        errors.append("embedded validation reports thin strategy fields")
    if validation.get("generic_recommendation_rows_remaining") != 0:
        errors.append("embedded validation reports generic recommendation rows")
    if validation.get("exact_account_scope_rows") != len(account_identities):
        errors.append("embedded validation exact-scope count does not match the account identities")
    if validation.get("exact_account_scope_complete") is not True:
        errors.append("embedded validation does not report complete exact account scope")
    if validation.get("exact_account_scope") != [
        {"tenant_session_id": "personal", "account_id": "5227886"},
        {"tenant_session_id": "darkcell", "account_id": "7616265"},
    ]:
        errors.append("embedded validation does not report both exact tenant/account scopes")
    contract = validation.get("dynamic_identity_contract", {})
    if contract:
        expected_instruments = {value for value in identities if value}
        if contract.get("schema_version") != 1:
            errors.append("dynamic identity contract schema is invalid")
        if contract.get("instrument_count") != len(expected_instruments):
            errors.append("dynamic identity contract instrument count mismatch")
        if contract.get("account_position_count") != len(account_identities):
            errors.append("dynamic identity contract account-position count mismatch")
        if contract.get("instrument_identity_sha256") != _identity_digest(expected_instruments):
            errors.append("dynamic identity contract instrument digest mismatch")
        if contract.get("account_position_identity_sha256") != _identity_digest(account_identities):
            errors.append("dynamic identity contract account-position digest mismatch")

    return errors


def validate_objective_audit(audit: dict[str, Any]) -> list[str]:
    """Validate the canonical per-instrument objective field contract."""

    errors: list[str] = []
    rows = audit.get("instruments")
    validation = audit.get("validation", {})
    if not isinstance(rows, list):
        return ["objective audit instruments must be a list"]
    if validation.get("unique_instruments") != len(rows):
        errors.append("objective audit embedded instrument count does not match its identities")
    if validation.get("deficient_fields") != []:
        errors.append("objective audit reports deficient fields")
    if validation.get("invalid_review_dates") != []:
        errors.append("objective audit reports invalid review dates")
    if validation.get("broker_mutation") is not False or validation.get("source_code_mutation") is not False:
        errors.append("objective audit authority flags are not read-only")
    if validation.get("live_authorization") != {"personal": False, "darkcell": False}:
        errors.append("objective audit live authorization is not explicitly off")

    required_fields = tuple(validation.get("required_fields", ()))
    if not required_fields:
        errors.append("objective audit required field list is missing")
    account_rows = 0
    identities: list[str] = []
    for index, row in enumerate(rows):
        label = row.get("ticker") or row.get("key") or f"row {index}"
        identities.append(str(row.get("key") or row.get("ticker") or ""))
        for field in required_fields:
            if not _present(row.get(field)):
                errors.append(f"{label}: missing objective field {field}")
        schedule = row.get("next_review_schedule")
        if not isinstance(schedule, dict) or not _present(schedule.get("date")) or schedule.get("timezone") != "Europe/Stockholm":
            errors.append(f"{label}: invalid next_review_schedule")
        exposure = row.get("intended_exposure", {})
        accounts = exposure.get("accounts") if isinstance(exposure, dict) else None
        if not isinstance(accounts, list) or not accounts:
            errors.append(f"{label}: intended_exposure has no account rows")
        else:
            account_rows += len(accounts)
            for account in accounts:
                if not {"account", "tenant_session_id", "account_id", "orderbook_id", "current_holding", "conditional_buy_volume", "conditional_sell_volume"}.issubset(account):
                    errors.append(f"{label}: intended_exposure account identity/exposure fields incomplete")
    if len(set(identities)) != len(identities):
        errors.append("objective audit instrument identity keys are not unique")
    if validation.get("exact_account_position_rows") != account_rows:
        errors.append("objective audit embedded account-row count does not match its identities")
    return errors


def validate_identity_parity(
    master: dict[str, Any],
    objective_audit: dict[str, Any],
) -> list[str]:
    """Require exact instrument and account-position identity parity."""

    errors: list[str] = []
    master_instruments = {
        str(row.get("key") or row.get("ticker") or "").strip()
        for row in master.get("instruments", [])
    }
    master_positions = {
        (
            str(account.get("tenant_session_id") or ""),
            str(account.get("account_id") or ""),
            str(account.get("orderbook_id") or ""),
        )
        for row in master.get("instruments", [])
        for account in row.get("accounts", [])
    }
    audit_instruments, audit_positions = _objective_identity_sets(objective_audit)
    if master_instruments != audit_instruments:
        errors.append("strategy master and objective audit instrument identities do not match")
    if master_positions != audit_positions:
        errors.append("strategy master and objective audit account-position identities do not match")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--objective-audit", type=Path, default=DEFAULT_OBJECTIVE_AUDIT)
    args = parser.parse_args()
    master = json.loads(args.master.read_text(encoding="utf-8"))
    objective_audit = json.loads(args.objective_audit.read_text(encoding="utf-8"))
    errors = validate(master)
    errors.extend(validate_objective_audit(objective_audit))
    errors.extend(validate_identity_parity(master, objective_audit))
    if errors:
        for error in errors:
            print(f"[strategy-master] FAIL: {error}")
        return 1
    print(
        "[strategy-master] PASS: "
        f"{len(master['instruments'])} instruments, "
        f"{sum(len(row.get('accounts', [])) for row in master['instruments'])} "
        "account-position identities, complete semantic and objective plans"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
