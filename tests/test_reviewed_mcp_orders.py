"""Offline-only tests. No account tool, keychain or broker transport is used."""

import copy
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from rich.console import Console
from rich.text import Text

from scripts import run_reviewed_mcp_orders as runner

NOW = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_native_preview_date(monkeypatch):
    from avanza_mcp import rendering
    monkeypatch.setattr(rendering, "validate_valid_until", lambda value, label: value)


def native_preview(tool, args):
    from avanza_mcp.rendering import build_order_preview, build_stop_loss_preview
    from avanza_mcp.strategy_intent import validate_mcp_stoploss_strategy_intent
    if tool == "avanza_stoploss_set_batch":
        return {"dry_run": True, "account_id": args["account_id"], "results": [
            native_preview("avanza_stoploss_set", {**item, "account_id": args["account_id"]}) for item in args["items"]]}
    if "stoploss" in tool and not tool.endswith("delete"):
        _, _, request = build_stop_loss_preview(args)
        validate_mcp_stoploss_strategy_intent(args, request, live=False)
        if tool.endswith("edit"):
            request = {"stop_loss_id": args["stop_loss_id"], "replacement": request}
    elif tool == "avanza_order_set":
        _, _, request = build_order_preview(args)
    else:
        request = {k: v for k, v in args.items() if k not in {"confirm", "tenant_session_id"}}
    return {"dry_run": True, "request": request}


def state():
    return {
        "account_id": "demo-account", "orderbook_id": "demo-book",
        "position": {"volume": 10}, "active_buy_stops": [], "active_sell_stops": [],
        "open_orders": [], "failed_orders": [], "non_active_or_error_stops": [],
        "quote": {"timestamp": NOW.isoformat(), "quote_age_ms": 1000, "market": "NASDAQ",
                  "currency": "USD", "last": 100, "bid": 99.9, "ask": 100.1, "spread_percent": 0.2},
    }


def plan():
    return {
        "version": 1, "plan_id": "DEMO-REVIEW", "created_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(), "contract_revision": "demo-contract",
        "reviews": [], "actions": [{
            "id": "DEMO-ONE", "label": "Demonstration instrument", "tool": "avanza_stoploss_set",
            "orderbook_id": "demo-book", "arguments": {
                "tenant_session_id": "demo-tenant", "account_id": "demo-account", "order_book_id": "demo-book",
                "trigger_type": "LESS_OR_EQUAL", "trigger_value": 95, "trigger_value_type": "SEK",
                "valid_until": "2026-09-10", "order_type": "buy", "order_price": 95,
                "order_price_type": "SEK", "volume": 2, "order_valid_days": 1,
                "trigger_on_market_maker_quote": False, "short_selling_allowed": False,
                "strategy_intent": "DEEP_RESIDUAL", "strategy_reason": "Reviewed demonstration only.",
            }, "expected_state": runner.state_summary(state()),
            "execution_window": {"start": (NOW - timedelta(hours=1)).isoformat(),
                                 "end": (NOW + timedelta(hours=1)).isoformat(), "state": "REGULAR_SESSION"},
            "quote_guard": {"currency": "USD", "market": "NASDAQ", "min": 98, "max": 102,
                            "max_age_seconds": 10, "max_spread_percent": 0.4, "min_buying_power_sek": 1000},
            "evidence": {"reviewed_at": NOW.isoformat(), "reason": "Mocked reviewed evidence.",
                         "source": "synthetic fixture", "gates": dict.fromkeys(runner.GATES, True)},
            "display": {"distance_percent": -5, "basis": "sold reference"},
        }],
    }


