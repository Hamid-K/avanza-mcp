"""Fail-closed consistency audit for reviewed portfolio strategy artifacts.

The broker-facing audits prove live holdings and order fingerprints. This
module proves that the instrument master, account clean sheet, and private
position registry carry the same reviewed strategy semantics. It is read-only
and never authorizes or performs a trade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REGISTRY_SEMANTIC_FIELDS = (
    "instrument",
    "strategy_class",
    "horizon",
    "thesis",
    "gate",
    "audit_status",
    "recommendation",
    "priority",
    "bucket",
    "next_gate",
    "ticker",
    "venue",
    "proposed_correction",
)

LIVE_FIELDS = (
    "holding",
    "active_buy_volume",
    "active_sell_volume",
    "active_buy_count",
    "active_sell_count",
)

MASTER_FIELD_MAP = {
    "priority": "priority",
    "bucket": "bucket",
    "method": "method",
    "primary_factor": "primary_factor",
    "overlapping_themes": "overlapping_themes",
    "thesis": "thesis",
    "gate": "decision_gate",
    "recommendation": "decision",
    "catalyst": "catalyst",
    "add_gate": "add_gate",
    "sell_gate": "sell_gate",
    "invalidation": "invalidation",
    "risk_budget_rule": "risk_budget_rule",
    "next_gate": "next_review",
    "friction_rule": "friction_rule",
    "loss_recovery_rule": "loss_recovery_rule",
}

MASTER_ACCOUNT_DIRECT_FIELDS = (
    "instrument",
    "ticker",
    "venue",
    "horizon",
    "proposed_correction",
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return data


def _normalized(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 8)
    return value


def _issue(
    code: str,
    *,
    account: str | None = None,
    account_id: str | None = None,
    orderbook_id: str | None = None,
    field: str | None = None,
    expected: Any = None,
    actual: Any = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "code": code,
            "account": account,
            "account_id": account_id,
            "orderbook_id": orderbook_id,
            "field": field,
            "expected": expected,
            "actual": actual,
            "detail": detail,
        }.items()
        if value is not None
    }


def _clean_positions(clean_sheet: Mapping[str, Any]) -> list[dict[str, Any]]:
    accounts = clean_sheet.get("accounts")
    if not isinstance(accounts, dict):
        raise ValueError("Clean sheet must contain an accounts object.")

    rows: list[dict[str, Any]] = []
    for account, payload in accounts.items():
        if not isinstance(payload, dict) or not isinstance(payload.get("positions"), list):
            raise ValueError(f"Clean-sheet account {account!r} has no positions list.")
        for position in payload["positions"]:
            if not isinstance(position, dict):
                raise ValueError(f"Clean-sheet account {account!r} contains a non-object row.")
            row = dict(position)
            row["_account"] = str(account)
            rows.append(row)
    return rows


def _registry_positions(registry: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    accounts = registry.get("accounts")
    if not isinstance(accounts, dict):
        raise ValueError("Position registry must contain an accounts object.")

    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for account_id, payload in accounts.items():
        positions = payload.get("positions") if isinstance(payload, dict) else None
        if not isinstance(positions, dict):
            raise ValueError(f"Registry account {account_id!r} has no positions object.")
        for orderbook_id, position in positions.items():
            if not isinstance(position, dict):
                raise ValueError(
                    f"Registry account {account_id!r} position {orderbook_id!r} is not an object."
                )
            key = (str(account_id), str(orderbook_id))
            if key in rows:
                raise ValueError(f"Duplicate registry position {key!r}.")
            rows[key] = position
    return rows


def _master_instruments(
    master: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    instruments = master.get("instruments")
    if not isinstance(instruments, list):
        raise ValueError("Instrument master must contain an instruments list.")

    by_orderbook: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    for instrument in instruments:
        if not isinstance(instrument, dict):
            raise ValueError("Instrument master contains a non-object row.")
        normalized.append(instrument)
        orderbook_ids = instrument.get("orderbook_ids")
        if not isinstance(orderbook_ids, list) or not orderbook_ids:
            issues.append(
                _issue(
                    "MASTER_ORDERBOOK_IDS_MISSING",
                    detail=str(instrument.get("key") or instrument.get("instrument") or "unknown"),
                )
            )
            continue
        for orderbook_id in orderbook_ids:
            token = str(orderbook_id)
            if token in by_orderbook:
                issues.append(
                    _issue(
                        "MASTER_ORDERBOOK_DUPLICATE",
                        orderbook_id=token,
                        detail="Orderbook belongs to more than one master instrument.",
                    )
                )
            else:
                by_orderbook[token] = instrument
    return normalized, by_orderbook, issues


def _active_counts(position: Mapping[str, Any]) -> tuple[int, int]:
    rows = position.get("active_rows")
    if not isinstance(rows, list):
        return 0, 0
    buy = 0
    sell = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or "").strip().upper()
        if side == "BUY":
            buy += 1
        elif side == "SELL":
            sell += 1
    return buy, sell


def _master_account(
    instrument: Mapping[str, Any], account: str
) -> dict[str, Any] | None:
    rows = instrument.get("accounts")
    if not isinstance(rows, list):
        return None
    matches = [row for row in rows if isinstance(row, dict) and str(row.get("account")) == account]
    return matches[0] if len(matches) == 1 else None


def _master_expected(
    instrument: Mapping[str, Any], account_row: Mapping[str, Any], clean_field: str
) -> Any:
    account_plan = account_row.get("semantic_plan")
    if isinstance(account_plan, dict) and clean_field in account_plan:
        return account_plan[clean_field]
    return instrument.get(MASTER_FIELD_MAP[clean_field])


def audit_strategy_semantics(
    master: Mapping[str, Any],
    clean_sheet: Mapping[str, Any],
    registry: Mapping[str, Any],
    account_map: Mapping[str, str],
    *,
    expected_instruments: int | None = None,
    expected_positions: int | None = None,
) -> dict[str, Any]:
    """Compare all reviewed semantic sources and return a fail-closed report."""

    clean_rows = _clean_positions(clean_sheet)
    registry_rows = _registry_positions(registry)
    master_rows, master_by_orderbook, issues = _master_instruments(master)
    observed_clean_keys: set[tuple[str, str]] = set()
    audited = 0

    clean_accounts = {row["_account"] for row in clean_rows}
    missing_account_maps = sorted(clean_accounts - set(account_map))
    for account in missing_account_maps:
        issues.append(_issue("ACCOUNT_MAPPING_MISSING", account=account))

    mapped_ids = [str(account_map[name]) for name in clean_accounts if name in account_map]
    if len(mapped_ids) != len(set(mapped_ids)):
        issues.append(
            _issue(
                "ACCOUNT_MAPPING_DUPLICATE_ID",
                detail="Each clean-sheet account label must map to one distinct registry account.",
            )
        )

    for clean in clean_rows:
        account = clean["_account"]
        account_id = str(account_map.get(account) or "")
        orderbook_id = str(clean.get("orderbook_id") or "").strip()
        if not orderbook_id:
            issues.append(_issue("CLEAN_ORDERBOOK_ID_MISSING", account=account))
            continue
        if not account_id:
            continue

        key = (account_id, orderbook_id)
        if key in observed_clean_keys:
            issues.append(
                _issue(
                    "CLEAN_POSITION_DUPLICATE",
                    account=account,
                    account_id=account_id,
                    orderbook_id=orderbook_id,
                )
            )
            continue
        observed_clean_keys.add(key)

        registered = registry_rows.get(key)
        if registered is None:
            issues.append(
                _issue(
                    "REGISTRY_POSITION_MISSING",
                    account=account,
                    account_id=account_id,
                    orderbook_id=orderbook_id,
                )
            )
        else:
            for field in REGISTRY_SEMANTIC_FIELDS:
                expected = _normalized(clean.get(field))
                actual = _normalized(registered.get(field))
                if expected != actual:
                    issues.append(
                        _issue(
                            "REGISTRY_SEMANTIC_MISMATCH",
                            account=account,
                            account_id=account_id,
                            orderbook_id=orderbook_id,
                            field=field,
                            expected=expected,
                            actual=actual,
                        )
                    )

            active_buy_count, active_sell_count = _active_counts(clean)
            live_expected = {
                "holding": clean.get("holding"),
                "active_buy_volume": clean.get("active_buy_volume"),
                "active_sell_volume": clean.get("active_sell_volume"),
                "active_buy_count": active_buy_count,
                "active_sell_count": active_sell_count,
            }
            for field in LIVE_FIELDS:
                expected = _normalized(live_expected[field])
                actual = _normalized(registered.get(field))
                if expected != actual:
                    issues.append(
                        _issue(
                            "REGISTRY_LIVE_STATE_MISMATCH",
                            account=account,
                            account_id=account_id,
                            orderbook_id=orderbook_id,
                            field=field,
                            expected=expected,
                            actual=actual,
                        )
                    )

        master_instrument = master_by_orderbook.get(orderbook_id)
        if master_instrument is None:
            issues.append(
                _issue(
                    "MASTER_INSTRUMENT_MISSING",
                    account=account,
                    account_id=account_id,
                    orderbook_id=orderbook_id,
                )
            )
            continue

        master_account = _master_account(master_instrument, account)
        if master_account is None:
            issues.append(
                _issue(
                    "MASTER_ACCOUNT_POSITION_MISSING_OR_DUPLICATE",
                    account=account,
                    account_id=account_id,
                    orderbook_id=orderbook_id,
                )
            )
            continue

        semantic_schema_version = master.get("semantic_schema_version")
        account_plan = master_account.get("semantic_plan")
        if semantic_schema_version is not None and not isinstance(account_plan, dict):
            issues.append(
                _issue(
                    "MASTER_ACCOUNT_SEMANTIC_PLAN_MISSING",
                    account=account,
                    account_id=account_id,
                    orderbook_id=orderbook_id,
                )
            )
            account_plan = {}

        if semantic_schema_version is not None and isinstance(account_plan, dict):
            for field in MASTER_ACCOUNT_DIRECT_FIELDS:
                expected = _normalized(clean.get(field))
                actual = _normalized(account_plan.get(field))
                if expected != actual:
                    issues.append(
                        _issue(
                            "MASTER_SEMANTIC_MISMATCH",
                            account=account,
                            account_id=account_id,
                            orderbook_id=orderbook_id,
                            field=field,
                            expected=expected,
                            actual=actual,
                        )
                    )

        for field in ("holding", "active_buy_volume", "active_sell_volume", "audit_status"):
            expected = _normalized(clean.get(field))
            actual = _normalized(master_account.get(field))
            if expected != actual:
                issues.append(
                    _issue(
                        "MASTER_ACCOUNT_STATE_MISMATCH",
                        account=account,
                        account_id=account_id,
                        orderbook_id=orderbook_id,
                        field=field,
                        expected=expected,
                        actual=actual,
                    )
                )

        expected_strategy_class = (
            account_plan.get("strategy_class")
            if isinstance(account_plan, dict) and "strategy_class" in account_plan
            else None
        )
        clean_strategy_class = _normalized(clean.get("strategy_class"))
        if expected_strategy_class is not None:
            strategy_class_match = clean_strategy_class == _normalized(expected_strategy_class)
            actual_strategy_class = _normalized(expected_strategy_class)
        else:
            strategy_classes = [_normalized(item) for item in master_instrument.get("strategy_classes", [])]
            strategy_class_match = clean_strategy_class in strategy_classes
            actual_strategy_class = strategy_classes
        if not strategy_class_match:
            issues.append(
                _issue(
                    "MASTER_SEMANTIC_MISMATCH",
                    account=account,
                    account_id=account_id,
                    orderbook_id=orderbook_id,
                    field="strategy_class",
                    expected=clean_strategy_class,
                    actual=actual_strategy_class,
                )
            )

        for clean_field in MASTER_FIELD_MAP:
            expected = _normalized(clean.get(clean_field))
            actual = _normalized(_master_expected(master_instrument, master_account, clean_field))
            if expected != actual:
                issues.append(
                    _issue(
                        "MASTER_SEMANTIC_MISMATCH",
                        account=account,
                        account_id=account_id,
                        orderbook_id=orderbook_id,
                        field=clean_field,
                        expected=expected,
                        actual=actual,
                    )
                )
        audited += 1

    for account_id, orderbook_id in sorted(set(registry_rows) - observed_clean_keys):
        issues.append(
            _issue(
                "REGISTRY_POSITION_UNEXPECTED",
                account_id=account_id,
                orderbook_id=orderbook_id,
            )
        )

    if expected_instruments is not None and len(master_rows) != expected_instruments:
        issues.append(
            _issue(
                "MASTER_INSTRUMENT_COUNT_MISMATCH",
                expected=expected_instruments,
                actual=len(master_rows),
            )
        )
    if expected_positions is not None and len(clean_rows) != expected_positions:
        issues.append(
            _issue(
                "CLEAN_POSITION_COUNT_MISMATCH",
                expected=expected_positions,
                actual=len(clean_rows),
            )
        )
    if expected_positions is not None and len(registry_rows) != expected_positions:
        issues.append(
            _issue(
                "REGISTRY_POSITION_COUNT_MISMATCH",
                expected=expected_positions,
                actual=len(registry_rows),
            )
        )

    counters: dict[str, int] = {}
    for item in issues:
        code = str(item["code"])
        counters[code] = counters.get(code, 0) + 1

    return {
        "ok": not issues,
        "generated_at": _timestamp(),
        "broker_mutation": False,
        "trade_authority": False,
        "coverage": {
            "master_instruments": len(master_rows),
            "clean_positions": len(clean_rows),
            "registry_positions": len(registry_rows),
            "audited_positions": audited,
        },
        "account_map": {str(key): str(value) for key, value in account_map.items()},
        "issue_count": len(issues),
        "issue_counts": counters,
        "issues": issues,
    }


def _account_map(values: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        label, separator, account_id = value.partition("=")
        label = label.strip()
        account_id = account_id.strip()
        if not separator or not label or not account_id:
            raise ValueError(f"Invalid account map {value!r}; expected LABEL=ACCOUNT_ID.")
        if label in result:
            raise ValueError(f"Duplicate account label {label!r}.")
        result[label] = account_id
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit strategy semantics across master, clean sheet, and private registry."
    )
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--clean-sheet", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument(
        "--account-map",
        action="append",
        default=[],
        metavar="LABEL=ACCOUNT_ID",
        help="Repeat once per clean-sheet account label.",
    )
    parser.add_argument("--expected-instruments", type=int)
    parser.add_argument("--expected-positions", type=int)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        account_map = _account_map(args.account_map)
        report = audit_strategy_semantics(
            _load_object(args.master),
            _load_object(args.clean_sheet),
            _load_object(args.registry),
            account_map,
            expected_instruments=args.expected_instruments,
            expected_positions=args.expected_positions,
        )
        report["sources"] = {
            "master": str(args.master.resolve()),
            "master_sha256": _digest(args.master),
            "clean_sheet": str(args.clean_sheet.resolve()),
            "clean_sheet_sha256": _digest(args.clean_sheet),
            "registry": str(args.registry.resolve()),
            "registry_sha256": _digest(args.registry),
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "ok": False,
            "generated_at": _timestamp(),
            "broker_mutation": False,
            "trade_authority": False,
            "issue_count": 1,
            "issue_counts": {"AUDIT_INPUT_ERROR": 1},
            "issues": [_issue("AUDIT_INPUT_ERROR", detail=str(exc))],
        }

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
