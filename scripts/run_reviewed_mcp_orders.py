#!/usr/bin/env python3
"""Run an operator-reviewed manifest through the existing Avanza MCP bridge.

Usage (preview is the default):
  .venv/bin/python scripts/run_reviewed_mcp_orders.py PRIVATE_PLAN.json --dry-run
  .venv/bin/python scripts/run_reviewed_mcp_orders.py PRIVATE_PLAN.json --execute

No server is launched, no login is performed and live authorization is never
enabled here. Execution requires the operator's existing scoped authorization.
The manifest is a reviewed instruction, not a strategy engine: no prices, sizes,
orders, cancellations or recovery allocations are inferred. Keep it private.
An uncertain submission is journaled before any further action and never retried.
Stop edits use the canonical MCP replacement semantics and reset trailing state.

Manifest v1: plan_id, created_at, expires_at, contract_revision, actions, reviews.
Each action: id, label, tool, arguments, expected_state, execution_window,
quote_guard, evidence, display. See validate_plan and state_summary for fields.
Display is percentage-only; exact native order parameters stay in the manifest.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import ipaddress
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table
from rich.text import Text

TOOLS = {
    "avanza_order_set", "avanza_order_edit", "avanza_order_delete",
    "avanza_stoploss_set", "avanza_stoploss_edit", "avanza_stoploss_delete",
    "avanza_stoploss_set_batch",
}
REGISTRY_TOOL = "avanza_position_strategy_register_batch"
REGISTRY_LIVE_FIELDS = {
    "holding", "active_buy_volume", "active_sell_volume", "active_buy_count",
    "active_sell_count", "open_buy_volume", "open_sell_volume", "open_buy_count", "open_sell_count",
}
GATES = {
    "thesis_event", "technical_structure", "sold_lot_attribution",
    "risk_at_invalidation", "combined_factors", "conditional_capacity",
    "full_friction_churn", "protection_intent", "named_asset_permission",
}
STATE_KEYS = {
    "holding", "buy_volume", "sell_volume", "open_buy_volume",
    "open_sell_volume", "stop_ids", "order_ids", "stop_fingerprints",
    "order_fingerprints",
}
STOP_FIELDS = (
    "stop_loss_id", "account_id", "orderbook_id", "side", "volume",
    "trigger_type", "trigger_value", "trigger_value_type", "order_price",
    "order_price_type", "valid_until", "order_valid_days", "strategy_intent",
    "strategy_reason", "trigger_on_market_maker_quote", "short_selling_allowed",
)
ORDER_FIELDS = ("order_id", "account_id", "orderbook_id", "side", "volume", "price", "valid_until")


class Blocked(RuntimeError):
    """An explicit precondition or verification failed."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise Blocked("Expected an ISO timestamp with timezone.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Blocked("Invalid ISO timestamp.") from exc
    if result.tzinfo is None:
        raise Blocked("Naive timestamps are not accepted.")
    return result