class FakeBridge:
    def __init__(self):
        self.current = state()
        self.calls = []
        self.authorized = True
        self.audit_complete = True
        self.fail_after_submission = False
        self.bad_metadata = False
        self.fail_revoke = False
        self.interrupt = False
        self.fill_immediately = False

    def __call__(self, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        if tool == "avanza_status":
            result = {"mcp_contract_revision": "demo-contract", "available_tools": sorted(runner.TOOLS),
                      "sessions": [{"session_id": "demo-tenant", "auth_valid": True}],
                      "read_write": True, "live_trading_allowed_for_this_session": self.authorized}
        elif tool == "avanza_accounts":
            result = [{"ID": "demo-account", "Buying Power": "2000.0 SEK"}]
        elif tool == "avanza_position_strategy_audit":
            result = {"governance_complete": self.audit_complete, "stop_error_count": 0, "order_error_count": 0}
        elif tool == "avanza_instrument_state":
            result = copy.deepcopy(self.current)
        elif tool == "avanza_live_session_revoke":
            if self.fail_revoke:
                raise OSError("Offline")
            self.authorized = False
            result = {"ok": True}
        elif tool in runner.TOOLS:
            if not args["confirm"]:
                return {"ok": True, "result": native_preview(tool, args)}
            if self.interrupt:
                raise KeyboardInterrupt()
            if self.fail_after_submission:
                raise TimeoutError("Ambiguous response")
            if self.fill_immediately:
                self.current["position"]["volume"] += args["volume"]
            elif tool == "avanza_stoploss_delete":
                self.current["active_buy_stops"] = []
            else:
                row = {k: v for k, v in args.items() if k not in {"confirm", "tenant_session_id"}}
                row = runner.canonical_arguments(row)
                row.update(stop_loss_id="NEW-STOP", orderbook_id=args["order_book_id"], side=args["order_type"].upper(),
                           strategy_metadata_status="MISSING" if self.bad_metadata else "RECORDED")
                if tool == "avanza_stoploss_edit":
                    self.current["active_buy_stops"] = []
                self.current["active_buy_stops"].append(row)
            result = {"dry_run": False, "ok": True, "metadata_persisted": not self.bad_metadata,
                      "stop_loss_id": "NEW-STOP", "strategy_metadata_removed": tool.endswith("delete")}
        else:
            raise AssertionError(f"Unexpected test call: {tool}")
        return {"ok": True, "result": result}


def execute(tmp_path, bridge=None, manifest=None, live=False):
    bridge = bridge or FakeBridge()
    output = io.StringIO()
    code = runner.run(manifest or plan(), bridge, Console(file=output, force_terminal=True, no_color=False, color_system="truecolor", width=120),
                      execute=live, journal_path=tmp_path / "journal.jsonl", now=lambda: NOW)
    return code, output.getvalue(), bridge


def portfolio_snapshot(current=None):
    current = current or state()
    return {
        "account_id": "demo-account",
        "portfolio": {"account_id": "demo-account", "positions": [{
            "account_id": "demo-account", "orderbook_id": "demo-book",
            "volume": current["position"]["volume"], "Value": "1000.0 SEK",
        }]},
        "stoplosses": {"account_id": "demo-account", "stoplosses": [
            {**r, "status": "ACTIVE"} for r in current["active_buy_stops"] + current["active_sell_stops"]]},
        "open_orders": {"account_id": "demo-account", "orders": current["open_orders"],
                        "fund_orders": [], "fund_order_count": 0},
    }


def guarded_plan():
    p = plan()
    snapshot = portfolio_snapshot()
    p["portfolio_guards"] = [{"tenant_session_id": "demo-tenant", "account_id": "demo-account",
                              "snapshot": snapshot, "expected_state": runner.portfolio_fingerprint(snapshot, "demo-account"),
                              "position_value_ceilings_sek": {"demo-book": 1050}}]
    p["reviews"] = [{"tenant_session_id": "demo-tenant", "account_id": "demo-account", "orderbook_id": "demo-book",
                      "instrument": "Demonstration", "current_holding": 10, "disposition": "ACTION_PREPARED",
                      "action_ids": ["DEMO-ONE"], "status": "PREPARED_NOT_PLACED",
                      "reason": "Synthetic bounded recovery.", "next_gate": "Exact operator preflight."}]
    p["decision_coverage"] = {"recorded_rows": 1, "scope_ids": ["demo-tenant/demo-account/demo-book"]}
    return p


class PortfolioBridge(FakeBridge):
    def __init__(self):
        super().__init__()
        self.changed = False
        self.value = "1000.0 SEK"

    def __call__(self, tool, args):
        if tool != "avanza_live_snapshot":
            return super().__call__(tool, args)
        self.calls.append((tool, copy.deepcopy(args)))
        snapshot = portfolio_snapshot(self.current)
        snapshot["portfolio"]["positions"][0]["Value"] = self.value
        if self.changed:
            snapshot["portfolio"]["positions"].append({
                "account_id": "demo-account", "orderbook_id": "unrelated", "volume": 1, "Value": "100.0 SEK"})
        return {"ok": True, "result": snapshot}


def test_full_decision_accounting_and_guarded_preview(tmp_path):
    code, output, bridge = execute(tmp_path, PortfolioBridge(), guarded_plan())
    output = " ".join(Text.from_ansi(output).plain.split())
    assert code == 0 and "1 account-position decisions" in output
    assert "0 evidence-blocked" in output and "portfolio-governance completion" in output
    assert not any(a.get("confirm") for _, a in bridge.calls)


@pytest.mark.parametrize("mutation", [
    lambda p: p["decision_coverage"].update(recorded_rows=True),
    lambda p: p["reviews"][0].update(disposition="NO_ACTION_DECISION"),
    lambda p: p["reviews"][0].update(account_id="another-account"),
    lambda p: p["reviews"][0].update(action_ids=[]),
    lambda p: p["reviews"][0].update(reason=""),
    lambda p: p["portfolio_guards"][0].update(position_value_ceilings_sek={}),
    lambda p: p["portfolio_guards"][0]["expected_state"]["holdings"].update({"demo-book": 11}),
])
def test_invalid_decision_or_guard_contract_fails_closed(mutation):
    p = guarded_plan()
    mutation(p)
    with pytest.raises(runner.Blocked):
        runner.validate_plan(p, NOW)


@pytest.mark.parametrize("value", ["1050.01 SEK", "1000.0 USD", "nan SEK", "bad SEK"])
def test_factor_value_ceiling_and_currency_block_before_submission(tmp_path, value):
    bridge = PortfolioBridge()
    bridge.value = value
    code, output, bridge = execute(tmp_path, bridge, guarded_plan(), live=True)
    assert code == 1 and "factor stress" in output
    assert not any(a.get("confirm") for _, a in bridge.calls)


def test_unrelated_holding_change_blocks_entire_batch(tmp_path):
    bridge = PortfolioBridge()
    bridge.changed = True
    code, output, bridge = execute(tmp_path, bridge, guarded_plan(), live=True)
    assert code == 1 and "Portfolio-wide" in output
    assert not any(a.get("confirm") for _, a in bridge.calls)


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(account_id="other"),
    lambda s: s["portfolio"]["positions"].append(copy.deepcopy(s["portfolio"]["positions"][0])),
    lambda s: s["open_orders"].update(fund_orders=[{}], fund_order_count=1),
    lambda s: s["open_orders"].pop("orders"),
])
def test_incomplete_or_duplicate_portfolio_inventory_is_rejected(mutation):
    snapshot = portfolio_snapshot()
    mutation(snapshot)
    with pytest.raises(runner.Blocked):
        runner.portfolio_fingerprint(snapshot, "demo-account")


