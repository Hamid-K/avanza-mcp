"""Guarded synchronization of reviewed strategy semantics across artifacts.

This control copies account-specific reviewed semantics from the private
position registry into the clean sheet and embeds the resulting exact account
plan in the instrument master. It verifies live fingerprints first, changes no
broker or registry state, and grants no trade authority.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from avanza_mcp.strategy_semantic_audit import (
    LIVE_FIELDS,
    MASTER_ACCOUNT_DIRECT_FIELDS,
    MASTER_FIELD_MAP,
    REGISTRY_SEMANTIC_FIELDS,
    _account_map,
    _active_counts,
    _digest,
    _load_object,
    _master_instruments,
    _normalized,
    _registry_positions,
)

SEMANTIC_SCHEMA_VERSION = 1


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def synchronize_strategy_semantics(
    master: Mapping[str, Any],
    clean_sheet: Mapping[str, Any],
    registry: Mapping[str, Any],
    account_map: Mapping[str, str],
    *,
    reason: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return synchronized copies plus a field-level change report."""

    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("A documented reconciliation reason is required.")

    clean = deepcopy(dict(clean_sheet))
    reconciled_master = deepcopy(dict(master))
    registry_rows = _registry_positions(registry)
    changes: list[dict[str, Any]] = []

    accounts = clean.get("accounts")
    if not isinstance(accounts, dict):
        raise ValueError("Clean sheet must contain an accounts object.")

    clean_index: dict[tuple[str, str], dict[str, Any]] = {}
    for account, payload in accounts.items():
        account_id = str(account_map.get(str(account)) or "")
        if not account_id:
            raise ValueError(f"Missing account mapping for {account!r}.")
        positions = payload.get("positions") if isinstance(payload, dict) else None
        if not isinstance(positions, list):
            raise ValueError(f"Clean-sheet account {account!r} has no positions list.")

        for position in positions:
            if not isinstance(position, dict):
                raise ValueError(f"Clean-sheet account {account!r} contains a non-object row.")
            orderbook_id = str(position.get("orderbook_id") or "").strip()
            key = (account_id, orderbook_id)
            registered = registry_rows.get(key)
            if registered is None:
                raise ValueError(f"Registry position {key!r} is missing.")

            active_buy_count, active_sell_count = _active_counts(position)
            live_expected = {
                "holding": position.get("holding"),
                "active_buy_volume": position.get("active_buy_volume"),
                "active_sell_volume": position.get("active_sell_volume"),
                "active_buy_count": active_buy_count,
                "active_sell_count": active_sell_count,
            }
            for field in LIVE_FIELDS:
                if _normalized(live_expected[field]) != _normalized(registered.get(field)):
                    raise ValueError(
                        f"Live fingerprint mismatch for {key!r} field {field!r}; "
                        "refresh and review before semantic synchronization."
                    )

            for field in REGISTRY_SEMANTIC_FIELDS:
                if field not in registered:
                    raise ValueError(f"Registry position {key!r} lacks semantic field {field!r}.")
                before = position.get(field)
                after = registered.get(field)
                if _normalized(before) != _normalized(after):
                    changes.append(
                        {
                            "source": "registry_to_clean_sheet",
                            "account": str(account),
                            "account_id": account_id,
                            "orderbook_id": orderbook_id,
                            "field": field,
                            "before": before,
                            "after": after,
                        }
                    )
                    position[field] = deepcopy(after)
            clean_index[(str(account), orderbook_id)] = position

    master_rows, _, master_issues = _master_instruments(reconciled_master)
    if master_issues:
        raise ValueError(f"Master orderbook mapping is invalid: {master_issues!r}")

    semantic_fields = {
        *MASTER_ACCOUNT_DIRECT_FIELDS,
        *MASTER_FIELD_MAP.keys(),
        "strategy_class",
        "audit_status",
    }
    for instrument in master_rows:
        orderbook_ids = {str(value) for value in instrument.get("orderbook_ids", [])}
        account_rows = instrument.get("accounts")
        if not isinstance(account_rows, list):
            raise ValueError(f"Master instrument {instrument.get('key')!r} has no accounts list.")

        instrument_theses: list[str] = []
        for account_row in account_rows:
            if not isinstance(account_row, dict):
                raise ValueError("Master account row is not an object.")
            account = str(account_row.get("account") or "")
            matches = [
                row
                for (row_account, orderbook_id), row in clean_index.items()
                if row_account == account and orderbook_id in orderbook_ids
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"Expected one clean row for master {instrument.get('key')!r} / {account!r}, "
                    f"found {len(matches)}."
                )
            clean_position = matches[0]
            clean_audit_status = deepcopy(clean_position.get("audit_status"))
            if _normalized(account_row.get("audit_status")) != _normalized(
                clean_audit_status
            ):
                changes.append(
                    {
                        "source": "clean_sheet_to_master_account_state",
                        "account": account,
                        "orderbook_id": str(clean_position.get("orderbook_id")),
                        "field": "audit_status",
                        "before": account_row.get("audit_status"),
                        "after": clean_audit_status,
                    }
                )
                account_row["audit_status"] = clean_audit_status
            semantic_plan = {
                field: deepcopy(clean_position.get(field)) for field in sorted(semantic_fields)
            }
            before_plan = account_row.get("semantic_plan")
            if _normalized(before_plan) != _normalized(semantic_plan):
                changes.append(
                    {
                        "source": "clean_sheet_to_master_account_plan",
                        "account": account,
                        "orderbook_id": str(clean_position.get("orderbook_id")),
                        "field": "semantic_plan",
                    }
                )
                account_row["semantic_plan"] = semantic_plan
            thesis = str(clean_position.get("thesis") or "").strip()
            if thesis and thesis not in instrument_theses:
                instrument_theses.append(thesis)

        if instrument_theses:
            canonical_thesis = instrument_theses[0]
            if _normalized(instrument.get("thesis")) != _normalized(canonical_thesis):
                changes.append(
                    {
                        "source": "clean_sheet_to_master",
                        "instrument": instrument.get("key"),
                        "field": "thesis",
                        "before": instrument.get("thesis"),
                        "after": canonical_thesis,
                    }
                )
                instrument["thesis"] = canonical_thesis

    now = _timestamp()
    clean["strategy_semantic_reconciled_at"] = now
    clean["strategy_semantic_reconciliation"] = {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "reason": reason,
        "registry_authoritative_fields": list(REGISTRY_SEMANTIC_FIELDS),
        "live_fingerprint_required": list(LIVE_FIELDS),
        "changed_fields": sum(
            1 for change in changes if change["source"] == "registry_to_clean_sheet"
        ),
        "broker_mutation": False,
        "registry_mutation": False,
        "trade_authority": False,
    }
    reconciled_master["generated_at"] = now
    reconciled_master["semantic_schema_version"] = SEMANTIC_SCHEMA_VERSION
    reconciled_master["strategy_semantic_reconciliation_overlay"] = {
        "as_of": now,
        "reason": reason,
        "account_position_rows": len(clean_index),
        "account_semantic_plans": sum(
            len(instrument.get("accounts", [])) for instrument in master_rows
        ),
        "broker_mutation": False,
        "registry_mutation": False,
        "trade_authority": False,
    }

    report = {
        "ok": True,
        "generated_at": now,
        "reason": reason,
        "broker_mutation": False,
        "registry_mutation": False,
        "trade_authority": False,
        "coverage": {
            "master_instruments": len(master_rows),
            "clean_positions": len(clean_index),
            "registry_positions": len(registry_rows),
        },
        "change_count": len(changes),
        "registry_to_clean_field_changes": sum(
            1 for change in changes if change["source"] == "registry_to_clean_sheet"
        ),
        "master_account_plan_changes": sum(
            1
            for change in changes
            if change["source"] == "clean_sheet_to_master_account_plan"
        ),
        "master_account_state_changes": sum(
            1
            for change in changes
            if change["source"] == "clean_sheet_to_master_account_state"
        ),
        "master_thesis_changes": sum(
            1 for change in changes if change["source"] == "clean_sheet_to_master"
        ),
        "changes": changes,
    }
    return reconciled_master, clean, report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize reviewed strategy semantics across analysis artifacts."
    )
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--clean-sheet", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--account-map", action="append", default=[], metavar="LABEL=ACCOUNT_ID")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Atomically write the synchronized clean sheet and master in place.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        master_path = args.master.resolve()
        clean_path = args.clean_sheet.resolve()
        registry_path = args.registry.resolve()
        master_before = _digest(master_path)
        clean_before = _digest(clean_path)
        registry_hash = _digest(registry_path)
        master, clean, report = synchronize_strategy_semantics(
            _load_object(master_path),
            _load_object(clean_path),
            _load_object(registry_path),
            _account_map(args.account_map),
            reason=args.reason,
        )
        report["dry_run"] = not args.confirm
        report["sources"] = {
            "master": str(master_path),
            "master_sha256_before": master_before,
            "clean_sheet": str(clean_path),
            "clean_sheet_sha256_before": clean_before,
            "registry": str(registry_path),
            "registry_sha256": registry_hash,
        }
        if args.confirm:
            _atomic_write(clean_path, clean)
            _atomic_write(master_path, master)
            report["sources"]["master_sha256_after"] = _digest(master_path)
            report["sources"]["clean_sheet_sha256_after"] = _digest(clean_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "ok": False,
            "generated_at": _timestamp(),
            "broker_mutation": False,
            "registry_mutation": False,
            "trade_authority": False,
            "error": str(exc),
        }

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
