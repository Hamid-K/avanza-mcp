#!/usr/bin/env python3
"""Validate the static instrument-strategy master without authorizing trades."""

from __future__ import annotations

import argparse
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


def validate(master: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    instruments = master.get("instruments")
    if not isinstance(instruments, list):
        return ["instruments must be a list"]

    identities = [str(row.get("key") or row.get("ticker") or "") for row in instruments]
    if len(instruments) != 65:
        errors.append(f"expected 65 instruments, found {len(instruments)}")
    if len(set(identities)) != len(identities):
        errors.append("instrument identity keys are not unique")

    account_rows = 0
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

    if account_rows != 107:
        errors.append(f"expected 107 account-position rows, found {account_rows}")

    validation = master.get("validation", {})
    if validation.get("unique_instruments") != 65:
        errors.append("embedded validation does not report 65 instruments")
    if validation.get("account_position_rows") != 107:
        errors.append("embedded validation does not report 107 account rows")
    if validation.get("thin_strategy_fields") != []:
        errors.append("embedded validation reports thin strategy fields")
    if validation.get("generic_recommendation_rows_remaining") != 0:
        errors.append("embedded validation reports generic recommendation rows")
    if validation.get("exact_account_scope_rows") != 107:
        errors.append("embedded validation does not report 107 exact account scopes")
    if validation.get("exact_account_scope_complete") is not True:
        errors.append("embedded validation does not report complete exact account scope")
    if validation.get("exact_account_scope") != [
        {"tenant_session_id": "personal", "account_id": "5227886"},
        {"tenant_session_id": "darkcell", "account_id": "7616265"},
    ]:
        errors.append("embedded validation does not report both exact tenant/account scopes")

    return errors


def validate_objective_audit(audit: dict[str, Any]) -> list[str]:
    """Validate the canonical per-instrument objective field contract."""

    errors: list[str] = []
    rows = audit.get("instruments")
    validation = audit.get("validation", {})
    if not isinstance(rows, list):
        return ["objective audit instruments must be a list"]
    if len(rows) != 65:
        errors.append(f"objective audit expected 65 instruments, found {len(rows)}")
    if validation.get("unique_instruments") != 65:
        errors.append("objective audit embedded instrument count is not 65")
    if validation.get("exact_account_position_rows") != 107:
        errors.append("objective audit embedded account-row count is not 107")
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
    if account_rows != 107:
        errors.append(f"objective audit expected 107 intended-exposure rows, found {account_rows}")
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
    if errors:
        for error in errors:
            print(f"[strategy-master] FAIL: {error}")
        return 1
    print("[strategy-master] PASS: 65 instruments, 107 account-position rows, complete semantic and objective plans")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