def test_guard_advances_only_verified_instrument(tmp_path):
    bridge = PortfolioBridge()
    p = guarded_plan()
    code, _, _ = execute(tmp_path, bridge, p, live=True)
    assert code == 0
    guards = copy.deepcopy(p["portfolio_guards"])
    runner.advance_portfolio_guard(guards, p["actions"][0], bridge.current)
    runner.check_portfolio_guards(bridge, guards)
    assert not p["portfolio_guards"][0]["expected_state"]["stops"]
    bridge.changed = True
    with pytest.raises(runner.Blocked, match="Portfolio-wide"):
        runner.check_portfolio_guards(bridge, guards)


def test_missing_live_scope_cannot_be_hidden_in_decision_count():
    p = guarded_plan()
    row = {**p["reviews"][0], "orderbook_id": "omitted-scope", "disposition": "NO_ACTION_DECISION", "action_ids": []}
    p["reviews"].append(row)
    p["decision_coverage"]["recorded_rows"] = 2
    p["decision_coverage"]["scope_ids"].append("demo-tenant/demo-account/omitted-scope")
    with pytest.raises(runner.Blocked, match="live portfolio universe"):
        runner.validate_plan(p, NOW)


def mutations(bridge):
    return [(t, a) for t, a in bridge.calls if t in runner.TOOLS and a.get("confirm") is True]


def reviewed_registry_item(orderbook="demo-book", buy_volume=2, buy_count=1):
    return {
        "order_book_id": orderbook, "instrument": "Demonstration instrument", "ticker": "DEMO", "venue": "NASDAQ",
        "holding": 10, "active_buy_volume": buy_volume, "active_buy_count": buy_count,
        "active_sell_volume": 0, "active_sell_count": 0, "open_buy_volume": 0, "open_sell_volume": 0,
        "open_buy_count": 0, "open_sell_count": 0, "strategy_class": "CORE", "horizon": "1-3m",
        "thesis": "Synthetic reviewed thesis.", "gate": "Synthetic gate.", "audit_status": "REVIEWED",
        "recommendation": "Retain the reviewed core.", "priority": "B", "bucket": "CORE", "stance": "HOLD",
        "next_gate": "Review a fill immediately.", "protection_classification": "CORE_HOLD_EXCEPTION",
        "protection_reason": "Synthetic explicit retained-core decision.",
    }


class RegistryBridge(FakeBridge):
    def __init__(self):
        super().__init__()
        self.states = {"demo-book": self.current, "demo-two": {**state(), "orderbook_id": "demo-two"}}
        self.bad_registry = False
        self.strict_incomplete = False

    def __call__(self, tool, args):
        if tool == "avanza_instrument_state":
            self.current = self.states[args["orderbook_id"]]
        if tool == runner.REGISTRY_TOOL:
            self.calls.append((tool, copy.deepcopy(args)))
            item = args["items"][0]
            row = {**item, "account_id": args["account_id"], "orderbook_id": str(item["order_book_id"])}
            if not args["confirm"]:
                return {"ok": True, "result": {"dry_run": True, "broker_mutation": False,
                        "account_id": args["account_id"], "count": 1, "items": [row]}}
            self.audit_complete = True
            row.update(position_strategy_status="RECORDED", position_strategy=copy.deepcopy(item))
            if self.bad_registry:
                row["position_strategy"]["next_gate"] = "Unexpected semantics"
            return {"ok": True, "result": {"ok": not self.strict_incomplete, "dry_run": False,
                    "broker_mutation": False, "registered_count": 1, "prune_stale": False,
                    "position_strategy": {"governance_complete": True, "positions": [row]}}}
        result = super().__call__(tool, args)
        if tool in runner.TOOLS and args.get("confirm"):
            self.audit_complete = False
        return result


def test_post_registry_plan_is_preview_only_in_dry_run(tmp_path):
    p = plan()
    p["actions"][0]["post_position_plan"] = reviewed_registry_item()
    code, _, bridge = execute(tmp_path, RegistryBridge(), p)
    assert code == 0
    assert not any(a.get("confirm") for _, a in bridge.calls)


def test_bad_projected_registry_plan_blocks_before_order(tmp_path):
    p = plan()
    p["actions"][0]["post_position_plan"] = reviewed_registry_item(buy_volume=3)
    code, _, bridge = execute(tmp_path, RegistryBridge(), p, live=True)
    assert code == 1 and not mutations(bridge)


