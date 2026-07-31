"""Durable, account-scoped strategy metadata for Avanza stop-loss rows.

Avanza's stop-loss payload does not retain the portfolio intent that justified
the row. This registry stores that intent locally and fingerprints the broker
row so stale or incorrectly associated metadata is surfaced instead of trusted.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from avanza_mcp.strategy_intent import (
    BUY_STOPLOSS_STRATEGY_INTENTS,
    SELL_STOPLOSS_STRATEGY_INTENTS,
)
from avanza_mcp.utils import scalar_number

REGISTRY_VERSION = 1
STRATEGY_METADATA_RECORDED = "RECORDED"
STRATEGY_METADATA_MISSING = "MISSING"
STRATEGY_METADATA_STALE_MISMATCH = "STALE_MISMATCH"
STRATEGY_METADATA_REGISTRY_UNAVAILABLE = "REGISTRY_UNAVAILABLE"

_FINGERPRINT_FIELDS = (
    "account_id",
    "stop_loss_id",
    "orderbook_id",
    "side",
    "volume",
    "trigger_type",
    "trigger_value",
    "trigger_value_type",
    "order_price",
    "order_price_type",
    "valid_until",
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalized_token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _normalized_price_type(value: Any) -> str:
    token = _normalized_token(value)
    if token in {"%", "PERCENT", "PERCENTAGE"}:
        return "PERCENTAGE"
    if token in {"SEK", "USD", "EUR", "CURRENCY", "MONETARY"}:
        return "MONETARY"
    return token


def _normalized_number(value: Any) -> float | None:
    parsed = scalar_number(value)
    if parsed is None:
        return None
    return round(float(parsed), 8)


def stoploss_strategy_fingerprint(row: dict[str, Any]) -> dict[str, Any]:
    """Build the stable broker-row identity used to validate local metadata."""

    return {
        "account_id": str(row.get("account_id") or "").strip(),
        "stop_loss_id": str(row.get("stop_loss_id") or "").strip(),
        "orderbook_id": str(row.get("orderbook_id") or row.get("order_book_id") or "").strip(),
        "side": _normalized_token(row.get("side")),
        "volume": _normalized_number(row.get("volume")),
        "trigger_type": _normalized_token(row.get("trigger_type")),
        "trigger_value": _normalized_number(row.get("trigger_value")),
        "trigger_value_type": _normalized_price_type(row.get("trigger_value_type")),
        "order_price": _normalized_number(row.get("order_price")),
        "order_price_type": _normalized_price_type(row.get("order_price_type")),
        "valid_until": str(row.get("valid_until") or "").strip(),
    }


def stoploss_row_from_preview(
    stop_loss_id: str,
    preview: dict[str, Any],
) -> dict[str, Any]:
    trigger = preview.get("stop_loss_trigger")
    trigger = trigger if isinstance(trigger, dict) else {}
    order = preview.get("stop_loss_order_event")
    order = order if isinstance(order, dict) else {}
    return {
        "account_id": str(preview.get("account_id") or ""),
        "stop_loss_id": str(stop_loss_id or ""),
        "orderbook_id": str(preview.get("order_book_id") or ""),
        "side": order.get("type"),
        "volume": order.get("volume"),
        "trigger_type": trigger.get("type"),
        "trigger_value": trigger.get("value"),
        "trigger_value_type": trigger.get("value_type"),
        "order_price": order.get("price"),
        "order_price_type": order.get("price_type"),
        "valid_until": trigger.get("valid_until"),
    }


def _fingerprint_mismatches(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[str]:
    return [
        field
        for field in _FINGERPRINT_FIELDS
        if expected.get(field) != actual.get(field)
    ]


def _validate_strategy_for_row(
    row: dict[str, Any],
    strategy_intent: Any,
    strategy_reason: Any,
) -> tuple[str, str]:
    intent = _normalized_token(strategy_intent)
    reason = str(strategy_reason or "").strip()
    side = _normalized_token(row.get("side"))
    allowed = (
        BUY_STOPLOSS_STRATEGY_INTENTS
        if side == "BUY"
        else SELL_STOPLOSS_STRATEGY_INTENTS
        if side == "SELL"
        else frozenset()
    )
    if not intent:
        raise ValueError("strategy_intent is required.")
    if intent not in allowed:
        choices = ", ".join(sorted(allowed)) if allowed else "a valid BUY or SELL intent"
        raise ValueError(
            f"strategy_intent {intent!r} is incompatible with stop side "
            f"{side or 'UNKNOWN'}; choose {choices}."
        )
    if not reason:
        raise ValueError("strategy_reason is required.")
    if intent == "DEEP_RESIDUAL" and (
        side != "BUY"
        or _normalized_token(row.get("trigger_type")) != "LESS_OR_EQUAL"
        or _normalized_price_type(row.get("trigger_value_type")) != "MONETARY"
        or _normalized_price_type(row.get("order_price_type")) != "MONETARY"
    ):
        raise ValueError(
            "DEEP_RESIDUAL must match a fixed BUY with LESS_OR_EQUAL monetary "
            "trigger and monetary child price."
        )
    return intent, reason


def _empty_registry() -> dict[str, Any]:
    now = _utc_timestamp()
    return {
        "version": REGISTRY_VERSION,
        "created_at": now,
        "updated_at": now,
        "accounts": {},
    }


class StopLossStrategyRegistry:
    """Atomic file-backed registry for stop-loss strategy metadata."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.load_error = ""
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_registry()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("registry root must be an object")
            if int(payload.get("version", -1)) != REGISTRY_VERSION:
                raise ValueError(
                    f"unsupported registry version {payload.get('version')!r}"
                )
            accounts = payload.get("accounts")
            if not isinstance(accounts, dict):
                raise ValueError("registry accounts must be an object")
            return payload
        except Exception as exc:
            self.load_error = str(exc)
            return _empty_registry()

    def _ensure_writable(self) -> None:
        if self.load_error:
            raise RuntimeError(
                "Stop-loss strategy registry could not be loaded; refusing to "
                f"overwrite it: {self.load_error}"
            )

    def _save_locked(self) -> None:
        self._ensure_writable()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data["updated_at"] = _utc_timestamp()
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_path = Path(handle.name)
        try:
            with handle:
                json.dump(self._data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def health(self) -> dict[str, Any]:
        with self._lock:
            accounts = self._data.get("accounts")
            accounts = accounts if isinstance(accounts, dict) else {}
            entry_count = sum(
                len(account.get("stops", {}))
                for account in accounts.values()
                if isinstance(account, dict) and isinstance(account.get("stops"), dict)
            )
            return {
                "available": not bool(self.load_error),
                "load_error": self.load_error or None,
                "path": str(self.path),
                "version": REGISTRY_VERSION,
                "entry_count": entry_count,
                "updated_at": self._data.get("updated_at"),
            }

    def ensure_writable(self) -> None:
        with self._lock:
            self._ensure_writable()

    def _account_stops_locked(
        self,
        account_id: str,
        *,
        create: bool = False,
    ) -> dict[str, dict[str, Any]]:
        accounts = self._data.setdefault("accounts", {})
        token = str(account_id or "").strip()
        if not token:
            raise ValueError("account_id is required.")
        account = accounts.get(token)
        if not isinstance(account, dict):
            if not create:
                return {}
            account = {"stops": {}}
            accounts[token] = account
        stops = account.get("stops")
        if not isinstance(stops, dict):
            if not create:
                return {}
            stops = {}
            account["stops"] = stops
        return stops

    def lookup(self, account_id: str, stop_loss_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._account_stops_locked(account_id).get(
                str(stop_loss_id or "").strip()
            )
            return deepcopy(entry) if isinstance(entry, dict) else None

    def enrich(self, row: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(row)
        fingerprint = stoploss_strategy_fingerprint(row)
        account_id = fingerprint["account_id"]
        stop_loss_id = fingerprint["stop_loss_id"]
        if self.load_error:
            enriched.update(
                {
                    "strategy_intent": None,
                    "strategy_reason": None,
                    "strategy_source": None,
                    "strategy_metadata_status": STRATEGY_METADATA_REGISTRY_UNAVAILABLE,
                    "strategy_metadata_mismatches": [],
                }
            )
            return enriched

        entry = self.lookup(account_id, stop_loss_id)
        if entry is None:
            enriched.update(
                {
                    "strategy_intent": None,
                    "strategy_reason": None,
                    "strategy_source": None,
                    "strategy_metadata_status": STRATEGY_METADATA_MISSING,
                    "strategy_metadata_mismatches": [],
                }
            )
            return enriched

        stored_fingerprint = {
            field: entry.get(field)
            for field in _FINGERPRINT_FIELDS
        }
        mismatches = _fingerprint_mismatches(stored_fingerprint, fingerprint)
        status = (
            STRATEGY_METADATA_RECORDED
            if not mismatches
            else STRATEGY_METADATA_STALE_MISMATCH
        )
        enriched.update(
            {
                "strategy_intent": (
                    entry.get("strategy_intent")
                    if status == STRATEGY_METADATA_RECORDED
                    else None
                ),
                "strategy_reason": (
                    entry.get("strategy_reason")
                    if status == STRATEGY_METADATA_RECORDED
                    else None
                ),
                "strategy_source": (
                    entry.get("source")
                    if status == STRATEGY_METADATA_RECORDED
                    else None
                ),
                "strategy_metadata_status": status,
                "strategy_metadata_mismatches": mismatches,
                "recorded_strategy_intent": entry.get("strategy_intent"),
                "recorded_strategy_reason": entry.get("strategy_reason"),
                "recorded_strategy_source": entry.get("source"),
                "strategy_recorded_at": entry.get("recorded_at"),
                "strategy_updated_at": entry.get("updated_at"),
            }
        )
        return enriched

    def _entry_from_row(
        self,
        row: dict[str, Any],
        *,
        strategy_intent: Any,
        strategy_reason: Any,
        tenant_session_id: str | None,
        source: str,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fingerprint = stoploss_strategy_fingerprint(row)
        missing = [
            field
            for field in ("account_id", "stop_loss_id", "orderbook_id", "side")
            if not fingerprint.get(field)
        ]
        if missing:
            raise ValueError(
                "Cannot register stop-loss strategy metadata without "
                + ", ".join(missing)
                + "."
            )
        intent, reason = _validate_strategy_for_row(
            fingerprint,
            strategy_intent,
            strategy_reason,
        )
        now = _utc_timestamp()
        return {
            **fingerprint,
            "tenant_session_id": str(tenant_session_id or "").strip() or None,
            "strategy_intent": intent,
            "strategy_reason": reason,
            "source": str(source or "UNKNOWN").strip().upper(),
            "recorded_at": (
                existing.get("recorded_at")
                if isinstance(existing, dict) and existing.get("recorded_at")
                else now
            ),
            "updated_at": now,
        }

    def register_existing(
        self,
        row: dict[str, Any],
        *,
        strategy_intent: Any,
        strategy_reason: Any,
        tenant_session_id: str | None,
        source: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_writable()
            fingerprint = stoploss_strategy_fingerprint(row)
            stops = self._account_stops_locked(
                fingerprint["account_id"],
                create=True,
            )
            stop_loss_id = fingerprint["stop_loss_id"]
            entry = self._entry_from_row(
                row,
                strategy_intent=strategy_intent,
                strategy_reason=strategy_reason,
                tenant_session_id=tenant_session_id,
                source=source,
                existing=stops.get(stop_loss_id),
            )
            stops[stop_loss_id] = entry
            self._save_locked()
            return deepcopy(entry)

    def register_many_existing(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        tenant_session_id: str | None,
        source: str,
    ) -> list[dict[str, Any]]:
        """Validate every candidate, then persist all entries in one atomic write."""

        candidates = list(rows)
        with self._lock:
            self._ensure_writable()
            prepared: list[tuple[str, str, dict[str, Any]]] = []
            seen: set[tuple[str, str]] = set()
            for candidate in candidates:
                row = candidate.get("row")
                if not isinstance(row, dict):
                    raise ValueError("Every registry candidate requires a normalized row.")
                fingerprint = stoploss_strategy_fingerprint(row)
                key = (fingerprint["account_id"], fingerprint["stop_loss_id"])
                if key in seen:
                    raise ValueError(
                        f"Duplicate registry candidate for account {key[0]} stop {key[1]}."
                    )
                seen.add(key)
                current = self._account_stops_locked(fingerprint["account_id"]).get(
                    fingerprint["stop_loss_id"]
                )
                entry = self._entry_from_row(
                    row,
                    strategy_intent=candidate.get("strategy_intent"),
                    strategy_reason=candidate.get("strategy_reason"),
                    tenant_session_id=tenant_session_id,
                    source=source,
                    existing=current,
                )
                prepared.append((key[0], key[1], entry))

            for account_id, stop_loss_id, entry in prepared:
                self._account_stops_locked(account_id, create=True)[
                    stop_loss_id
                ] = entry
            if prepared:
                self._save_locked()
            return [deepcopy(entry) for _, _, entry in prepared]

    def register_from_preview(
        self,
        preview: dict[str, Any],
        readback_row: dict[str, Any],
        *,
        tenant_session_id: str | None,
        source: str,
    ) -> dict[str, Any]:
        stop_loss_id = str(readback_row.get("stop_loss_id") or "").strip()
        expected = stoploss_strategy_fingerprint(
            stoploss_row_from_preview(stop_loss_id, preview)
        )
        actual = stoploss_strategy_fingerprint(readback_row)
        mismatches = _fingerprint_mismatches(expected, actual)
        if mismatches:
            raise ValueError(
                "Refusing to persist strategy metadata because live readback "
                f"mismatched the request: {', '.join(mismatches)}."
            )
        return self.register_existing(
            readback_row,
            strategy_intent=preview.get("strategy_intent"),
            strategy_reason=preview.get("strategy_reason"),
            tenant_session_id=tenant_session_id,
            source=source,
        )

    def remove(self, account_id: str, stop_loss_id: str) -> bool:
        with self._lock:
            self._ensure_writable()
            token = str(stop_loss_id or "").strip()
            stops = self._account_stops_locked(account_id)
            if token not in stops:
                return False
            del stops[token]
            self._save_locked()
            return True

    def reconcile_account(
        self,
        account_id: str,
        rows: Iterable[dict[str, Any]],
        *,
        prune_missing: bool = False,
    ) -> dict[str, Any]:
        normalized_rows = [
            self.enrich(row)
            for row in rows
            if str(row.get("account_id") or "").strip() == str(account_id or "").strip()
        ]
        recorded = [
            row
            for row in normalized_rows
            if row.get("strategy_metadata_status") == STRATEGY_METADATA_RECORDED
        ]
        missing = [
            row
            for row in normalized_rows
            if row.get("strategy_metadata_status") == STRATEGY_METADATA_MISSING
        ]
        mismatches = [
            row
            for row in normalized_rows
            if row.get("strategy_metadata_status") == STRATEGY_METADATA_STALE_MISMATCH
        ]
        unavailable = [
            row
            for row in normalized_rows
            if row.get("strategy_metadata_status")
            == STRATEGY_METADATA_REGISTRY_UNAVAILABLE
        ]
        pruned: list[str] = []
        if prune_missing:
            with self._lock:
                self._ensure_writable()
                live_ids = {
                    str(row.get("stop_loss_id") or "").strip()
                    for row in normalized_rows
                    if row.get("stop_loss_id")
                }
                stops = self._account_stops_locked(account_id)
                pruned = sorted(stop_loss_id for stop_loss_id in stops if stop_loss_id not in live_ids)
                for stop_loss_id in pruned:
                    del stops[stop_loss_id]
                if pruned:
                    self._save_locked()
        return {
            "account_id": str(account_id or "").strip(),
            "complete": not missing and not mismatches and not unavailable,
            "row_count": len(normalized_rows),
            "recorded_count": len(recorded),
            "missing_count": len(missing),
            "mismatch_count": len(mismatches),
            "registry_unavailable_count": len(unavailable),
            "missing_stop_loss_ids": [row.get("stop_loss_id") for row in missing],
            "mismatched_stop_loss_ids": [
                row.get("stop_loss_id") for row in mismatches
            ],
            "pruned_count": len(pruned),
            "pruned_stop_loss_ids": pruned,
            "registry": self.health(),
        }
