import json
import stat

import pytest

from avanza_mcp.position_strategy_registry import (
    PositionStrategyRegistry,
    build_event_protection_screen,
    build_position_strategy_live_states,
)


def live_state(
    *,
    holding: float = 10,
    active_buy_volume: float = 3,
    active_sell_volume: float = 2,
    active_buy_count: int = 1,
    active_sell_count: int = 1,
    open_buy_volume: float = 0,
    open_sell_volume: float = 0,
    open_buy_count: int = 0,
    open_sell_count: int = 0,
) -> dict:
    return {
        "account_id": "acc-1",
        "orderbook_id": "ob-1",
        "stock": "Test Corp",
        "holding": holding,
        "active_buy_volume": active_buy_volume,
        "active_sell_volume": active_sell_volume,
        "active_buy_count": active_buy_count,
        "active_sell_count": active_sell_count,
        "open_buy_volume": open_buy_volume,
        "open_sell_volume": open_sell_volume,
        "open_buy_count": open_buy_count,
        "open_sell_count": open_sell_count,
    }


def candidate(state: dict | None = None) -> dict:
    return {
        "live_state": state or live_state(),
        "instrument": "Test Corp",
        "ticker": "TEST",
        "venue": "NYSE",
        "strategy_class": "CORE_COMPOUNDER",
        "horizon": "12-36m",
        "thesis": "Reviewed durable thesis.",
        "gate": "No tight core SELL.",
        "audit_status": "VALID_CURRENT_PLAN",
        "recommendation": "Keep the reviewed holding and plan.",
        "priority": "A",
        "bucket": "CORE_RESTORATION",
        "stance": "KEEP",
        "next_gate": "Review after the next material event.",
        "proposed_correction": None,
        "source_snapshot_at": "2026-07-31T01:28:25+02:00",
    }


def test_event_protection_screen_flags_profitable_event_shock_without_authority():
    positions = [
        {
            "stock": "Fastly A",
            "orderbook_id": "956885",
            "volume": 79,
            "Day %": "-12.64%",
            "Profit %": "+7.41%",
        }
    ]
    strategy_positions = [
        {
            "account_id": "5227886",
            "orderbook_id": "956885",
            "active_sell_volume": 0,
            "active_sell_count": 0,
            "position_strategy": {
                "audit_status": "EVENT_SHOCK_REVIEW_REQUIRED",
                "bucket": "EVENT_SHOCK_PROTECTION_REVIEW",
                "gate": "Review guidance and reclaim before hold or tactical slice.",
                "recommendation": "No automatic SELL.",
                "stance": "Review",
                "next_gate": "Post-event guidance and regular-session reclaim.",
            },
        }
    ]

    result = build_event_protection_screen(positions, strategy_positions)

    assert result["authority"] == "READ_ONLY_TRIAGE"
    assert result["broker_mutation"] is False
    assert result["trade_authority"] is False
    assert result["material_move_count"] == 1
    assert result["profitable_without_sell_count"] == 1
    assert result["rows"][0]["decision_required"] is True


def test_event_protection_screen_keeps_normal_core_out_of_triage():
    positions = [
        {
            "stock": "Core Corp",
            "orderbook_id": "1",
            "volume": 10,
            "Day %": "-1.2%",
            "Profit %": "+10%",
        }
    ]
    strategy_positions = [
        {
            "account_id": "5227886",
            "orderbook_id": "1",
            "active_sell_volume": 0,
            "active_sell_count": 0,
            "position_strategy": {
                "audit_status": "VALID_CURRENT_PLAN",
                "bucket": "CORE_HOLD",
                "gate": "Review monthly fundamentals.",
                "recommendation": "Hold core.",
                "stance": "KEEP",
                "next_gate": "Monthly review.",
            },
        }
    ]

    result = build_event_protection_screen(positions, strategy_positions)

    assert result["rows"] == []


