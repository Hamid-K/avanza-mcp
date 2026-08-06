"""Durable, account-scoped strategy plans for every tracked position.

The broker exposes holdings and orders, but it does not retain the investment
thesis, intended horizon, or reviewed aggregate exposure. This registry keeps
that control data locally and compares it with live holdings and order state.
It never authorizes or places a broker order.
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

from avanza_mcp.utils import scalar_number

REGISTRY_VERSION = 1
POSITION_STRATEGY_RECORDED = "RECORDED"
POSITION_STRATEGY_MISSING = "MISSING"
POSITION_STRATEGY_STALE_MISMATCH = "STALE_MISMATCH"
POSITION_STRATEGY_REGISTRY_UNAVAILABLE = "REGISTRY_UNAVAILABLE"

_LIVE_STATE_FIELDS = (
    "account_id",
    "orderbook_id",
    "holding",
    "active_buy_volume",
    "active_sell_volume",
    "active_buy_count",
    "active_sell_count",
    "open_buy_volume",
    "open_sell_volume",
    "open_buy_count",
    "open_sell_count",
)
_STOP_EXPOSURE_FIELDS = {
    "active_buy_volume",
    "active_sell_volume",
    "active_buy_count",
    "active_sell_count",
}
_OPEN_ORDER_EXPOSURE_FIELDS = {
    "open_buy_volume",
    "open_sell_volume",
    "open_buy_count",
    "open_sell_count",
}
_POSITION_AUDIT_EXCEPTION_ALLOWED_FIELDS = {"holding"}
_POSITION_AUDIT_EXCEPTION_KINDS = {
    "USER_CONTROLLED_ALLOCATION",
    "POST_MANUAL_EXIT_DRIFT",
}
_TERMINAL_ORDER_STATUSES = {
    "CANCELLED",
    "CANCELED",
    "DELETED",
    "EXPIRED",
    "FAILED",
    "FAULTY",
    "FELAKTIG",
    "FILLED",
    "REJECTED",
}
_REQUIRED_PLAN_TEXT_FIELDS = (
    "instrument",
    "strategy_class",
    "horizon",
    "thesis",
    "gate",
    "audit_status",
    "recommendation",
    "priority",
    "bucket",
    "stance",
    "next_gate",
)
_OPTIONAL_PLAN_TEXT_FIELDS = (
    "ticker",
    "venue",
)
_PLAN_TEXT_FIELDS = (
    *_REQUIRED_PLAN_TEXT_FIELDS,
    *_OPTIONAL_PLAN_TEXT_FIELDS,
)


def _normalize_audit_exception(value: Any) -> dict[str, Any] | None:
    """Validate metadata that explains intentional drift without clearing it."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("audit_exception must be an object or null.")
    kind = _normalized_token(value.get("kind"))
    reason = str(value.get("reason") or "").strip()
    owner = str(value.get("owner") or "").strip()
    review_due = str(value.get("review_due") or "").strip()
    allowed_fields = value.get("allowed_mismatches")
    if kind not in _POSITION_AUDIT_EXCEPTION_KINDS:
        raise ValueError(
            "audit_exception.kind must be USER_CONTROLLED_ALLOCATION or "
            "POST_MANUAL_EXIT_DRIFT."
        )
    if not reason or not owner or not review_due:
        raise ValueError(
            "audit_exception requires reason, owner, and review_due."
        )
    if not isinstance(allowed_fields, list) or not allowed_fields:
        raise ValueError("audit_exception.allowed_mismatches must be a non-empty list.")
    normalized_fields = sorted({_normalized_token(field).lower() for field in allowed_fields})
    if not set(normalized_fields).issubset(_POSITION_AUDIT_EXCEPTION_ALLOWED_FIELDS):
        raise ValueError("audit_exception may acknowledge holding drift only.")
    return {
        "kind": kind,
        "reason": reason,
        "owner": owner,
        "review_due": review_due,
        "allowed_mismatches": normalized_fields,
        "rebaseline_authorized": False,
    }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalized_token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _normalized_number(value: Any) -> float:
    parsed = scalar_number(value)
    return round(float(parsed or 0.0), 8)