def number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Blocked("Expected a finite number, not a boolean or formatted string.")
    return float(value)


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def text_value(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Blocked(f"Missing {field}.")
    return value


def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Blocked(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(), object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        raise Blocked("Manifest must be a JSON object.")
    return value


def validate_registry_item(item: Any, orderbook: str) -> None:
    from avanza_mcp.mcp.server import mcp_tools_catalog
    schema = next(t for t in mcp_tools_catalog() if t["name"] == REGISTRY_TOOL)["inputSchema"]["properties"]["items"]["items"]
    if not isinstance(item, dict) or set(schema["required"]) - set(item) or set(item) - set(schema["properties"]):
        raise Blocked("Registry update requires a complete canonical position plan.")
    if str(item.get("order_book_id")) != orderbook:
        raise Blocked("Registry plan changed the reviewed instrument.")
    if item.get("audit_exception") is not None or item.get("preserve_audit_exception_fingerprint"):
        raise Blocked("This runner cannot create, change or rebaseline audit exceptions.")
    for key, value in item.items():
        spec = schema["properties"][key]
        if key in REGISTRY_LIVE_FIELDS:
            n = number(value)
            if n < 0 or (key.endswith("count") and not n.is_integer()):
                raise Blocked("Registry exposure/count is invalid.")
        elif spec.get("type") == "string":
            text_value(value, key)
        if "enum" in spec and value not in spec["enum"]:
            raise Blocked(f"Unsupported registry {key}.")


def validate_plan(plan: dict[str, Any], now: datetime) -> None:
    if plan.get("version") != 1 or isinstance(plan.get("version"), bool):
        raise Blocked("Only manifest version 1 is supported.")
    for field in ("plan_id", "contract_revision"):
        text_value(plan.get(field), field)
    created, expires = timestamp(plan.get("created_at")), timestamp(plan.get("expires_at"))
    if created > now or expires <= created or (expires - created).total_seconds() > 86400:
        raise Blocked("Manifest timestamps are invalid or validity exceeds 24 hours.")
    if not isinstance(plan.get("actions"), list) or not isinstance(plan.get("reviews"), list):
        raise Blocked("Manifest requires actions and reviews lists.")
    validate_review_coverage(plan)
    guards = plan.get("portfolio_guards", [])
    if not isinstance(guards, list):
        raise Blocked("Portfolio guards must be a list.")
    guard_scopes = set()
    for guard in guards:
        scope = tuple(text_value(guard.get(k), k) for k in ("tenant_session_id", "account_id"))
        if scope in guard_scopes:
            raise Blocked("Duplicate portfolio guard scope.")
        guard_scopes.add(scope)
        if portfolio_fingerprint(guard.get("snapshot", {}), scope[1]) != guard.get("expected_state"):
            raise Blocked("Portfolio guard does not match its reviewed snapshot.")
        ceilings = guard.get("position_value_ceilings_sek", {})
        if set(ceilings) != set(guard["expected_state"]["holdings"]):
            raise Blocked("Portfolio value ceilings omit a reviewed holding.")
        if any(number(v) <= 0 for v in ceilings.values()):
            raise Blocked("Portfolio value ceilings must be positive.")
    action_scopes = {(a.get("arguments", {}).get("tenant_session_id"), a.get("arguments", {}).get("account_id"))
                     for a in plan["actions"] if isinstance(a, dict)}
    if guards and action_scopes - guard_scopes:
        raise Blocked("Every action account requires a portfolio guard.")
    if guards and plan.get("decision_coverage"):
        universe = set()
        for guard in guards:
            state = guard["expected_state"]
            books = set(state["holdings"]) | {r["orderbook_id"] for k in ("stops", "orders") for r in state[k].values()}
            universe.update(f"{guard['tenant_session_id']}/{guard['account_id']}/{book}" for book in books)
        if sorted(universe) != plan["decision_coverage"]["scope_ids"]:
            raise Blocked("Decisions do not cover the full reviewed live portfolio universe.")
    ids: set[str] = set()
    requests: set[str] = set()
    from avanza_mcp.mcp.server import mcp_tools_catalog
    schemas = {tool["name"]: tool["inputSchema"] for tool in mcp_tools_catalog()}
    updates = plan.get("registry_updates", [])
    if not isinstance(updates, list):
        raise Blocked("registry_updates must be a list.")
    update_scopes: set[tuple[str, str, str]] = set()
    for update in updates:
        if not isinstance(update, dict):
            raise Blocked("Registry updates must be objects.")
        for key in ("id", "tenant_session_id", "account_id", "orderbook_id", "reason"):
            text_value(update.get(key), key)
        scope = tuple(update[k] for k in ("tenant_session_id", "account_id", "orderbook_id"))
        if scope in update_scopes or update["id"] in ids:
            raise Blocked("Duplicate registry scope or id.")
        update_scopes.add(scope)
        ids.add(update["id"])
        if not isinstance(update.get("expected_state"), dict) or set(update["expected_state"]) != STATE_KEYS:
            raise Blocked("Registry update requires the exact reviewed broker fingerprint.")
        validate_registry_item(update.get("item"), update["orderbook_id"])
    for action in plan["actions"]:
        if not isinstance(action, dict):
            raise Blocked("Each action must be an object.")
        action_id = text_value(action.get("id"), "action id")
        if action_id in ids:
            raise Blocked("Duplicate action id.")
        ids.add(action_id)
        if "post_position_plan" in action:
            validate_registry_item(action["post_position_plan"], action.get("orderbook_id", ""))
        text_value(action.get("label"), "action label")
        tool, args = action.get("tool"), action.get("arguments")
        if tool not in TOOLS or not isinstance(args, dict):
            raise Blocked("Unsupported action tool or arguments.")
        if tool == "avanza_stoploss_set_batch":
            if set(args) != {"tenant_session_id", "account_id", "items"} or not isinstance(args["items"], list) or not 1 <= len(args["items"]) <= 3:
                raise Blocked("A ladder batch requires an exact scope and one to three complete stages.")
            components = []
            for index, item in enumerate(args["items"]):
                if not isinstance(item, dict) or any(k in item for k in ("tenant_session_id", "session_id", "account_id", "confirm")):
                    raise Blocked("A batch item cannot override scope or confirmation.")
                components.append({**{k: v for k, v in action.items() if k != "recovery_allocations"}, "id": f"{action_id}:{index}", "tool": "avanza_stoploss_set",
                                   "arguments": {**item, "tenant_session_id": args["tenant_session_id"], "account_id": args["account_id"]}})
            child_plan = {k: v for k, v in plan.items() if k != "decision_coverage"}
            validate_plan({**child_plan, "registry_updates": [], "actions": components}, now)
            request_hash = digest({"tool": tool, "arguments": args})
            if request_hash in requests:
                raise Blocked("Duplicate batch request.")
            requests.add(request_hash)
            continue
        if "confirm" in args or "session_id" in args:
            raise Blocked("The runner owns confirm; legacy session aliases are forbidden.")
        for field in ("tenant_session_id", "account_id"):
            text_value(args.get(field), field)
        schema = schemas[tool]
        if set(args) - set(schema["properties"]):
            raise Blocked(f"Unknown parameter for {tool}.")
        if set(schema.get("required", [])) - set(args):
            raise Blocked(f"Incomplete native tuple for {tool}.")
        for key, value in args.items():
            spec = schema["properties"][key]
            kind = spec.get("type")
            if kind in {"number", "integer"}:
                n = number(value)
                if kind == "integer" and not n.is_integer():
                    raise Blocked(f"{key} must be an integer.")
            elif kind == "string":
                text_value(value, key)
            elif kind == "boolean" and not isinstance(value, bool):
                raise Blocked(f"{key} must be a boolean.")
            if "enum" in spec and value not in spec["enum"]:
                raise Blocked(f"Unsupported {key}.")
        if "volume" in args and (number(args["volume"]) <= 0 or not float(args["volume"]).is_integer()):
            raise Blocked("Only positive whole-share order quantities are supported.")
        for field in ("price", "order_price", "trigger_value"):
            if field in args and number(args[field]) <= 0:
                raise Blocked(f"{field} must be positive.")
        if "stoploss" in tool:
            for field in ("strategy_intent", "strategy_reason"):
                text_value(args.get(field), field)
            if not tool.endswith("delete"):
                required = {"trigger_type", "trigger_value_type", "order_price_type", "order_type",
                            "valid_until", "order_valid_days", "trigger_on_market_maker_quote", "short_selling_allowed"}
                if required - set(args) or args["short_selling_allowed"] is not False:
                    raise Blocked("Complete non-short stop settings are required.")
        if tool == "avanza_order_set" and (args.get("order_type") not in {"buy", "sell"} or args.get("condition") != "normal"):
            raise Blocked("Regular placement requires an explicit side and normal limit condition.")
        if "order_type" in args and args["order_type"] not in {"buy", "sell"}:
            raise Blocked("Unsupported side.")
        expected = action.get("expected_state")
        if not isinstance(expected, dict) or set(expected) != STATE_KEYS:
            raise Blocked("Missing exact reviewed holding/order/stop fingerprint.")
        text_value(action.get("orderbook_id"), "reviewed orderbook_id")
        if "order_book_id" in args and args["order_book_id"] != action["orderbook_id"]:
            raise Blocked("Orderbook scope differs from reviewed identity.")
        evidence = action.get("evidence", {})
        if set(evidence.get("gates", {})) != GATES or not all(v is True for v in evidence["gates"].values()):
            raise Blocked("An executable action requires every reviewed risk/evidence gate.")
        text_value(evidence.get("reason"), "current evidence reason")
        text_value(evidence.get("source"), "current evidence source")
        if not created <= timestamp(evidence.get("reviewed_at")) <= min(expires, now):
            raise Blocked("Evidence is future-dated or outside manifest validity.")
        window = action.get("execution_window", {})
        start, end = timestamp(window.get("start")), timestamp(window.get("end"))
        if end <= start or (end - start).total_seconds() > 8 * 3600 or window.get("state") != "REGULAR_SESSION":
            raise Blocked("An explicit regular-session execution window is required.")
        guard = action.get("quote_guard", {})
        text_value(guard.get("currency"), "quote currency")
        text_value(guard.get("market"), "quote market")
        if not 0 < number(guard.get("min")) <= number(guard.get("max")):
            raise Blocked("Invalid quote guard band.")
        if not 0 < number(guard.get("max_age_seconds")) <= 60 or not 0 < number(guard.get("max_spread_percent")) <= 2:
            raise Blocked("Invalid quote age/spread guard.")
        if number(guard.get("min_buying_power_sek")) < 0:
            raise Blocked("A reviewed conditional-capacity cash floor is required.")
        if "min_capital_sek" in guard and number(guard["min_capital_sek"]) <= 0:
            raise Blocked("The reviewed capital floor must be positive.")
        if "minimum_current_profit_percent" in action and number(action["minimum_current_profit_percent"]) <= 0:
            raise Blocked("A profit-harvest floor must be positive.")
        if guard["currency"] != "SEK" and "order_valid_days" in args and args["order_valid_days"] != 1:
            raise Blocked("Foreign stop children must retain one-day validity.")
        display = action.get("display", {})
        text_value(display.get("basis"), "percentage display basis")
        number(display.get("distance_percent"))
        request_hash = digest({"tool": tool, "arguments": args})
        if request_hash in requests:
            raise Blocked("Duplicate native request in manifest.")
        requests.add(request_hash)
    validate_recovery_allocations(plan["actions"])


def validate_review_coverage(plan: dict[str, Any]) -> None:
    coverage = plan.get("decision_coverage")
    if coverage is None:
        return
    if not isinstance(coverage, dict) or type(coverage.get("recorded_rows")) is not int or coverage["recorded_rows"] != len(plan["reviews"]):
        raise Blocked("Decision coverage count differs from the actual rows.")
    scopes, linked = set(), set()
    actions = {a.get("id"): a for a in plan["actions"] if isinstance(a, dict)}
    for row in plan["reviews"]:
        if not isinstance(row, dict):
            raise Blocked("Decision rows must be objects.")
        scope = tuple(text_value(row.get(k), k) for k in ("tenant_session_id", "account_id", "orderbook_id"))
        if scope in scopes:
            raise Blocked("Duplicate account-position decision.")
        scopes.add(scope)
        disposition = row.get("disposition")
        if disposition not in {"ACTION_PREPARED", "NO_ACTION_DECISION", "EVIDENCE_BLOCKED"}:
            raise Blocked("Every reviewed row needs an explicit decision disposition.")
        for key in ("reason", "next_gate", "status"):
            text_value(row.get(key), key)
        ids = row.get("action_ids", [])
        if not isinstance(ids, list) or bool(ids) != (disposition == "ACTION_PREPARED"):
            raise Blocked("Decision disposition disagrees with its prepared actions.")
        for action_id in ids:
            action = actions.get(action_id)
            if action is None or action_id in linked:
                raise Blocked("Missing or multiply linked reviewed action.")
            actual = (action["arguments"]["tenant_session_id"], action["arguments"]["account_id"], action["orderbook_id"])
            if actual != scope:
                raise Blocked("Action is linked to a different account-position.")
            linked.add(action_id)
    if linked != set(actions) or sorted("/".join(s) for s in scopes) != coverage.get("scope_ids"):
        raise Blocked("Decision coverage omits an action or a reviewed scope.")


def portfolio_fingerprint(snapshot: dict[str, Any], account: str) -> dict[str, Any]:
    if str(snapshot.get("account_id")) != account:
        raise Blocked("Portfolio snapshot account mismatch.")
    containers = [snapshot.get(k, {}) for k in ("portfolio", "stoplosses", "open_orders")]
    if any(str(c.get("account_id")) != account for c in containers):
        raise Blocked("Portfolio snapshot contains a different account.")
    positions, stops, orders = [c.get(k) for c, k in zip(containers, ("positions", "stoplosses", "orders"))]
    if any(not isinstance(rows, list) for rows in (positions, stops, orders)):
        raise Blocked("Incomplete portfolio inventory.")
    if containers[2].get("fund_orders") != [] or containers[2].get("fund_order_count") != 0:
        raise Blocked("Unreviewed fund commitments are present or unresolved.")
    holdings = {}
    for row in positions:
        if str(row.get("account_id")) != account:
            raise Blocked("Holding account mismatch.")
        book = text_value(row.get("orderbook_id"), "holding orderbook")
        if book in holdings or number(row.get("volume")) <= 0:
            raise Blocked("Duplicate or invalid portfolio holding.")
        holdings[book] = number(row["volume"])
    inventories = []
    for rows, fields, id_key in ((stops, STOP_FIELDS, "stop_loss_id"), (orders, ORDER_FIELDS, "order_id")):
        inventory = {}
        for row in rows:
            if str(row.get("account_id")) != account or row.get("side") not in {"BUY", "SELL"}:
                raise Blocked("Portfolio commitment identity or side is invalid.")
            if id_key == "stop_loss_id" and (row.get("status") != "ACTIVE" or row.get("strategy_metadata_status") != "RECORDED"):
                raise Blocked("A portfolio stop is failed, inactive or lacks strategy metadata.")
            row_id = text_value(row.get(id_key), id_key)
            if row_id in inventory or number(row.get("volume")) <= 0:
                raise Blocked("Duplicate or invalid portfolio commitment.")
            inventory[row_id] = {field: row.get(field) for field in fields}
        inventories.append(inventory)
    return {"holdings": holdings, "stops": inventories[0], "orders": inventories[1]}


def check_portfolio_guards(call: Callable, guards: list[dict[str, Any]]) -> None:
    for guard in guards:
        snapshot = check_payload(call("avanza_live_snapshot", {
            "tenant_session_id": guard["tenant_session_id"], "account_id": guard["account_id"],
            "refresh": True,
        }))["result"]
        if portfolio_fingerprint(snapshot, guard["account_id"]) != guard["expected_state"]:
            raise Blocked("Portfolio-wide holdings or commitments changed; re-review factors and capacity.")
        for row in snapshot["portfolio"]["positions"]:
            value = str(row.get("Value", ""))
            try:
                amount = float(value.removesuffix(" SEK")) if value.endswith(" SEK") else math.nan
            except ValueError:
                amount = math.nan
            if not math.isfinite(amount) or amount < 0 or amount > guard["position_value_ceilings_sek"][row["orderbook_id"]]:
                raise Blocked("A holding exceeds the reviewed factor stress or its SEK value is unresolved.")


def advance_portfolio_guard(guards: list[dict[str, Any]], action: dict[str, Any], after: dict[str, Any]) -> None:
    # Only an independently verified scoped delta may advance the in-memory
    # guard. Other positions and commitments retain the original fingerprint.
    args, book = action["arguments"], action["orderbook_id"]
    for guard in guards:
        if (guard["tenant_session_id"], guard["account_id"]) != (args["tenant_session_id"], args["account_id"]):
            continue
        expected = guard["expected_state"]
        for key, rows, fields, id_key in (
            ("stops", after["active_buy_stops"] + after["active_sell_stops"], STOP_FIELDS, "stop_loss_id"),
            ("orders", after["open_orders"], ORDER_FIELDS, "order_id"),
        ):
            expected[key] = {k: v for k, v in expected[key].items() if v["orderbook_id"] != book}
            expected[key].update({r[id_key]: {field: r.get(field) for field in fields} for r in rows})


def validate_recovery_allocations(actions: list[dict[str, Any]]) -> None:
    lots: dict[tuple[str, str, str], dict[str, Any]] = {}
    source_ids: dict[str, tuple[str, int]] = {}
    allocation_ids: set[tuple[str, str]] = set()
    raw_lots: dict[tuple[str, str, str], str] = {}
    for action in actions:
        allocations = action.get("recovery_allocations")
        if allocations is None:
            continue
        if not isinstance(allocations, list) or not allocations:
            raise Blocked("Recovery allocations must be a nonempty exact list.")
        args = action["arguments"]
        stages = args.get("items", [args])
        stage_totals = [0.0] * len(stages)
        for row in allocations:
            if not isinstance(row, dict):
                raise Blocked("Recovery allocation must be an object.")
            source = text_value(row.get("planned_source_id"), "planned recovery source id")
            index = row.get("stage_index")
            if type(index) is not int or not 0 <= index < len(stages) or stages[index].get("order_type") != "buy":
                raise Blocked("Recovery allocation has no exact BUY stage.")
            stage_id = (action["id"], index)
            if source_ids.setdefault(source, stage_id) != stage_id:
                raise Blocked("One planned recovery source is assigned to different orders.")
            quantity = number(row.get("allocation_antal"))
            remaining, sold = number(row.get("remaining_before_antal")), number(row.get("sold_antal"))
            if not 0 < quantity <= remaining <= sold or any(not n.is_integer() for n in (quantity, remaining, sold)):
                raise Blocked("Recovery source quantity is invalid or overallocated.")
            key = (args["tenant_session_id"], args["account_id"], text_value(row.get("sale_lot_id"), "sale lot id"))
            identity = (action["orderbook_id"], text_value(row.get("raw_transaction_id"), "raw sale id"), remaining, sold)
            allocation_id = (source, key[2])
            if allocation_id in allocation_ids:
                raise Blocked("Duplicate source-to-lot recovery allocation.")
            allocation_ids.add(allocation_id)
            raw_key = (key[0], key[1], identity[1])
            if raw_lots.setdefault(raw_key, key[2]) != key[2]:
                raise Blocked("One raw SELL source is assigned to different immutable lots.")
            existing = lots.setdefault(key, {"identity": identity, "quantity": 0.0})
            if existing["identity"] != identity:
                raise Blocked("One sale lot has contradictory identity or quantity evidence.")
            existing["quantity"] += quantity
            if existing["quantity"] > remaining:
                raise Blocked("Sale lot is overallocated across this manifest.")
            stage_totals[index] += quantity
            if "sold_reference" in row and "entry_drop_percent" in row:
                reference = number(row["sold_reference"])
                price = number(stages[index].get("price", stages[index].get("order_price")))
                drop = number(row["entry_drop_percent"])
                if reference <= 0 or not 0 < drop < 100 or abs(drop - (1 - price / reference) * 100) > 1e-6:
                    raise Blocked("Recovery percentage is not the exact same-sale entry discount.")
        if any(total != number(stage["volume"]) for total, stage in zip(stage_totals, stages, strict=True)):
            raise Blocked("Recovery allocation quantities do not match complete BUY stages.")


def check_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise Blocked("MCP request failed; no automatic retry. Inspect the private journal.")
    result = payload.get("result")
    if isinstance(result, dict):
        if result.get("ok") is False or result.get("all_ok") is False or result.get("error") or result.get("errors"):
            raise Blocked("MCP reported a nested error; outcome requires reconciliation.")
        nested = result.get("result")
        if isinstance(nested, dict) and (nested.get("ok") is False or nested.get("error")):
            raise Blocked("Broker reported an error; outcome requires reconciliation.")
    return payload


def canonical_arguments(args: dict[str, Any]) -> dict[str, Any]:
    from avanza_mcp.rendering import parse_price_type
    result = dict(args)
    for key in ("trigger_value_type", "order_price_type"):
        if key in result:
            result[key] = parse_price_type(result[key]).upper()
    if "trigger_type" in result:
        result["trigger_type"] = result["trigger_type"].upper().replace("-", "_")
    return result


def verify_preview(action: dict[str, Any], preview: dict[str, Any]) -> None:
    """Compare native preview fields, not the server's human-readable summary."""
    if preview.get("dry_run") is not True or preview.get("warnings") or preview.get("warning"):
        raise Blocked("Native preview is unverified or has unresolved warnings.")
    tool, args = action["tool"], canonical_arguments(action["arguments"])
    if tool == "avanza_stoploss_set_batch":
        results = preview.get("results", [])
        if len(results) != len(args["items"]) or preview.get("account_id") != args["account_id"]:
            raise Blocked("Native batch preview is incomplete or incorrectly scoped.")
        for index, item in enumerate(args["items"]):
            child = {**action, "tool": "avanza_stoploss_set", "arguments": {**item, "account_id": args["account_id"]}}
            verify_preview(child, results[index])
        return
    request = preview.get("request")
    if not isinstance(request, dict):
        raise Blocked("Native preview omitted its exact request.")
    if tool == "avanza_stoploss_edit":
        if request.get("stop_loss_id") != args["stop_loss_id"]:
            raise Blocked("Native replacement preview changed the target stop.")
        request = request.get("replacement", {})
    if "stoploss" in tool and not tool.endswith("delete"):
        expected = {
            "account_id": args["account_id"], "order_book_id": args["order_book_id"],
            "parent_stop_loss_id": args.get("parent_stop_loss_id", "0"),
            "strategy_intent": args["strategy_intent"], "strategy_reason": args["strategy_reason"],
        }
        trigger = {
            "type": args["trigger_type"], "value": args["trigger_value"], "value_type": args["trigger_value_type"],
            "valid_until": args["valid_until"], "trigger_on_market_maker_quote": args["trigger_on_market_maker_quote"],
        }
        child = {
            "type": args["order_type"].upper(), "price": args["order_price"], "price_type": args["order_price_type"],
            "volume": args["volume"], "valid_days": args["order_valid_days"], "short_selling_allowed": False,
        }
        returned_child = {k: v for k, v in request.get("stop_loss_order_event", {}).items() if k != "derived_expiry_if_triggered_today"}
        if request.get("stop_loss_trigger") != trigger or returned_child != child:
            raise Blocked("Native preview changed the trigger or child-order tuple.")
    else:
        expected = {k: v for k, v in args.items() if k != "tenant_session_id"}
        if tool == "avanza_order_set":
            expected["order_type"] = args["order_type"].upper()
            expected["condition"] = args["condition"].upper()
    if any(request.get(k) != v for k, v in expected.items()):
        raise Blocked("Native preview differs from the reviewed request.")


class ExistingBridge:
    def __init__(self) -> None:
        from avanza_mcp.mcp.proxy import load_mcp_session
        self.session = load_mcp_session()
        url = urlsplit(str(self.session["url"]))
        try:
            local = url.hostname == "localhost" or ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            local = False
        if not local or url.scheme != "http" or url.username or url.password or url.query or url.fragment:
            raise Blocked("Only the existing local MCP bridge is supported.")

    def __call__(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        from avanza_mcp.mcp.proxy import call_mcp_bridge
        return call_mcp_bridge(self.session, tool, args)


def state_summary(state: dict[str, Any]) -> dict[str, Any]:
    if any(not isinstance(state.get(key), list) for key in (
        "active_buy_stops", "active_sell_stops", "open_orders", "failed_orders", "non_active_or_error_stops"
    )):
        raise Blocked("Incomplete instrument-state response.")
    if state["failed_orders"] or any(str(row.get("status", "")).upper() in {"ERROR", "FAILED", "REJECTED"} for row in state["non_active_or_error_stops"]):
        raise Blocked("Relevant failed orders/stops require reconciliation.")
    stops = state["active_buy_stops"] + state["active_sell_stops"]
    orders = state["open_orders"]
    for row in stops:
        if row.get("strategy_metadata_status") != "RECORDED":
            raise Blocked("A stop lacks exact recorded strategy metadata.")
    def total(rows: list[dict[str, Any]]) -> float:
        return sum(number(row.get("volume")) for row in rows)
    def fingerprints(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[str]:
        return sorted(digest({field: row.get(field) for field in fields}) for row in rows)
    holding = 0 if state.get("position") is None else number(state["position"].get("volume"))
    return {
        "holding": holding,
        "buy_volume": total(state["active_buy_stops"]),
        "sell_volume": total(state["active_sell_stops"]),
        "open_buy_volume": total([r for r in orders if str(r.get("side", "")).upper() == "BUY"]),
        "open_sell_volume": total([r for r in orders if str(r.get("side", "")).upper() == "SELL"]),
        "stop_ids": sorted(text_value(r.get("stop_loss_id"), "stop id") for r in stops),
        "order_ids": sorted(text_value(r.get("order_id"), "order id") for r in orders),
        "stop_fingerprints": fingerprints(stops, STOP_FIELDS),
        "order_fingerprints": fingerprints(orders, ORDER_FIELDS),
    }


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        self.fd = os.open(path, flags, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fchmod(self.fd, 0o600)
            with os.fdopen(os.dup(self.fd), "r") as stream:
                self.rows = [json.loads(line) for line in stream if line.strip()]
            self.attempted = {r["action_id"] for r in self.rows if r.get("event") == "SUBMITTING"}
            self.requests = {r["request_sha256"] for r in self.rows if r.get("event") == "SUBMITTING"}
        except BaseException:
            os.close(self.fd)
            raise

    def append(self, **row: Any) -> None:
        row["at"] = utcnow().isoformat()
        data = (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode()
        view = memoryview(data)
        while view:
            written = os.write(self.fd, view)
            view = view[written:]
        os.fsync(self.fd)

    def close(self) -> None:
        os.close(self.fd)


def scoped_state(call: Callable, action: dict[str, Any]) -> dict[str, Any]:
    args = action["arguments"]
    payload = check_payload(call("avanza_instrument_state", {
        "tenant_session_id": args["tenant_session_id"], "account_id": args["account_id"],
        "orderbook_id": action["orderbook_id"], "include_raw": True,
    }))
    state = payload["result"]
    if str(state.get("account_id")) != args["account_id"] or str(state.get("orderbook_id")) != action["orderbook_id"]:
        raise Blocked("Instrument-state account/orderbook scope mismatch.")
    return state


def check_sell_capacity(state: dict[str, Any], action: dict[str, Any]) -> None:
    """Pending BUYs never supply shares for an executable SELL."""
    args, tool = action["arguments"], action["tool"]
    summary = state_summary(state)
    projected = summary["sell_volume"] + summary["open_sell_volume"]
    target_key = "stop_loss_id" if "stoploss" in tool else "order_id"
    rows = state["active_sell_stops"] if "stoploss" in tool else state["open_orders"]
    target = next((r for r in rows if r.get(target_key) == args.get(target_key) and args.get(target_key)), None)
    if target and str(target.get("side", "")).upper() == "SELL":
        projected -= number(target["volume"])
    items = args["items"] if tool == "avanza_stoploss_set_batch" else [args]
    new_sells = [] if tool.endswith("delete") else [item for item in items if str(item.get("order_type") or (target or {}).get("side", "")).upper() == "SELL"]
    projected += sum(number(item["volume"]) for item in new_sells)
    if projected > summary["holding"]:
        raise Blocked("Aggregate SELL exposure exceeds currently held shares.")
    if new_sells:
        retained = number(action.get("minimum_retained_antal"))
        if retained < 0 or retained > summary["holding"] or not retained.is_integer():
            raise Blocked("An exact reviewed retained-core floor is required for a SELL.")
        if retained == 0 and action.get("allow_full_exit") is not True:
            raise Blocked("Full exit requires an explicit reviewed marker/core waiver.")
        if projected > summary["holding"] - retained:
            raise Blocked("Aggregate SELL exposure violates the reviewed retained-core floor.")


def audit_ready(audit: dict[str, Any], pending: set[str] | None = None) -> bool:
    if audit.get("stop_error_count") != 0 or audit.get("order_error_count") != 0:
        return False
    if audit.get("governance_complete") is True:
        return True
    p = audit.get("position_strategy", {})
    if not pending or any(p.get(key) != 0 for key in (
        "missing_count", "registry_unavailable_count", "stale_plan_count", "protection_missing_count",
        "protection_invalid_count", "protection_repair_required_count", "protection_registry_unavailable_count",
    )):
        return False
    # Only explicitly reviewed rows may defer an audit during the preview pass.
    # The full audit must pass after their metadata repair, before any order.
    mismatches = p.get("unresolved_mismatch_orderbook_ids")
    contradictions = p.get("protection_contradiction_orderbook_ids")
    if not isinstance(mismatches, list) or not isinstance(contradictions, list):
        return False
    affected = set(mismatches) | set(contradictions)
    return bool(affected) and affected <= pending


def registry_fingerprint(state: dict[str, Any]) -> dict[str, Any]:
    s = state_summary(state)
    return {
        "holding": s["holding"], "active_buy_volume": s["buy_volume"], "active_sell_volume": s["sell_volume"],
        "active_buy_count": len(state["active_buy_stops"]), "active_sell_count": len(state["active_sell_stops"]),
        "open_buy_volume": s["open_buy_volume"], "open_sell_volume": s["open_sell_volume"],
        "open_buy_count": sum(str(r.get("side", "")).upper() == "BUY" for r in state["open_orders"]),
        "open_sell_count": sum(str(r.get("side", "")).upper() == "SELL" for r in state["open_orders"]),
    }


def write_reviewed_registry(call: Callable, action: dict[str, Any], item: dict[str, Any], state: dict[str, Any],
                            *, execute: bool, journal: Journal | None = None) -> None:
    if any(item[key] != value for key, value in registry_fingerprint(state).items()):
        raise Blocked("Reviewed registry plan does not match the exact verified exposure.")
    args = {k: action["arguments"][k] for k in ("tenant_session_id", "account_id")}
    args.update(items=[item], prune_stale=False)
    preview = check_payload(call(REGISTRY_TOOL, {**args, "confirm": False}))["result"]
    if preview.get("dry_run") is not True or preview.get("broker_mutation") is not False or preview.get("account_id") != args["account_id"] or preview.get("count") != 1:
        raise Blocked("Registry preview did not prove the exact local-only update.")
    rows = preview.get("items", [])
    if len(rows) != 1 or str(rows[0].get("orderbook_id")) != action["orderbook_id"]:
        raise Blocked("Registry preview changed the instrument.")
    for key in ("holding", "active_buy_volume", "active_sell_volume", "open_buy_volume", "open_sell_volume",
                "strategy_class", "priority", "next_gate", "protection_classification", "protection_reason"):
        if rows[0].get(key) != item[key]:
            raise Blocked(f"Registry preview differs for {key}.")
    if not execute:
        return
    if state_summary(scoped_state(call, action)) != state_summary(state):
        raise Blocked("Broker state changed before the local registry update.")
    journal.append(event="METADATA_SUBMITTING", action_id=action["id"], request_sha256=digest(args))
    payload = call(REGISTRY_TOOL, {**args, "confirm": True})
    journal.append(event="METADATA_RESPONSE", action_id=action["id"], response=payload)
    # The native 'ok' uses strict completeness, which can be false for a
    # preserved, unrelated holding-only exception. Verify governance and the
    # exact target instead; never treat an arbitrary nested error as success.
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    p = result.get("position_strategy", {})
    if (payload.get("ok") is not True or result.get("error") or result.get("errors") or
        result.get("dry_run") is not False or result.get("broker_mutation") is not False or
        result.get("registered_count") != 1 or result.get("prune_stale") is not False or
        p.get("governance_complete") is not True):
        raise Blocked("Registry write requires reconciliation; no order retry.")
    matches = [r for r in p.get("positions", []) if str(r.get("orderbook_id")) == action["orderbook_id"]
               and str(r.get("account_id")) == args["account_id"]]
    if len(matches) != 1 or matches[0].get("position_strategy_status") != "RECORDED":
        raise Blocked("Registry target was not exactly recorded.")
    row = matches[0]
    if any(row.get(k) != item[k] for k in REGISTRY_LIVE_FIELDS):
        raise Blocked("Registry exposure readback differs from the reviewed plan.")
    excluded = REGISTRY_LIVE_FIELDS | {"order_book_id", "preserve_audit_exception_fingerprint"}
    if any(row.get("position_strategy", {}).get(k) != v for k, v in item.items() if k not in excluded):
        raise Blocked("Registry strategy readback differs from the reviewed plan.")
    if state_summary(scoped_state(call, action)) != state_summary(state):
        raise Blocked("Broker state changed during the local registry update.")
    audit = check_payload(call("avanza_position_strategy_audit", {k: args[k] for k in ("tenant_session_id", "account_id")}))["result"]
    if not audit_ready(audit):
        raise Blocked("Account drift remains after the exact local registry update.")
    journal.append(event="METADATA_VERIFIED", action_id=action["id"])


def preflight(call: Callable, action: dict[str, Any], plan: dict[str, Any], execute: bool, now: datetime,
              pending_registry: set[str] | None = None, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    args = action["arguments"]
    tenant, account = args["tenant_session_id"], args["account_id"]
    status = check_payload(call("avanza_status", {"tenant_session_id": tenant}))["result"]
    if status.get("mcp_contract_revision") != plan["contract_revision"] or action["tool"] not in status.get("available_tools", []):
        raise Blocked("Live MCP contract/tools differ from the reviewed manifest.")
    sessions = status.get("sessions", [])
    if not any(s.get("session_id") == tenant and s.get("auth_valid") is True for s in sessions):
        raise Blocked("The exact tenant is not authenticated.")
    accounts = check_payload(call("avanza_accounts", {"tenant_session_id": tenant}))["result"]
    matched_accounts = [row for row in accounts if str(row.get("ID")) == account]
    if len(matched_accounts) != 1:
        raise Blocked("Account does not belong to the exact authenticated tenant.")
    cash_text = str(matched_accounts[0].get("Buying Power", ""))
    if not cash_text.endswith(" SEK"):
        raise Blocked("Buying-power currency is unresolved.")
    try:
        cash = float(cash_text.removesuffix(" SEK"))
    except ValueError as exc:
        raise Blocked("Buying power is not a native numeric SEK value.") from exc
    if not math.isfinite(cash) or cash < action["quote_guard"]["min_buying_power_sek"]:
        raise Blocked("Buying power fell below the reviewed conditional-capacity floor.")
    if "min_capital_sek" in action["quote_guard"]:
        capital_text = str(matched_accounts[0].get("Total Value", ""))
        if not capital_text.endswith(" SEK"):
            raise Blocked("Account capital currency is unresolved.")
        try:
            capital = float(capital_text.removesuffix(" SEK"))
        except ValueError as exc:
            raise Blocked("Account capital is not numeric.") from exc
        if not math.isfinite(capital) or capital < action["quote_guard"]["min_capital_sek"]:
            raise Blocked("Capital fell below the reviewed factor/risk denominator.")
    if execute and (status.get("read_write") is not True or status.get("live_trading_allowed_for_this_session") is not True):
        raise Blocked("Existing scoped live authorization is OFF; this runner never enables it.")
    if now >= timestamp(plan["expires_at"]):
        raise Blocked("Manifest expired; obtain a newly reviewed plan, not a timestamp edit.")
    window = action["execution_window"]
    if not timestamp(window["start"]) <= now < timestamp(window["end"]):
        raise Blocked("Outside the reviewed regular-session execution window.")
    audit = check_payload(call("avanza_position_strategy_audit", {"tenant_session_id": tenant, "account_id": account}))["result"]
    if not audit_ready(audit, pending_registry):
        raise Blocked("Account strategy drift/errors remain unresolved.")
    state = scoped_state(call, action)
    if state_summary(state) != action["expected_state"]:
        raise Blocked("Holdings, stops or orders changed since review; no submission.")
    if "minimum_current_profit_percent" in action:
        value = str((state.get("position") or {}).get("Profit %", ""))
        try:
            profit = float(value.removesuffix("%")) if value.endswith("%") else math.nan
        except ValueError:
            profit = math.nan
        if not math.isfinite(profit) or profit < action["minimum_current_profit_percent"]:
            raise Blocked("Current position profit is unresolved or below the reviewed harvest floor.")
    if "post_position_plan" in action:
        if action["tool"] not in {"avanza_stoploss_set", "avanza_stoploss_set_batch", "avanza_order_set"}:
            raise Blocked("Post-position plans currently support new placements only.")
        projected = registry_fingerprint(state)
        for item in args.get("items", [args]):
            side = item["order_type"].lower()
            prefix = "active" if "stoploss" in action["tool"] else "open"
            projected[f"{prefix}_{side}_volume"] += number(item["volume"])
            projected[f"{prefix}_{side}_count"] += 1
        if any(action["post_position_plan"][k] != v for k, v in projected.items()):
            raise Blocked("Post-position plan does not equal the exact proposed order delta.")
    check_sell_capacity(state, action)
    if "stop_loss_id" in args or "order_id" in args:
        is_stop = "stop_loss_id" in args
        rows = state["active_buy_stops"] + state["active_sell_stops"] if is_stop else state["open_orders"]
        id_key = "stop_loss_id" if is_stop else "order_id"
        targets = [row for row in rows if row.get(id_key) == args[id_key]]
        if len(targets) != 1:
            raise Blocked("Edit/delete target is not in the exact reviewed instrument state.")
        if is_stop and action["tool"].endswith("edit") and targets[0].get("trigger_type") in {"FOLLOW_UPWARDS", "FOLLOW_DOWNWARDS"} and action.get("accept_trailing_reset") is not True:
            raise Blocked("Trailing replacement requires an explicit reviewed reset acknowledgement.")
    # Account/audit calls can be slow. Measure freshness at readback, not at
    # preflight entry, and refuse a window that elapsed during those calls.
    now = clock() if clock else now
    if now >= timestamp(plan["expires_at"]) or not timestamp(window["start"]) <= now < timestamp(window["end"]):
        raise Blocked("Manifest or execution window expired during preflight.")
    q, guard = state.get("quote", {}), action["quote_guard"]
    if q.get("error") or q.get("currency") != guard["currency"] or q.get("market") != guard["market"]:
        raise Blocked("Quote identity, market or currency is unresolved.")
    reported_age = number(q.get("quote_age_ms")) / 1000
    timestamp_age = (now - timestamp(q.get("timestamp"))).total_seconds()
    if min(reported_age, timestamp_age) < -5 or max(reported_age, timestamp_age) > guard["max_age_seconds"]:
        raise Blocked("Quote is stale or its clock is inconsistent.")
    if not guard["min"] <= number(q.get("last")) <= guard["max"]:
        raise Blocked("Quote moved outside the reviewed band; no automatic repricing.")
    if number(q.get("bid")) <= 0 or number(q.get("ask")) < number(q.get("bid")) or number(q.get("spread_percent")) > guard["max_spread_percent"]:
        raise Blocked("Invalid or excessive quote spread.")
    if str(q.get("trading_status") or "").upper() in {"HALTED", "SUSPENDED", "CLOSED", "AFTER_HOURS", "PRE_MARKET"}:
        raise Blocked("Instrument is not in regular trading.")
    return state


def verify_change(before: dict[str, Any], after: dict[str, Any], action: dict[str, Any], response: dict[str, Any]) -> None:
    args, tool = canonical_arguments(action["arguments"]), action["tool"]
    old, new = state_summary(before), state_summary(after)
    if old["holding"] != new["holding"]:
        raise Blocked("A fill changed holdings: reconcile it before continuing; never resubmit.")
    if tool == "avanza_stoploss_set_batch":
        items, results = args["items"], response.get("results", [])
        if response.get("all_ok") is not True or len(results) != len(items):
            raise Blocked("Batch partially failed; reconcile every completed stage without retry.")
        old_ids = set(old["stop_ids"])
        added = [r for r in after["active_buy_stops"] + after["active_sell_stops"] if r["stop_loss_id"] not in old_ids]
        if len(added) != len(items) or not old_ids <= set(new["stop_ids"]):
            raise Blocked("Batch stop identity readback is incomplete.")
        intermediate = copy.deepcopy(before)
        for index, item in enumerate(items):
            result = results[index]
            matches = [r for r in added if r["stop_loss_id"] == result.get("stop_loss_id")]
            if len(matches) != 1:
                raise Blocked("A batch result has no unique exact broker row.")
            row = matches[0]
            next_state = copy.deepcopy(intermediate)
            next_state["active_buy_stops" if row["side"] == "BUY" else "active_sell_stops"].append(row)
            child = {**action, "tool": "avanza_stoploss_set", "arguments": {**item, "tenant_session_id": args["tenant_session_id"], "account_id": args["account_id"]}}
            verify_change(intermediate, next_state, child, result)
            intermediate = next_state
        if state_summary(intermediate) != new:
            raise Blocked("Unexpected exposure changed during the batch.")
        return
    is_stop = "stoploss" in tool
    id_key = "stop_loss_id" if is_stop else "order_id"
    old_rows = before["active_buy_stops"] + before["active_sell_stops"] if is_stop else before["open_orders"]
    new_rows = after["active_buy_stops"] + after["active_sell_stops"] if is_stop else after["open_orders"]
    old_ids, new_ids = {r[id_key] for r in old_rows}, {r[id_key] for r in new_rows}
    target = args.get(id_key)
    if tool.endswith("delete"):
        if old_ids - new_ids != {target} or new_ids - old_ids:
            raise Blocked("Deletion readback is not exact.")
        if is_stop and response.get("strategy_metadata_removed") is not True:
            raise Blocked("Stop deletion metadata cleanup was not verified.")
    else:
        if tool.endswith("edit") and not is_stop:
            matches = [r for r in new_rows if r[id_key] == target]
            if old_ids != new_ids:
                raise Blocked("Unexpected regular-order identity change.")
        else:
            added = new_ids - old_ids
            removed = old_ids - new_ids
            if len(added) != 1 or removed != ({target} if tool.endswith("edit") else set()):
                raise Blocked("Placement/replacement readback is ambiguous.")
            matches = [r for r in new_rows if r[id_key] in added]
        if len(matches) != 1:
            raise Blocked("Exact submitted row was not found; it may have filled.")
        row = matches[0]
        if is_stop and response.get("stop_loss_id") != row[id_key]:
            raise Blocked("Submitted stop ID differs from the exact live readback.")
        if str(row.get("account_id")) != args["account_id"] or str(row.get("orderbook_id")) != action["orderbook_id"]:
            raise Blocked("Post-readback account/orderbook mismatch.")
        mapping = {"volume": "volume", "price": "price", "order_price": "order_price", "valid_until": "valid_until",
                   "trigger_type": "trigger_type", "trigger_value": "trigger_value", "trigger_value_type": "trigger_value_type",
                   "order_price_type": "order_price_type", "order_valid_days": "order_valid_days", "strategy_intent": "strategy_intent",
                   "strategy_reason": "strategy_reason"}
        for parameter, field in mapping.items():
            if parameter in args and row.get(field) != args[parameter]:
                raise Blocked(f"Post-readback mismatch for {parameter}; no further actions.")
        if "order_type" in args and str(row.get("side", "")).lower() != args["order_type"]:
            raise Blocked("Post-readback side mismatch.")
        if tool == "avanza_order_edit" and row.get("side") != next(r["side"] for r in old_rows if r[id_key] == target):
            raise Blocked("Regular-order side changed during edit.")
        if is_stop and (response.get("metadata_persisted") is not True or row.get("strategy_metadata_status") != "RECORDED"):
            raise Blocked("Stop metadata was not durably verified.")
    unchanged_ids = old_ids - ({target} if target else set())
    fields = STOP_FIELDS if is_stop else ORDER_FIELDS
    fingerprint = lambda row: digest({field: row.get(field) for field in fields})
    if {r[id_key]: fingerprint(r) for r in old_rows if r[id_key] in unchanged_ids} != {r[id_key]: fingerprint(r) for r in new_rows if r[id_key] in unchanged_ids}:
        raise Blocked("An unrelated row changed during submission.")
    other = ("order_ids", "order_fingerprints") if is_stop else ("stop_ids", "stop_fingerprints")
    if any(old[key] != new[key] for key in other):
        raise Blocked("Other instrument exposure changed during submission.")


def run(plan: dict[str, Any], call: Callable, console: Console, *, execute: bool, journal_path: Path, now: Callable = utcnow, show_reviews: bool = False) -> int:
    validate_plan(plan, now())
    table = Table(title="Reviewed MCP Actions", show_lines=True)
    for name in ("Action", "Account", "Instrument", "Tool", "Antal", "Distance / basis"):
        table.add_column(name)
    for action in plan["actions"]:
        args, display = action["arguments"], action["display"]
        table.add_row(action["id"], args["account_id"], action["label"], action["tool"].removeprefix("avanza_"),
                      str(sum(item["volume"] for item in args["items"]) if "items" in args else args.get("volume", "existing")),
                      f"{display['distance_percent']:+.2f}% / {display['basis']}")
    console.print(table)
    console.print(f"Plan: {plan['plan_id']} | SHA256: {digest(plan)}", style="cyan", markup=False)
    console.print(f"Mode: {'LIVE EXECUTION' if execute else 'DRY RUN - no submissions'}", style="bold red" if execute else "bold cyan")
    if plan.get("registry_updates"):
        console.print(f"{len(plan['registry_updates'])} exact local-registry repair(s) are included; these are not broker orders.", style="cyan")
    if plan.get("proposals"):
        proposals = Table(title="Proposals Only - Not Awaiting Broker Triggers")
        for heading in ("Account", "Instrument", "Status"):
            proposals.add_column(heading, overflow="fold")
        for row in plan["proposals"]:
            proposals.add_row(*(Text(str(row.get(field, ""))) for field in ("account_id", "instrument", "status")))
        console.print(proposals)
    if plan["reviews"]:
        if plan.get("decision_coverage"):
            counts = {kind: sum(r["disposition"] == kind for r in plan["reviews"])
                      for kind in ("ACTION_PREPARED", "NO_ACTION_DECISION", "EVIDENCE_BLOCKED")}
            console.print(f"{len(plan['reviews'])} account-position decisions: {counts['ACTION_PREPARED']} with prepared actions, "
                          f"{counts['NO_ACTION_DECISION']} no-action decisions, {counts['EVIDENCE_BLOCKED']} evidence-blocked.", style="cyan")
            console.print("Decision coverage is not broker execution or portfolio-governance completion.", style="yellow")
        else:
            console.print(f"{len(plan['reviews'])} non-executable review rows remain; this run cannot claim portfolio completion.", style="yellow")
        if not all(isinstance(row, dict) for row in plan["reviews"]):
            raise Blocked("Review rows must be objects.")
        if not show_reviews:
            console.print("Full instrument decisions are in the private report; --details includes them here.", style="dim")
    if plan["reviews"] and show_reviews:
        reviews = Table(title="Instrument Decisions (Not Additional Orders)", show_lines=True)
        for heading in ("Account", "Instrument", "Antal", "Decision", "Next gate"):
            reviews.add_column(heading, overflow="fold")
        for row in plan["reviews"]:
            reviews.add_row(*(Text(str(row.get(field, ""))) for field in (
                "account_id", "instrument", "current_holding", "status", "next_gate")))
        console.print(reviews)
    if not plan["actions"]:
        console.print("No executable actions. Review the manifest's blocked decisions.", style="yellow")
        return 2
    journal = Journal(journal_path) if execute else None
    attempted_tenants: set[str] = set()
    portfolio_guards = copy.deepcopy(plan.get("portfolio_guards", []))
    code = 0
    try:
        # Preflight every row before the first mutation. Same-instrument staged
        # actions belong in separate freshly reviewed plans, not guessed states.
        scopes = [(a["arguments"]["tenant_session_id"], a["arguments"]["account_id"], a["orderbook_id"]) for a in plan["actions"]]
        if len(scopes) != len(set(scopes)):
            raise Blocked("Use one canonical stop batch for same-instrument ladders; re-review other follow-ups.")
        account_scopes = {(scope[0], scope[1]) for scope in scopes}
        check_portfolio_guards(call, portfolio_guards)
        for update in plan.get("registry_updates", []):
            if (update["tenant_session_id"], update["account_id"]) not in account_scopes:
                raise Blocked("Registry repair is outside this manifest's account scopes.")
            ref = {"id": update["id"], "orderbook_id": update["orderbook_id"], "arguments": update}
            state = scoped_state(call, ref)
            if state_summary(state) != update["expected_state"]:
                raise Blocked("Registry repair's reviewed broker state changed.")
            write_reviewed_registry(call, ref, update["item"], state, execute=False)
        for action in plan["actions"]:
            if journal and (action["id"] in journal.attempted or digest({"tool": action["tool"], "arguments": action["arguments"]}) in journal.requests):
                raise Blocked("This action/request was already submitted or is uncertain. Never retry it blindly.")
            pending = {u["orderbook_id"] for u in plan.get("registry_updates", [])
                       if (u["tenant_session_id"], u["account_id"]) ==
                       (action["arguments"]["tenant_session_id"], action["arguments"]["account_id"])}
            preflight(call, action, plan, execute, now(), pending_registry=pending, clock=now)
            preview = check_payload(call(action["tool"], {**action["arguments"], "confirm": False}))["result"]
            verify_preview(action, preview)
            console.print(f"PASS  {action['id']}  scoped preflight and native preview", style="green", markup=False)
        if not execute:
            console.print("Dry run complete. No live authorization, broker or paper state changed.", style="bold green")
            return 0
        check_portfolio_guards(call, portfolio_guards)
        for update in plan.get("registry_updates", []):
            ref = {"id": update["id"], "orderbook_id": update["orderbook_id"], "arguments": update}
            state = scoped_state(call, ref)
            if state_summary(state) != update["expected_state"]:
                raise Blocked("Broker state changed before the reviewed registry repair.")
            write_reviewed_registry(call, ref, update["item"], state, execute=True, journal=journal)
            console.print(f"VERIFIED LOCAL REGISTRY  {update['id']}", style="green", markup=False)
        for action in plan["actions"]:
            check_portfolio_guards(call, portfolio_guards)
            before = preflight(call, action, plan, True, now(), clock=now)
            tenant = action["arguments"]["tenant_session_id"]
            attempted_tenants.add(tenant)
            request_hash = digest({"tool": action["tool"], "arguments": action["arguments"]})
            journal.append(event="SUBMITTING", plan_id=plan["plan_id"], action_id=action["id"], request_sha256=request_hash,
                           tool=action["tool"], tenant_session_id=tenant, account_id=action["arguments"]["account_id"],
                           reviewed_action=action, manifest_sha256=digest(plan))
            payload = call(action["tool"], {**action["arguments"], "confirm": True})
            journal.append(event="RESPONSE", action_id=action["id"], response=payload)
            response = check_payload(payload)["result"]
            if response.get("dry_run") is not False or response.get("warnings") or response.get("warning"):
                raise Blocked("Submission has warnings or does not prove a live action.")
            after = scoped_state(call, action)
            verify_change(before, after, action, response)
            advance_portfolio_guard(portfolio_guards, action, after)
            journal.append(event="VERIFIED", action_id=action["id"], state=state_summary(after))
            if "post_position_plan" in action:
                write_reviewed_registry(call, action, action["post_position_plan"], after, execute=True, journal=journal)
            console.print(f"VERIFIED  {action['id']}", style="bold green", markup=False)
        console.print("Submitted actions verified. Recovery-lot ledgers and subsequent fills still require reviewed reconciliation.", style="green")
    except (Exception, KeyboardInterrupt) as exc:
        code = 1
        console.print(f"STOPPED: {exc}", style="bold red", markup=False)
        if journal:
            journal.append(event="STOPPED", reason=type(exc).__name__, detail=str(exc))
    finally:
        # Never switch on permissions. Revoke only scopes in which this run
        # attempted a submission, and continue cleanup if one tenant is offline.
        for tenant in sorted(attempted_tenants):
            try:
                check_payload(call("avanza_live_session_revoke", {"tenant_session_id": tenant}))
                status = check_payload(call("avanza_status", {"tenant_session_id": tenant}))["result"]
                if status.get("live_trading_allowed_for_this_session") is not False:
                    raise Blocked("Authorization OFF was not verified.")
                journal.append(event="AUTHORIZATION_OFF", tenant_session_id=tenant)
                console.print(f"Authorization OFF: {tenant}", style="cyan", markup=False)
            except Exception as exc:
                code = 1
                console.print(f"CRITICAL: revoke/verify {tenant} manually in the existing UI. {exc}", style="bold red", markup=False)
        if journal:
            journal.close()
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Scoped preflight and native previews only (default).")
    mode.add_argument("--execute", action="store_true", help="Submit the exact manifest using existing scoped live authorization.")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--details", action="store_true", help="Include all non-executable instrument reviews.")
    args = parser.parse_args(argv)
    console = Console(no_color=args.no_color, highlight=False)
    try:
        plan = load_json(args.manifest)
        validate_plan(plan, utcnow())
        return run(plan, ExistingBridge() if plan["actions"] else None, console, execute=args.execute,
                   journal_path=ROOT / "output" / "MCP_USER_ACTION_JOURNAL.jsonl", show_reviews=args.details)
    except (Exception, KeyboardInterrupt) as exc:
        console.print(f"BLOCKED: {exc}", style="bold red", markup=False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