def test_multi_instrument_plan_preserves_audit_between_submissions(tmp_path):
    p = plan()
    first = p["actions"][0]
    first["post_position_plan"] = reviewed_registry_item()
    second = copy.deepcopy(first)
    second.update(id="DEMO-TWO", orderbook_id="demo-two")
    second["arguments"]["order_book_id"] = "demo-two"
    second["post_position_plan"]["order_book_id"] = "demo-two"
    p["actions"].append(second)
    code, _, bridge = execute(tmp_path, RegistryBridge(), p, live=True)
    assert code == 0 and len(mutations(bridge)) == 2
    assert len([a for t, a in bridge.calls if t == runner.REGISTRY_TOOL and a["confirm"]]) == 2
    assert not bridge.authorized


def test_registry_failure_after_order_never_retries_and_revokes(tmp_path):
    p = plan()
    p["actions"][0]["post_position_plan"] = reviewed_registry_item()
    bridge = RegistryBridge()
    bridge.bad_registry = True
    code, _, bridge = execute(tmp_path, bridge, p, live=True)
    assert code == 1 and len(mutations(bridge)) == 1 and not bridge.authorized
    bridge.bad_registry = False
    bridge.audit_complete = True
    code, _, bridge = execute(tmp_path, bridge, p, live=True)
    assert code == 1 and len(mutations(bridge)) == 1


def test_unrelated_holding_exception_does_not_mask_exact_target_readback(tmp_path):
    p = plan()
    p["actions"][0]["post_position_plan"] = reviewed_registry_item()
    bridge = RegistryBridge()
    bridge.strict_incomplete = True
    code, _, _ = execute(tmp_path, bridge, p, live=True)
    assert code == 0


def test_registry_updates_are_local_only_and_exactly_scoped(tmp_path):
    p = plan()
    p["registry_updates"] = [{"id": "LOCAL-REPAIR", "tenant_session_id": "demo-tenant",
        "account_id": "demo-account", "orderbook_id": "demo-book", "reason": "Verified completed change.",
        "expected_state": runner.state_summary(state()), "item": reviewed_registry_item(buy_volume=0, buy_count=0)}]
    code, _, bridge = execute(tmp_path, RegistryBridge(), p)
    assert code == 0 and not any(a.get("confirm") for _, a in bridge.calls)
    p["registry_updates"][0]["expected_state"]["holding"] = 11
    code, _, bridge = execute(tmp_path, RegistryBridge(), p, live=True)
    assert code == 1 and not mutations(bridge)


def test_registry_cannot_rebaseline_audit_exception():
    p = plan()
    p["actions"][0]["post_position_plan"] = reviewed_registry_item()
    p["actions"][0]["post_position_plan"]["audit_exception"] = {"kind": "POST_MANUAL_EXIT_DRIFT"}
    with pytest.raises(runner.Blocked, match="audit exceptions"):
        runner.validate_plan(p, NOW)


def test_pending_metadata_never_masks_unrelated_audit_drift():
    p = dict.fromkeys(("missing_count", "registry_unavailable_count", "stale_plan_count", "protection_missing_count",
        "protection_invalid_count", "protection_repair_required_count", "protection_registry_unavailable_count"), 0)
    p.update(unresolved_mismatch_orderbook_ids=["demo-book"], protection_contradiction_orderbook_ids=["demo-book"])
    a = {"governance_complete": False, "stop_error_count": 0, "order_error_count": 0, "position_strategy": p}
    assert runner.audit_ready(a, {"demo-book"})
    assert not runner.audit_ready(a)
    p["unresolved_mismatch_orderbook_ids"].append("unreviewed")
    assert not runner.audit_ready(a, {"demo-book"})


def allocation(**changes):
    return {"stage_index": 0, "sale_lot_id": "LOT-ONE", "raw_transaction_id": "RAW-SELL-ONE",
            "sold_antal": 4, "remaining_before_antal": 4, "allocation_antal": 2,
            "planned_source_id": "PLANNED-ONE", "sold_reference": 100, "entry_drop_percent": 5, **changes}


@pytest.mark.parametrize("change", ["underallocated", "overallocated", "wrong_discount", "duplicate", "foreign_stage"])
def test_recovery_allocations_fail_closed(change):
    p = plan()
    rows = [allocation()]
    p["actions"][0]["recovery_allocations"] = rows
    if change == "underallocated":
        rows[0]["allocation_antal"] = 1
    elif change == "overallocated":
        rows[0]["remaining_before_antal"] = 1
    elif change == "wrong_discount":
        rows[0]["entry_drop_percent"] = 6
    elif change == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    else:
        rows[0]["stage_index"] = 1
    with pytest.raises(runner.Blocked):
        runner.validate_plan(p, NOW)


def test_one_recovery_order_can_fully_allocate_across_distinct_lots():
    p = plan()
    p["actions"][0]["recovery_allocations"] = [allocation(allocation_antal=1),
        allocation(sale_lot_id="LOT-TWO", raw_transaction_id="RAW-SELL-TWO", allocation_antal=1)]
    runner.validate_plan(p, NOW)


def test_capital_floor_blocks_unknown_denominator(tmp_path):
    p = plan()
    p["actions"][0]["quote_guard"]["min_capital_sek"] = 100
    code, _, bridge = execute(tmp_path, manifest=p, live=True)
    assert code == 1 and not mutations(bridge)