def _normalized_count(value: Any) -> int:
    parsed = scalar_number(value)
    if parsed is None or float(parsed) < 0 or not float(parsed).is_integer():
        raise ValueError(f"Expected a non-negative integer count, got {value!r}.")
    return int(parsed)


def _row_account_id(row: dict[str, Any]) -> str:
    return str(
        row.get("account_id")
        or row.get("Account ID")
        or row.get("accountId")
        or ""
    ).strip()


def _row_orderbook_id(row: dict[str, Any]) -> str:
    return str(
        row.get("orderbook_id")
        or row.get("order_book_id")
        or row.get("Order Book ID")
        or row.get("orderBookId")
        or ""
    ).strip()


def _row_stock(row: dict[str, Any]) -> str:
    return str(
        row.get("stock")
        or row.get("Stock")
        or row.get("instrument")
        or row.get("instrument_name")
        or ""
    ).strip()


def _row_side(row: dict[str, Any]) -> str:
    return _normalized_token(row.get("side") or row.get("Side"))


def _row_volume(row: dict[str, Any]) -> float:
    return _normalized_number(row.get("volume", row.get("Volume")))


def _is_active_stop(row: dict[str, Any]) -> bool:
    return _normalized_token(row.get("status") or row.get("Status")) == "ACTIVE"


def _is_active_open_order(row: dict[str, Any]) -> bool:
    status = _normalized_token(row.get("status") or row.get("Status"))
    return status not in _TERMINAL_ORDER_STATUSES