def test_event_protection_screen_surfaces_event_gated_core_material_move():
    positions = [
        {
            "stock": "Sandisk",
            "orderbook_id": "1968764",
            "volume": 4,
            "Day %": "-5.02%",
            "Profit %": "+7.67%",
        }
    ]
    strategy_positions = [
        {
            "account_id": "5227886",
            "orderbook_id": "1968764",
            "active_sell_volume": 0,
            "active_sell_count": 0,
            "position_strategy": {
                "audit_status": "VALID_CURRENT_PLAN",
                "bucket": "EVENT_GATED_CORE",
                "gate": "Review after the report and following regular-session price discovery.",
                "recommendation": "Hold the event-gated core; no automatic SELL.",
                "stance": "HOLD",
                "next_gate": "Post-report review.",
            },
        }
    ]

    result = build_event_protection_screen(positions, strategy_positions)

    assert result["material_move_count"] == 1
    assert result["profitable_without_sell_count"] == 1
    assert result["rows"][0]["event_sensitive"] is True
    assert result["rows"][0]["decision_required"] is True
    assert result["trade_authority"] is False


def test_position_registry_persists_and_detects_holding_and_order_drift(tmp_path):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    registry.register_many_existing(
        [candidate()],
        tenant_session_id="personal",
        source="unit_test",
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    reloaded = PositionStrategyRegistry(path)
    recorded = reloaded.enrich(live_state())
    assert recorded["position_strategy_status"] == "RECORDED"
    assert recorded["position_strategy"]["strategy_class"] == "CORE_COMPOUNDER"

    drifted = reloaded.enrich(
        live_state(holding=11, active_buy_volume=2, active_buy_count=2)
    )
    assert drifted["position_strategy_status"] == "STALE_MISMATCH"
    assert drifted["position_strategy_mismatches"] == [
        "holding",
        "active_buy_volume",
        "active_buy_count",
    ]


def test_intentional_holding_drift_is_acknowledged_but_remains_incomplete(tmp_path):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    reviewed = candidate(
        live_state(
            holding=10,
            active_buy_volume=0,
            active_sell_volume=0,
            active_buy_count=0,
            active_sell_count=0,
        )
    )
    reviewed["audit_exception"] = {
        "kind": "USER_CONTROLLED_ALLOCATION",
        "reason": "User controls this allocation outside stock stop logic.",
        "owner": "user",
        "review_due": "MONTHLY_OR_ON_USER_INSTRUCTION",
        "allowed_mismatches": ["holding"],
    }
    registry.register_many_existing(
        [reviewed],
        tenant_session_id="personal",
        source="unit_test",
    )

    enriched = registry.enrich(
        live_state(
            holding=11,
            active_buy_volume=0,
            active_sell_volume=0,
            active_buy_count=0,
            active_sell_count=0,
        )
    )
    assert enriched["position_strategy_status"] == "STALE_MISMATCH"
    assert enriched["position_strategy_exception_status"] == "ACKNOWLEDGED_INTENTIONAL_DRIFT"

    audit = registry.reconcile_account(
        "acc-1",
        [{"account_id": "acc-1", "orderbook_id": "ob-1", "stock": "Test Corp", "volume": 11}],
        [],
        [],
    )
    assert audit["complete"] is False
    assert audit["holding_drift_count"] == 1
    assert audit["acknowledged_mismatch_count"] == 1
    assert audit["unresolved_mismatch_count"] == 0
    assert audit["acknowledged_mismatch_orderbook_ids"] == ["ob-1"]


def test_holding_exception_never_acknowledges_order_drift(tmp_path):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    reviewed = candidate(
        live_state(
            active_buy_volume=0,
            active_sell_volume=0,
            active_buy_count=0,
            active_sell_count=0,
        )
    )
    reviewed["audit_exception"] = {
        "kind": "POST_MANUAL_EXIT_DRIFT",
        "reason": "The user-controlled exit may leave holding-only drift.",
        "owner": "user",
        "review_due": "NEXT_LIFECYCLE_REVIEW",
        "allowed_mismatches": ["holding"],
        "rebaseline_authorized": True,
    }
    registry.register_many_existing(
        [reviewed],
        tenant_session_id="darkcell",
        source="unit_test",
    )

    enriched = registry.enrich(
        live_state(
            holding=11,
            active_buy_volume=1,
            active_buy_count=1,
            active_sell_volume=0,
            active_sell_count=0,
        )
    )
    assert enriched["position_strategy_exception_status"] is None
    assert enriched["position_strategy"]["audit_exception"]["rebaseline_authorized"] is False

    audit = registry.reconcile_account(
        "acc-1",
        [{"account_id": "acc-1", "orderbook_id": "ob-1", "stock": "Test Corp", "volume": 11}],
        [
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-1",
                "stock": "Test Corp",
                "status": "ACTIVE",
                "side": "BUY",
                "volume": 1,
            }
        ],
        [],
    )
    assert audit["complete"] is False
    assert audit["acknowledged_mismatch_count"] == 0
    assert audit["unresolved_mismatch_count"] == 1
    assert audit["unresolved_mismatch_orderbook_ids"] == ["ob-1"]