def test_regular_day_limit_updates_registry_and_journals_exact_allocation(tmp_path):
    class LimitBridge(RegistryBridge):
        def __call__(self, tool, args):
            if tool == "avanza_order_set" and args.get("confirm"):
                self.calls.append((tool, copy.deepcopy(args)))
                self.current["open_orders"].append({"order_id": "EXACT-LIMIT", "account_id": args["account_id"],
                    "orderbook_id": args["order_book_id"], "side": "BUY", "volume": args["volume"],
                    "price": args["price"], "valid_until": args["valid_until"]})
                self.audit_complete = False
                return {"ok": True, "result": {"dry_run": False, "order_id": "EXACT-LIMIT"}}
            return super().__call__(tool, args)
    p = plan()
    a = p["actions"][0]
    a["tool"] = "avanza_order_set"
    a["arguments"] = {"tenant_session_id": "demo-tenant", "account_id": "demo-account", "order_book_id": "demo-book",
        "order_type": "buy", "condition": "normal", "price": 95, "volume": 2, "valid_until": "2026-09-09"}
    a["recovery_allocations"] = [allocation()]
    a["post_position_plan"] = reviewed_registry_item(buy_volume=0, buy_count=0)
    a["post_position_plan"].update(open_buy_volume=2, open_buy_count=1)
    code, _, bridge = execute(tmp_path, LimitBridge(), p, live=True)
    assert code == 0 and not bridge.authorized
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    submitted = next(r for r in records if r["event"] == "SUBMITTING")
    assert submitted["reviewed_action"]["recovery_allocations"] == [allocation()]
    assert submitted["manifest_sha256"] == runner.digest(p)


def test_dry_run_never_submits_or_changes_authorization(tmp_path):
    bridge = FakeBridge()
    bridge.authorized = False
    code, output, bridge = execute(tmp_path, bridge)
    assert code == 0
    assert not mutations(bridge)
    assert not any(t == "avanza_live_session_revoke" for t, _ in bridge.calls)
    assert not (tmp_path / "journal.jsonl").exists()
    assert "DRY RUN" in output and "\x1b[" in output


def test_execution_verifies_and_revokes(tmp_path):
    code, _, bridge = execute(tmp_path, live=True)
    assert code == 0
    assert len(mutations(bridge)) == 1
    assert bridge.authorized is False
    rows = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert [r["event"] for r in rows] == ["SUBMITTING", "RESPONSE", "VERIFIED", "AUTHORIZATION_OFF"]
    assert (tmp_path / "journal.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("field,value", [("authorized", False), ("audit_complete", False)])
def test_existing_permissions_and_audit_are_required(tmp_path, field, value):
    bridge = FakeBridge()
    setattr(bridge, field, value)
    code, _, bridge = execute(tmp_path, bridge, live=True)
    assert code == 1 and not mutations(bridge)
    assert not any(t == "avanza_live_session_authorize" for t, _ in bridge.calls)


@pytest.mark.parametrize("change", ["quantity", "scope", "quote", "spread", "currency", "failed_order", "fingerprint"])
def test_live_drift_blocks_submission(tmp_path, change):
    bridge = FakeBridge()
    if change == "quantity":
        bridge.current["position"]["volume"] = 11
    elif change == "scope":
        bridge.current["account_id"] = "wrong-account"
    elif change == "quote":
        bridge.current["quote"]["quote_age_ms"] = 20000
    elif change == "spread":
        bridge.current["quote"]["spread_percent"] = 1
    elif change == "currency":
        bridge.current["quote"]["currency"] = None
    elif change == "failed_order":
        bridge.current["failed_orders"] = [{"status": "FAILED"}]
    else:
        bridge.current["open_orders"] = [{"order_id": "unexpected", "volume": 1, "side": "BUY"}]
    code, _, bridge = execute(tmp_path, bridge, live=True)
    assert code == 1 and not mutations(bridge)


@pytest.mark.parametrize("failure", ["fail_after_submission", "bad_metadata", "interrupt", "fill_immediately"])
def test_uncertain_actions_stop_revoke_and_never_retry(tmp_path, failure):
    bridge = FakeBridge()
    setattr(bridge, failure, True)
    code, _, bridge = execute(tmp_path, bridge, live=True)
    assert code == 1 and len(mutations(bridge)) == 1
    assert bridge.authorized is False
    fresh = FakeBridge()
    code, _, fresh = execute(tmp_path, fresh, live=True)
    assert code == 1 and not mutations(fresh)


def test_revoke_failure_is_not_reported_success(tmp_path):
    bridge = FakeBridge()
    bridge.fail_revoke = True
    code, output, _ = execute(tmp_path, bridge, live=True)
    assert code == 1 and "CRITICAL" in output


@pytest.mark.parametrize("change", ["duplicate", "alias", "confirm", "nan", "zero", "missing_gate", "expiry", "future_evidence", "tool", "unknown_parameter"])
def test_invalid_manifests_fail_offline(change):
    manifest = plan()
    action = manifest["actions"][0]
    if change == "duplicate":
        manifest["actions"].append(copy.deepcopy(action))
    elif change == "alias":
        action["arguments"]["session_id"] = "legacy"
    elif change == "confirm":
        action["arguments"]["confirm"] = True
    elif change == "nan":
        action["arguments"]["volume"] = float("nan")
    elif change == "zero":
        action["arguments"]["volume"] = 0
    elif change == "missing_gate":
        action["evidence"]["gates"]["full_friction_churn"] = False
    elif change == "expiry":
        manifest["expires_at"] = (NOW + timedelta(days=3)).isoformat()
    elif change == "future_evidence":
        action["evidence"]["reviewed_at"] = (NOW + timedelta(minutes=1)).isoformat()
    elif change == "tool":
        action["tool"] = "avanza_live_session_authorize"
    else:
        action["arguments"]["bypass"] = True
    with pytest.raises(runner.Blocked):
        runner.validate_plan(manifest, NOW)


def test_empty_action_plan_reports_unfinished_review_without_calls(tmp_path):
    manifest = plan()
    manifest["actions"] = []
    manifest["reviews"] = [{"instrument": "demo", "status": "PERCENTAGE_NOT_SET"}]
    code, output, bridge = execute(tmp_path, manifest=manifest, live=True)
    assert code == 2 and not bridge.calls
    assert "No executable actions" in output


def test_duplicate_json_fields_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"version": 1, "version": 2}')
    with pytest.raises(runner.Blocked, match="Duplicate JSON"):
        runner.load_json(path)