def position_strategy_live_fingerprint(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize the live holdings and aggregate order state used for drift."""

    return {
        "account_id": _row_account_id(row),
        "orderbook_id": _row_orderbook_id(row),
        "holding": _normalized_number(row.get("holding", row.get("volume"))),
        "active_buy_volume": _normalized_number(row.get("active_buy_volume")),
        "active_sell_volume": _normalized_number(row.get("active_sell_volume")),
        "active_buy_count": _normalized_count(row.get("active_buy_count", 0)),
        "active_sell_count": _normalized_count(row.get("active_sell_count", 0)),
        "open_buy_volume": _normalized_number(row.get("open_buy_volume")),
        "open_sell_volume": _normalized_number(row.get("open_sell_volume")),
        "open_buy_count": _normalized_count(row.get("open_buy_count", 0)),
        "open_sell_count": _normalized_count(row.get("open_sell_count", 0)),
    }


def _position_percent(row: dict[str, Any], *keys: str) -> float | None:
    """Read a rendered percentage without treating missing data as zero."""

    for key in keys:
        value = row.get(key)
        if value is None or str(value).strip() == "":
            continue
        cleaned = str(value).strip().replace("%", "").replace(",", "")
        parsed = scalar_number(cleaned)
        if parsed is not None:
            return float(parsed)
    return None


def build_event_protection_screen(
    positions: Iterable[dict[str, Any]],
    strategy_positions: Iterable[dict[str, Any]],
    *,
    material_move_threshold_percent: float = 5.0,
) -> dict[str, Any]:
    """Build a read-only event/protection triage screen.

    The threshold is a surfacing aid, not a universal stop or sell rule. The
    screen forces a plan-level event decision when a holding is event-sensitive
    or makes a material move without active SELL protection, while preserving
    the distinction between a review flag and trade authorization.
    """

    threshold = abs(float(material_move_threshold_percent))
    strategy_by_orderbook = {
        str(row.get("orderbook_id") or ""): row
        for row in strategy_positions
        if row.get("orderbook_id")
    }
    event_tokens = (
        "EVENT",
        "EARNINGS",
        "REPORT",
        "GUIDANCE",
        "CATALYST",
        "AFTER-CLOSE",
        "BEFORE-OPEN",
        "POST-EVENT",
        "PUBLICATION",
    )
    decision_tokens = (
        "HOLD",
        "REDUCE",
        "SELL",
        "TRIM",
        "RECLAIM",
        "AVOID",
        "ADD",
        "REVIEW",
    )
    rows: list[dict[str, Any]] = []
    for position in positions:
        orderbook_id = str(
            position.get("orderbook_id") or position.get("Order Book ID") or ""
        ).strip()
        strategy_row = strategy_by_orderbook.get(orderbook_id, {})
        plan = strategy_row.get("position_strategy") or {}
        day_percent = _position_percent(position, "Day %", "day_percent")
        profit_percent = _position_percent(position, "Profit %", "profit_percent")
        holding = _normalized_number(
            position.get("volume", position.get("Volume", 0))
        )
        active_sell_volume = _normalized_number(
            strategy_row.get("active_sell_volume")
        )
        active_sell_count = _normalized_count(
            strategy_row.get("active_sell_count", 0)
        )
        plan_text = " ".join(
            str(plan.get(field) or "")
            for field in ("audit_status", "bucket", "gate", "recommendation", "stance", "next_gate")
        ).upper()
        event_sensitive = any(token in plan_text for token in event_tokens)
        explicit_event_decision = event_sensitive and any(
            token in plan_text for token in decision_tokens
        )
        material_move = (
            day_percent is not None and abs(day_percent) >= threshold
        )
        profitable_without_sell = (
            holding > 0
            and active_sell_volume <= 0
            and profit_percent is not None
            and profit_percent > 0
        )
        protection_review_required = holding > 0 and active_sell_volume <= 0 and (
            material_move or event_sensitive
        )
        if not protection_review_required:
            continue
        rows.append(
            {
                "account_id": str(strategy_row.get("account_id") or ""),
                "orderbook_id": orderbook_id,
                "stock": str(position.get("stock") or position.get("Stock") or ""),
                "holding": holding,
                "day_percent": day_percent,
                "profit_percent": profit_percent,
                "active_sell_volume": active_sell_volume,
                "active_sell_count": active_sell_count,
                "event_sensitive": event_sensitive,
                "material_move": material_move,
                "profitable_without_sell": profitable_without_sell,
                "explicit_event_decision": explicit_event_decision,
                "decision_required": not explicit_event_decision or material_move,
                "audit_status": plan.get("audit_status"),
                "bucket": plan.get("bucket"),
                "next_gate": plan.get("next_gate"),
            }
        )

    rows.sort(
        key=lambda row: (
            not row["material_move"],
            not row["profitable_without_sell"],
            -(abs(row["day_percent"]) if row["day_percent"] is not None else 0.0),
            row["stock"],
        )
    )
    return {
        "authority": "READ_ONLY_TRIAGE",
        "broker_mutation": False,
        "trade_authority": False,
        "material_move_threshold_percent": threshold,
        "rows": rows,
        "material_move_count": sum(row["material_move"] for row in rows),
        "profitable_without_sell_count": sum(
            row["profitable_without_sell"] for row in rows
        ),
        "decision_required_count": sum(row["decision_required"] for row in rows),
        "notes": [
            "A material move is a review trigger, not a universal stop or sell rule.",
            "Event gaps, core retention, tactical slices, and risk-off decisions remain instrument-specific.",
        ],
    }


def build_position_strategy_live_states(
    account_id: str,
    positions: Iterable[dict[str, Any]],
    stoplosses: Iterable[dict[str, Any]],
    open_orders: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build one exact live state per account/orderbook.

    The tracked set is the union of held instruments, active stop rows, and
    non-terminal regular orders. This also catches an order left behind after
    a position reaches zero.
    """

    account_token = str(account_id or "").strip()
    if not account_token:
        raise ValueError("account_id is required.")

    states: dict[str, dict[str, Any]] = {}

    def ensure(orderbook_id: str, stock: str = "") -> dict[str, Any]:
        token = str(orderbook_id or "").strip()
        if not token:
            raise ValueError("Cannot audit a position/order without orderbook_id.")
        state = states.setdefault(
            token,
            {
                "account_id": account_token,
                "orderbook_id": token,
                "stock": str(stock or "").strip(),
                "holding": 0.0,
                "active_buy_volume": 0.0,
                "active_sell_volume": 0.0,
                "active_buy_count": 0,
                "active_sell_count": 0,
                "open_buy_volume": 0.0,
                "open_sell_volume": 0.0,
                "open_buy_count": 0,
                "open_sell_count": 0,
            },
        )
        if not state["stock"] and stock:
            state["stock"] = str(stock).strip()
        return state

    for position in positions:
        if _row_account_id(position) not in {"", account_token}:
            continue
        state = ensure(_row_orderbook_id(position), _row_stock(position))
        state["holding"] = _normalized_number(
            state["holding"] + _normalized_number(position.get("volume"))
        )

    for stoploss in stoplosses:
        if _row_account_id(stoploss) not in {"", account_token}:
            continue
        if not _is_active_stop(stoploss):
            continue
        side = _row_side(stoploss)
        if side not in {"BUY", "SELL"}:
            continue
        state = ensure(_row_orderbook_id(stoploss), _row_stock(stoploss))
        volume_key = "active_buy_volume" if side == "BUY" else "active_sell_volume"
        count_key = "active_buy_count" if side == "BUY" else "active_sell_count"
        state[volume_key] = _normalized_number(state[volume_key] + _row_volume(stoploss))
        state[count_key] += 1

    for order in open_orders:
        if _row_account_id(order) not in {"", account_token}:
            continue
        if not _is_active_open_order(order):
            continue
        side = _row_side(order)
        if side not in {"BUY", "SELL"}:
            continue
        state = ensure(_row_orderbook_id(order), _row_stock(order))
        volume_key = "open_buy_volume" if side == "BUY" else "open_sell_volume"
        count_key = "open_buy_count" if side == "BUY" else "open_sell_count"
        state[volume_key] = _normalized_number(state[volume_key] + _row_volume(order))
        state[count_key] += 1

    return [states[key] for key in sorted(states)]


def _empty_registry() -> dict[str, Any]:
    now = _utc_timestamp()
    return {
        "version": REGISTRY_VERSION,
        "created_at": now,
        "updated_at": now,
        "accounts": {},
    }


class PositionStrategyRegistry:
    """Atomic file-backed registry for reviewed per-position strategy plans."""

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
            if not isinstance(payload.get("accounts"), dict):
                raise ValueError("registry accounts must be an object")
            return payload
        except Exception as exc:
            self.load_error = str(exc)
            return _empty_registry()

    def _ensure_writable(self) -> None:
        if self.load_error:
            raise RuntimeError(
                "Position strategy registry could not be loaded; refusing to "
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

    def _account_positions_locked(
        self,
        account_id: str,
        *,
        create: bool = False,
    ) -> dict[str, dict[str, Any]]:
        token = str(account_id or "").strip()
        if not token:
            raise ValueError("account_id is required.")
        accounts = self._data.setdefault("accounts", {})
        account = accounts.get(token)
        if not isinstance(account, dict):
            if not create:
                return {}
            account = {"positions": {}}
            accounts[token] = account
        positions = account.get("positions")
        if not isinstance(positions, dict):
            if not create:
                return {}
            positions = {}
            account["positions"] = positions
        return positions

    def health(self) -> dict[str, Any]:
        with self._lock:
            accounts = self._data.get("accounts")
            accounts = accounts if isinstance(accounts, dict) else {}
            entry_count = sum(
                len(account.get("positions", {}))
                for account in accounts.values()
                if isinstance(account, dict)
                and isinstance(account.get("positions"), dict)
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

    def lookup(self, account_id: str, orderbook_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._account_positions_locked(account_id).get(
                str(orderbook_id or "").strip()
            )
            return deepcopy(entry) if isinstance(entry, dict) else None

    def _entry_from_candidate(
        self,
        candidate: dict[str, Any],
        *,
        tenant_session_id: str | None,
        source: str,
        existing: dict[str, Any] | None,
    ) -> dict[str, Any]:
        live_state = candidate.get("live_state")
        if not isinstance(live_state, dict):
            raise ValueError("Every registry candidate requires a live_state.")
        fingerprint = position_strategy_live_fingerprint(live_state)
        missing_identity = [
            field
            for field in ("account_id", "orderbook_id")
            if not fingerprint.get(field)
        ]
        if missing_identity:
            raise ValueError(
                "Cannot register position strategy without "
                + ", ".join(missing_identity)
                + "."
            )

        plan: dict[str, Any] = {}
        for field in _REQUIRED_PLAN_TEXT_FIELDS:
            value = str(candidate.get(field) or "").strip()
            if not value:
                raise ValueError(f"{field} is required.")
            plan[field] = value
        for field in _OPTIONAL_PLAN_TEXT_FIELDS:
            value = str(candidate.get(field) or "").strip()
            plan[field] = value or None
        priority = plan["priority"].upper()
        if priority not in {"A", "B", "C", "D", "E"}:
            raise ValueError("priority must be one of A, B, C, D, or E.")
        plan["priority"] = priority

        now = _utc_timestamp()
        return {
            **fingerprint,
            **plan,
            "broker_instrument": _row_stock(live_state),
            "proposed_correction": (
                str(candidate.get("proposed_correction")).strip()
                if candidate.get("proposed_correction") is not None
                else None
            ),
            "audit_exception": _normalize_audit_exception(
                candidate.get("audit_exception")
            ),
            "source_snapshot_at": (
                str(candidate.get("source_snapshot_at") or "").strip() or None
            ),
            "tenant_session_id": str(tenant_session_id or "").strip() or None,
            "source": str(source or "UNKNOWN").strip().upper(),
            "recorded_at": (
                existing.get("recorded_at")
                if isinstance(existing, dict) and existing.get("recorded_at")
                else now
            ),
            "updated_at": now,
        }

    def register_many_existing(
        self,
        candidates: Iterable[dict[str, Any]],
        *,
        tenant_session_id: str | None,
        source: str,
    ) -> list[dict[str, Any]]:
        """Validate every reviewed plan, then persist in one atomic write."""

        rows = list(candidates)
        with self._lock:
            self._ensure_writable()
            prepared: list[tuple[str, str, dict[str, Any]]] = []
            seen: set[tuple[str, str]] = set()
            for candidate in rows:
                live_state = candidate.get("live_state")
                if not isinstance(live_state, dict):
                    raise ValueError("Every registry candidate requires a live_state.")
                fingerprint = position_strategy_live_fingerprint(live_state)
                key = (fingerprint["account_id"], fingerprint["orderbook_id"])
                if key in seen:
                    raise ValueError(
                        "Duplicate registry candidate for account "
                        f"{key[0]} orderbook {key[1]}."
                    )
                seen.add(key)
                current = self._account_positions_locked(key[0]).get(key[1])
                entry = self._entry_from_candidate(
                    candidate,
                    tenant_session_id=tenant_session_id,
                    source=source,
                    existing=current,
                )
                prepared.append((key[0], key[1], entry))

            for account_id, orderbook_id, entry in prepared:
                self._account_positions_locked(account_id, create=True)[
                    orderbook_id
                ] = entry
            if prepared:
                self._save_locked()
            return [deepcopy(entry) for _, _, entry in prepared]

    def enrich(self, live_state: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(live_state)
        fingerprint = position_strategy_live_fingerprint(live_state)
        if self.load_error:
            enriched.update(
                {
                    "position_strategy_status": POSITION_STRATEGY_REGISTRY_UNAVAILABLE,
                    "position_strategy_mismatches": [],
                    "position_strategy": None,
                }
            )
            return enriched

        entry = self.lookup(
            fingerprint["account_id"],
            fingerprint["orderbook_id"],
        )
        if entry is None:
            enriched.update(
                {
                    "position_strategy_status": POSITION_STRATEGY_MISSING,
                    "position_strategy_mismatches": [],
                    "position_strategy": None,
                }
            )
            return enriched

        mismatches = [
            field
            for field in _LIVE_STATE_FIELDS
            if entry.get(field) != fingerprint.get(field)
        ]
        enriched.update(
            {
                "position_strategy_status": (
                    POSITION_STRATEGY_RECORDED
                    if not mismatches
                    else POSITION_STRATEGY_STALE_MISMATCH
                ),
                "position_strategy_mismatches": mismatches,
                "position_strategy": {
                    field: entry.get(field)
                    for field in (
                        *_PLAN_TEXT_FIELDS,
                        "proposed_correction",
                        "audit_exception",
                        "source",
                        "source_snapshot_at",
                        "recorded_at",
                        "updated_at",
                    )
                },
                "recorded_live_state": {
                    field: entry.get(field) for field in _LIVE_STATE_FIELDS
                },
            }
        )
        exception = entry.get("audit_exception")
        mismatch_fields = set(mismatches)
        allowed_fields = set(exception.get("allowed_mismatches", [])) if isinstance(exception, dict) else set()
        enriched["position_strategy_exception_status"] = (
            "ACKNOWLEDGED_INTENTIONAL_DRIFT"
            if exception and mismatch_fields and mismatch_fields.issubset(allowed_fields)
            else None
        )
        return enriched

    def reconcile_account(
        self,
        account_id: str,
        positions: Iterable[dict[str, Any]],
        stoplosses: Iterable[dict[str, Any]],
        open_orders: Iterable[dict[str, Any]],
        *,
        prune_stale: bool = False,
    ) -> dict[str, Any]:
        account_token = str(account_id or "").strip()
        states = build_position_strategy_live_states(
            account_token,
            positions,
            stoplosses,
            open_orders,
        )
        enriched = [self.enrich(state) for state in states]
        missing = [
            row
            for row in enriched
            if row.get("position_strategy_status") == POSITION_STRATEGY_MISSING
        ]
        mismatches = [
            row
            for row in enriched
            if row.get("position_strategy_status")
            == POSITION_STRATEGY_STALE_MISMATCH
        ]
        unavailable = [
            row
            for row in enriched
            if row.get("position_strategy_status")
            == POSITION_STRATEGY_REGISTRY_UNAVAILABLE
        ]
        recorded = [
            row
            for row in enriched
            if row.get("position_strategy_status") == POSITION_STRATEGY_RECORDED
        ]
        live_ids = {row["orderbook_id"] for row in states}
        with self._lock:
            planned_ids = set(self._account_positions_locked(account_token))
        stale_ids = sorted(planned_ids - live_ids)
        pruned_ids: list[str] = []
        if prune_stale and stale_ids:
            with self._lock:
                self._ensure_writable()
                account_positions = self._account_positions_locked(account_token)
                for orderbook_id in stale_ids:
                    del account_positions[orderbook_id]
                self._save_locked()
                pruned_ids = stale_ids
                stale_ids = []

        holding_drift = [
            row
            for row in mismatches
            if "holding" in row.get("position_strategy_mismatches", [])
        ]
        stop_drift = [
            row
            for row in mismatches
            if _STOP_EXPOSURE_FIELDS.intersection(
                row.get("position_strategy_mismatches", [])
            )
        ]
        open_order_drift = [
            row
            for row in mismatches
            if _OPEN_ORDER_EXPOSURE_FIELDS.intersection(
                row.get("position_strategy_mismatches", [])
            )
        ]
        acknowledged_mismatches = [
            row
            for row in mismatches
            if row.get("position_strategy_exception_status")
            == "ACKNOWLEDGED_INTENTIONAL_DRIFT"
        ]
        unresolved_mismatches = [
            row for row in mismatches if row not in acknowledged_mismatches
        ]
        complete = not missing and not mismatches and not unavailable and not stale_ids
        return {
            "account_id": account_token,
            "complete": complete,
            "review_required": not complete,
            "row_count": len(enriched),
            "planned_count": len(planned_ids) - len(pruned_ids),
            "recorded_count": len(recorded),
            "missing_count": len(missing),
            "mismatch_count": len(mismatches),
            "registry_unavailable_count": len(unavailable),
            "stale_plan_count": len(stale_ids),
            "holding_drift_count": len(holding_drift),
            "stop_exposure_drift_count": len(stop_drift),
            "open_order_drift_count": len(open_order_drift),
            "acknowledged_mismatch_count": len(acknowledged_mismatches),
            "unresolved_mismatch_count": len(unresolved_mismatches),
            "acknowledged_mismatch_orderbook_ids": [
                row["orderbook_id"] for row in acknowledged_mismatches
            ],
            "unresolved_mismatch_orderbook_ids": [
                row["orderbook_id"] for row in unresolved_mismatches
            ],
            "missing_orderbook_ids": [row["orderbook_id"] for row in missing],
            "mismatched_orderbook_ids": [
                row["orderbook_id"] for row in mismatches
            ],
            "stale_plan_orderbook_ids": stale_ids,
            "pruned_count": len(pruned_ids),
            "pruned_orderbook_ids": pruned_ids,
            "positions": enriched,
            "registry": self.health(),
        }