def test_live_state_union_catches_orders_without_a_position():
    states = build_position_strategy_live_states(
        "acc-1",
        [
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-held",
                "stock": "Held",
                "volume": 5,
            }
        ],
        [
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-recovery",
                "stock": "Recovery",
                "status": "ACTIVE",
                "side": "BUY",
                "volume": 2,
            }
        ],
        [
            {
                "account_id": "acc-1",
                "order_book_id": "ob-open",
                "stock": "Open",
                "status": "OPEN",
                "side": "SELL",
                "Volume": 3,
            }
        ],
    )

    by_id = {row["orderbook_id"]: row for row in states}
    assert by_id["ob-held"]["holding"] == 5
    assert by_id["ob-recovery"]["holding"] == 0
    assert by_id["ob-recovery"]["active_buy_volume"] == 2
    assert by_id["ob-open"]["open_sell_volume"] == 3


def test_position_registry_batch_is_atomic(tmp_path):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    bad = candidate({**live_state(), "orderbook_id": "ob-2"})
    bad["priority"] = "Z"
    with pytest.raises(ValueError, match="priority"):
        registry.register_many_existing(
            [candidate(), bad],
            tenant_session_id="personal",
            source="unit_test",
        )

    assert registry.health()["entry_count"] == 0
    assert not path.exists()


def test_reconcile_is_fail_closed_and_prunes_only_explicit_stale_plan(tmp_path):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    stale = candidate({**live_state(), "orderbook_id": "ob-stale"})
    registry.register_many_existing(
        [candidate(), stale],
        tenant_session_id="personal",
        source="unit_test",
    )
    positions = [
        {
            "account_id": "acc-1",
            "orderbook_id": "ob-1",
            "stock": "Test Corp",
            "volume": 10,
        }
    ]
    stoplosses = [
        {
            "account_id": "acc-1",
            "orderbook_id": "ob-1",
            "stock": "Test Corp",
            "status": "ACTIVE",
            "side": "BUY",
            "volume": 3,
        },
        {
            "account_id": "acc-1",
            "orderbook_id": "ob-1",
            "stock": "Test Corp",
            "status": "ACTIVE",
            "side": "SELL",
            "volume": 2,
        },
    ]

    before = registry.reconcile_account("acc-1", positions, stoplosses, [])
    assert before["complete"] is False
    assert before["stale_plan_orderbook_ids"] == ["ob-stale"]

    after = registry.reconcile_account(
        "acc-1",
        positions,
        stoplosses,
        [],
        prune_stale=True,
    )
    assert after["complete"] is True
    assert after["pruned_orderbook_ids"] == ["ob-stale"]
    assert registry.lookup("acc-1", "ob-stale") is None


def test_corrupt_position_registry_remains_read_only(tmp_path):
    path = tmp_path / "position-strategies.json"
    path.write_text("{not-json", encoding="utf-8")
    registry = PositionStrategyRegistry(path)

    assert registry.health()["available"] is False
    audit = registry.reconcile_account(
        "acc-1",
        [
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-1",
                "stock": "Test Corp",
                "volume": 10,
            }
        ],
        [],
        [],
    )
    assert audit["complete"] is False
    assert audit["registry_unavailable_count"] == 1
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        registry.register_many_existing(
            [candidate()],
            tenant_session_id="personal",
            source="unit_test",
        )
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_position_registry_json_is_versioned_and_account_scoped(tmp_path):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    registry.register_many_existing(
        [candidate()],
        tenant_session_id="personal",
        source="unit_test",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert set(payload["accounts"]) == {"acc-1"}
    assert set(payload["accounts"]["acc-1"]["positions"]) == {"ob-1"}


def test_position_registry_preserves_unavailable_non_equity_identity_fields(tmp_path):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    non_equity = candidate()
    non_equity["ticker"] = None
    non_equity["venue"] = None

    registry.register_many_existing(
        [non_equity],
        tenant_session_id="personal",
        source="unit_test",
    )

    recorded = registry.enrich(live_state())
    assert recorded["position_strategy_status"] == "RECORDED"
    assert recorded["position_strategy"]["ticker"] is None
    assert recorded["position_strategy"]["venue"] is None