def test_journal_lock_prevents_concurrent_runs(tmp_path):
    first = runner.Journal(tmp_path / "journal.jsonl")
    try:
        with pytest.raises(BlockingIOError):
            runner.Journal(tmp_path / "journal.jsonl")
    finally:
        first.close()


def test_journal_symlink_rejected(tmp_path):
    target = tmp_path / "target"
    target.write_text("")
    link = tmp_path / "journal.jsonl"
    link.symlink_to(target)
    with pytest.raises(OSError):
        runner.Journal(link)


def test_bridge_reuses_existing_transport_without_starting_server(monkeypatch):
    from avanza_mcp.mcp import proxy
    monkeypatch.setattr(proxy, "load_mcp_session", lambda: {"url": "http://127.0.0.1:12345", "token": "synthetic"})
    calls = []
    monkeypatch.setattr(proxy, "call_mcp_bridge", lambda session, tool, args: calls.append((tool, args)) or {"ok": True, "result": {}})
    runner.ExistingBridge()("avanza_status", {"tenant_session_id": "demo-tenant"})
    assert calls == [("avanza_status", {"tenant_session_id": "demo-tenant"})]


def test_nonlocal_bridge_is_rejected(monkeypatch):
    from avanza_mcp.mcp import proxy
    monkeypatch.setattr(proxy, "load_mcp_session", lambda: {"url": "https://example.com", "token": "synthetic"})
    with pytest.raises(runner.Blocked, match="local"):
        runner.ExistingBridge()


def native_row(args, stop_id="OLD-STOP"):
    return {**runner.canonical_arguments(args), "stop_loss_id": stop_id, "orderbook_id": args["order_book_id"],
            "side": args["order_type"].upper(), "strategy_metadata_status": "RECORDED"}


@pytest.mark.parametrize("tool", ["avanza_stoploss_delete", "avanza_stoploss_edit"])
def test_existing_stop_actions(tmp_path, tool):
    manifest, bridge = plan(), FakeBridge()
    action = manifest["actions"][0]
    bridge.current["active_buy_stops"] = [native_row(action["arguments"])]
    action["expected_state"] = runner.state_summary(bridge.current)
    action["tool"] = tool
    action["arguments"]["stop_loss_id"] = "OLD-STOP"
    if tool.endswith("delete"):
        action["arguments"] = {key: action["arguments"][key] for key in (
            "account_id", "tenant_session_id", "stop_loss_id", "strategy_intent", "strategy_reason")}
    else:
        action["arguments"]["volume"] = 3
    code, _, bridge = execute(tmp_path, bridge, manifest, live=True)
    assert code == 0 and len(mutations(bridge)) == 1 and not bridge.authorized


def test_wrong_delete_id_is_blocked_before_submission(tmp_path):
    manifest = plan()
    action = manifest["actions"][0]
    action["tool"] = "avanza_stoploss_delete"
    action["arguments"] = {key: action["arguments"][key] for key in (
        "account_id", "tenant_session_id", "strategy_intent", "strategy_reason")}
    action["arguments"]["stop_loss_id"] = "OTHER-INSTRUMENT"
    code, _, bridge = execute(tmp_path, manifest=manifest, live=True)
    assert code == 1 and not mutations(bridge)


def test_trailing_reset_requires_explicit_acknowledgement(tmp_path):
    manifest, bridge = plan(), FakeBridge()
    action = manifest["actions"][0]
    old = native_row(action["arguments"])
    old["trigger_type"] = "FOLLOW_DOWNWARDS"
    bridge.current["active_buy_stops"] = [old]
    action["expected_state"] = runner.state_summary(bridge.current)
    action["tool"] = "avanza_stoploss_edit"
    action["arguments"]["stop_loss_id"] = "OLD-STOP"
    code, _, bridge = execute(tmp_path, bridge, manifest, live=True)
    assert code == 1 and not mutations(bridge)


def test_batch_validation_and_exact_readback():
    manifest = plan()
    action = manifest["actions"][0]
    item = {k: v for k, v in action["arguments"].items() if k not in {"account_id", "tenant_session_id"}}
    second = {**item, "volume": 3, "trigger_value": 90, "order_price": 90}
    action["tool"] = "avanza_stoploss_set_batch"
    action["arguments"] = {"account_id": "demo-account", "tenant_session_id": "demo-tenant", "items": [item, second]}
    runner.validate_plan(manifest, NOW)
    after = state()
    after["active_buy_stops"] = [native_row({**item, "account_id": "demo-account"}, "A"),
                                 native_row({**second, "account_id": "demo-account"}, "B")]
    response = {"all_ok": True, "results": [{"stop_loss_id": "A", "metadata_persisted": True},
                                             {"stop_loss_id": "B", "metadata_persisted": True}]}
    runner.verify_change(state(), after, action, response)
    response["results"][1]["stop_loss_id"] = "A"
    with pytest.raises(runner.Blocked):
        runner.verify_change(state(), after, action, response)


def test_partial_batch_never_counts_as_success():
    with pytest.raises(runner.Blocked):
        runner.check_payload({"ok": True, "result": {"all_ok": False, "completed_count": 1}})


@pytest.mark.parametrize("tool", ["avanza_order_set", "avanza_order_edit", "avanza_order_delete"])
def test_regular_order_readback(tool):
    manifest = plan()
    action = manifest["actions"][0]
    args = {"tenant_session_id": "demo-tenant", "account_id": "demo-account"}
    old_row = {"order_id": "ORDER", "account_id": "demo-account", "orderbook_id": "demo-book", "side": "BUY",
               "volume": 2, "price": 95, "valid_until": "2026-09-10"}
    before, after = state(), state()
    if tool != "avanza_order_set":
        before["open_orders"] = [old_row]
        args["order_id"] = "ORDER"
    if tool != "avanza_order_delete":
        args.update(price=94, volume=3, valid_until="2026-09-10")
        after["open_orders"] = [{**old_row, "price": 94, "volume": 3}]
    if tool == "avanza_order_set":
        args.update(order_book_id="demo-book", order_type="buy", condition="normal")
    action.update(tool=tool, arguments=args, expected_state=runner.state_summary(before))
    runner.validate_plan(manifest, NOW)
    runner.verify_change(before, after, action, {"dry_run": False})


@pytest.mark.parametrize("change", ["expired", "closed", "future_quote", "preview_quantity"])
def test_dry_run_does_not_pass_stale_or_changed_preview(tmp_path, change):
    manifest, bridge = plan(), FakeBridge()
    if change == "expired":
        manifest["expires_at"] = NOW.isoformat()
    elif change == "closed":
        manifest["actions"][0]["execution_window"]["end"] = NOW.isoformat()
    elif change == "future_quote":
        bridge.current["quote"]["timestamp"] = (NOW + timedelta(minutes=1)).isoformat()
    else:
        original = bridge
        def changed(tool, args):
            result = original(tool, args)
            if tool == "avanza_stoploss_set":
                result["result"]["request"]["stop_loss_order_event"]["volume"] += 1
            return result
        bridge = changed
    code, _, _ = execute(tmp_path, bridge, manifest)
    assert code == 1


def batch_plan():
    manifest = plan()
    action = manifest["actions"][0]
    item = {k: v for k, v in action["arguments"].items() if k not in {"account_id", "tenant_session_id"}}
    second = {**item, "volume": 3, "trigger_value": 90, "order_price": 90}
    action.update(tool="avanza_stoploss_set_batch", arguments={
        "account_id": "demo-account", "tenant_session_id": "demo-tenant", "items": [item, second]})
    return manifest


def test_reviewed_batch_preserves_parent_decision_identity():
    manifest = guarded_plan()
    manifest["actions"] = batch_plan()["actions"]
    runner.validate_plan(manifest, NOW)
    manifest["reviews"][0]["action_ids"] = ["unknown-batch"]
    with pytest.raises(runner.Blocked, match="Missing or multiply linked"):
        runner.validate_plan(manifest, NOW)


class BatchBridge(FakeBridge):
    def __init__(self, partial=False):
        super().__init__()
        self.partial = partial

    def __call__(self, tool, args):
        if tool != "avanza_stoploss_set_batch":
            return super().__call__(tool, args)
        self.calls.append((tool, copy.deepcopy(args)))
        if not args["confirm"]:
            return {"ok": True, "result": native_preview(tool, args)}
        items = args["items"][:1] if self.partial else args["items"]
        results = []
        for index, item in enumerate(items):
            stop_id = f"BATCH-{index}"
            self.current["active_buy_stops"].append(native_row({**item, "account_id": args["account_id"]}, stop_id))
            results.append({"stop_loss_id": stop_id, "metadata_persisted": True})
        return {"ok": True, "result": {"dry_run": False, "all_ok": not self.partial, "results": results}}


@pytest.mark.parametrize("partial", [False, True])
def test_batch_execution_and_partial_failure_revoke_without_retry(tmp_path, partial):
    bridge = BatchBridge(partial)
    code, _, _ = execute(tmp_path, bridge, batch_plan(), live=True)
    assert code == (1 if partial else 0)
    assert len(mutations(bridge)) == 1 and not bridge.authorized
    assert len(bridge.current["active_buy_stops"]) == (1 if partial else 2)
    fresh = BatchBridge()
    code, _, _ = execute(tmp_path, fresh, batch_plan(), live=True)
    assert code == 1 and not mutations(fresh)


def test_empty_manifest_cli_does_not_access_keychain(tmp_path, monkeypatch):
    manifest = plan()
    manifest.update(actions=[], reviews=[{"status": "REPAIR_REQUIRED"}])
    path = tmp_path / "review.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(runner, "utcnow", lambda: NOW)
    def forbidden():
        raise AssertionError("No connection is needed for an empty action list")
    monkeypatch.setattr(runner, "ExistingBridge", forbidden)
    assert runner.main([str(path), "--dry-run"]) == 2


def test_sell_capacity_preserves_core_and_never_counts_pending_buys():
    manifest = plan()
    action = manifest["actions"][0]
    action["arguments"].update(order_type="sell", strategy_intent="PROFIT_PROTECTION", volume=3)
    action["minimum_retained_antal"] = 8
    with pytest.raises(runner.Blocked, match="retained-core"):
        runner.check_sell_capacity(state(), action)
    action["minimum_retained_antal"] = 7
    runner.check_sell_capacity(state(), action)
    current = state()
    current["active_buy_stops"] = [native_row(plan()["actions"][0]["arguments"])]
    action["arguments"]["volume"] = 11
    with pytest.raises(runner.Blocked, match="currently held"):
        runner.check_sell_capacity(current, action)


def test_full_exit_requires_explicit_waiver():
    action = plan()["actions"][0]
    action["arguments"].update(order_type="sell", strategy_intent="RISK_OFF_EXIT", volume=10)
    action["minimum_retained_antal"] = 0
    with pytest.raises(runner.Blocked, match="waiver"):
        runner.check_sell_capacity(state(), action)
    action["allow_full_exit"] = True
    runner.check_sell_capacity(state(), action)


@pytest.mark.parametrize("profit", ["+8.0%", "nan%", "unknown", None])
def test_profit_harvest_cannot_use_unresolved_or_insufficient_profit(tmp_path, profit):
    p, bridge = plan(), FakeBridge()
    p["actions"][0]["minimum_current_profit_percent"] = 12
    bridge.current["position"]["Profit %"] = profit
    code, output, bridge = execute(tmp_path, bridge, p, live=True)
    assert code == 1 and "harvest floor" in " ".join(Text.from_ansi(output).plain.split())
    assert not mutations(bridge)


def test_current_profit_floor_is_checked_before_preview(tmp_path):
    p, bridge = plan(), FakeBridge()
    p["actions"][0]["minimum_current_profit_percent"] = 12
    bridge.current["position"]["Profit %"] = "+14.57%"
    code, _, bridge = execute(tmp_path, bridge, p)
    assert code == 0 and not mutations(bridge)


def test_quote_freshness_uses_readback_clock_after_slow_requests():
    p, bridge = plan(), FakeBridge()
    later = NOW + timedelta(seconds=60)
    bridge.current["quote"]["timestamp"] = later.isoformat()
    runner.preflight(bridge, p["actions"][0], p, False, NOW, clock=lambda: later)
    assert not mutations(bridge)


def test_window_expiry_during_preflight_blocks_before_submission():
    p, bridge = plan(), FakeBridge()
    with pytest.raises(runner.Blocked, match="expired during preflight"):
        runner.preflight(bridge, p["actions"][0], p, False, NOW,
                         clock=lambda: NOW + timedelta(minutes=11))
    assert not mutations(bridge)


@pytest.mark.parametrize("second_authorized", [True, False])
def test_tenant_authorization_and_cleanup_are_independent(tmp_path, second_authorized):
    first, second = FakeBridge(), FakeBridge()
    second.authorized = second_authorized
    second.current["account_id"] = "second-account"
    manifest = plan()
    action = copy.deepcopy(manifest["actions"][0])
    action["id"] = "SECOND-ONE"
    action["arguments"].update(tenant_session_id="second-tenant", account_id="second-account")
    action["expected_state"] = runner.state_summary(second.current)
    manifest["actions"].append(action)
    def routed(tool, args):
        is_second = args.get("tenant_session_id") == "second-tenant"
        bridge = second if is_second else first
        response = bridge(tool, args)
        if is_second and tool == "avanza_status":
            response["result"]["sessions"] = [{"session_id": "second-tenant", "auth_valid": True}]
        if is_second and tool == "avanza_accounts":
            response["result"][0]["ID"] = "second-account"
        return response
    code, _, _ = execute(tmp_path, routed, manifest, live=True)
    assert code == (0 if second_authorized else 1)
    assert len(mutations(first)) == int(second_authorized)
    assert len(mutations(second)) == int(second_authorized)
    if second_authorized:
        assert not first.authorized and not second.authorized


def test_review_details_are_opt_in(tmp_path):
    manifest = plan()
    manifest.update(actions=[], reviews=[{"instrument": "A review-only instrument", "next_gate": "Unique detailed gate."}])
    output = io.StringIO()
    assert runner.run(manifest, None, Console(file=output), execute=False, journal_path=tmp_path / "journal", now=lambda: NOW) == 2
    assert "Unique detailed gate" not in output.getvalue()
    assert runner.run(manifest, None, Console(file=output), execute=False, journal_path=tmp_path / "journal", now=lambda: NOW, show_reviews=True) == 2
    assert "Unique detailed gate" in output.getvalue()
