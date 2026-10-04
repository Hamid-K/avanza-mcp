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


def no_stop_exception_evidence(
    protection_choice: str = "DELIBERATELY_UNPROTECTED_CORE",
) -> dict:
    return {
        "decision_at": "2026-01-02T00:00:00+00:00",
        "evidence_as_of": "2026-01-01T00:00:00+00:00",
        "next_review_at": "2099-01-01T00:00:00+00:00",
        "valid_until": "2099-12-31T00:00:00+00:00",
        "gap_risk_statement": (
            "The reviewed core remains exposed to downside without a broker SELL row."
        ),
        "evidence_source_ids": ["unit-test-position-review"],
        "protection_choice": protection_choice,
    }


def non_stop_eligible_evidence() -> dict:
    return {
        "evidence_as_of": "2026-01-01T00:00:00+00:00",
        "valid_until": "2099-12-31T00:00:00+00:00",
        "capability_statement": (
            "The dated broker capability review confirms that this instrument "
            "cannot receive a stop-loss row."
        ),
        "evidence_source_ids": ["unit-test-broker-capability-review"],
    }


def candidate(state: dict | None = None) -> dict:
    reviewed_state = state or live_state()
    has_active_sell = (
        reviewed_state.get("active_sell_volume", 0) > 0
        and reviewed_state.get("active_sell_count", 0) > 0
    )
    reviewed = {
        "live_state": reviewed_state,
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
        "protection_classification": (
            "CALIBRATED_STOP_PROFIT_LADDER"
            if has_active_sell
            else "CORE_HOLD_EXCEPTION"
        ),
        "protection_reason": (
            "The reviewed tactical SELL row is active and metadata-controlled."
            if has_active_sell
            else "The intact reviewed core deliberately has no mechanical SELL stop."
        ),
        "proposed_correction": None,
        "source_snapshot_at": "2026-07-31T01:28:25+02:00",
    }
    if has_active_sell:
        reviewed["protection_target_antal"] = reviewed_state[
            "active_sell_volume"
        ]
        reviewed["retained_core_antal"] = (
            reviewed_state["holding"] - reviewed_state["active_sell_volume"]
        )
    else:
        reviewed["no_stop_exception_evidence"] = no_stop_exception_evidence()
    return reviewed


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


def test_position_registry_requires_explicit_protection_metadata(tmp_path):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    reviewed = candidate()
    del reviewed["protection_classification"]

    with pytest.raises(ValueError, match="protection_classification is required"):
        registry.register_many_existing(
            [reviewed],
            tenant_session_id="personal",
            source="unit_test",
        )


@pytest.mark.parametrize(
    ("classification", "state", "message"),
    [
        (
            "CALIBRATED_STOP_PROFIT_LADDER",
            live_state(active_sell_volume=0, active_sell_count=0),
            "requires active SELL volume and count",
        ),
        (
            "CORE_HOLD_EXCEPTION",
            live_state(),
            "cannot coexist with an active SELL stop",
        ),
        (
            "MARKER_EXCEPTION",
            live_state(
                holding=2,
                active_sell_volume=0,
                active_sell_count=0,
            ),
            "requires live holding at or below one",
        ),
    ],
)
def test_position_registry_rejects_protection_contradictions(
    tmp_path,
    classification,
    state,
    message,
):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    reviewed = candidate(state)
    reviewed["protection_classification"] = classification
    if classification == "CALIBRATED_STOP_PROFIT_LADDER":
        reviewed.pop("no_stop_exception_evidence", None)
        reviewed["protection_target_antal"] = 2
        reviewed["retained_core_antal"] = 8
    elif classification in {"CORE_HOLD_EXCEPTION", "MARKER_EXCEPTION"}:
        reviewed.pop("protection_target_antal", None)
        reviewed.pop("retained_core_antal", None)
        if classification == "MARKER_EXCEPTION":
            reviewed.pop("no_stop_exception_evidence", None)

    with pytest.raises(ValueError, match=message):
        registry.register_many_existing(
            [reviewed],
            tenant_session_id="personal",
            source="unit_test",
        )


def test_repair_required_is_recorded_but_blocks_governance_completion(tmp_path):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    state = live_state(
        active_buy_volume=0,
        active_buy_count=0,
        active_sell_volume=0,
        active_sell_count=0,
    )
    reviewed = candidate(state)
    reviewed["protection_classification"] = "REPAIR_REQUIRED"
    reviewed["protection_reason"] = "The live relative BUY child lacks a fixed cap."
    reviewed.pop("no_stop_exception_evidence", None)
    registry.register_many_existing(
        [reviewed],
        tenant_session_id="personal",
        source="unit_test",
    )

    audit = registry.reconcile_account(
        "acc-1",
        [{"account_id": "acc-1", "orderbook_id": "ob-1", "volume": 10}],
        [],
        [],
    )

    assert audit["strict_fingerprint_complete"] is True
    assert audit["protection_complete"] is False
    assert audit["governance_complete"] is False
    assert audit["complete"] is False
    assert audit["protection_repair_required_count"] == 1
    assert audit["protection_repair_required_orderbook_ids"] == ["ob-1"]


def test_material_no_stop_exception_is_valid_but_broker_unprotected(tmp_path):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    state = live_state(
        active_buy_volume=0,
        active_sell_volume=0,
        active_buy_count=0,
        active_sell_count=0,
    )
    registry.register_many_existing(
        [candidate(state)],
        tenant_session_id="personal",
        source="unit_test",
    )

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

    assert audit["protection_classification_complete"] is True
    assert audit["positions"][0]["position_protection_status"] == "VALID"
    assert audit["broker_sell_protected"] is False
    assert audit["broker_protection_review_required"] is True
    assert audit["protection_review_required"] is True
    assert audit["protection_complete"] is False
    assert audit["complete"] is False
    assert audit["governance_complete"] is False
    assert audit["zero_sell_material_position_count"] == 1
    assert audit["zero_sell_material_positions"] == [
        {
            "account_id": "acc-1",
            "orderbook_id": "ob-1",
            "stock": "Test Corp",
            "held_antal": 10.0,
            "active_sell_row_count": 0,
            "active_sell_antal": 0.0,
            "protection_classification": "CORE_HOLD_EXCEPTION",
            "position_protection_status": "VALID",
            "no_stop_exception_evidence_status": "CURRENT",
            "no_stop_exception_evidence_complete": True,
            "no_stop_exception_evidence_issues": [],
            "no_stop_exception_decision_current": True,
            "no_stop_exception_protection_choice": (
                "DELIBERATELY_UNPROTECTED_CORE"
            ),
            "no_stop_exception_protection_action_required": False,
            "no_stop_exception_evidence": no_stop_exception_evidence(),
        }
    ]
    assert audit["no_stop_exception_evidence_incomplete_count"] == 0
    metrics = audit["broker_sell_protection"]
    assert metrics["assessment_status"] == "ASSESSED"
    assert metrics["coverage_status"] == "BROKER_SELL_PROTECTION_ABSENT"
    assert metrics["review_status"] == "PROTECTION_REVIEW_REQUIRED"
    assert metrics["eligible_position_count"] == 1
    assert metrics["covered_position_count"] == 0
    assert metrics["material_stop_eligible_position_count"] == 1
    assert metrics["positions_with_active_sell_count"] == 0
    assert metrics["active_sell_row_count"] == 0
    assert metrics["position_coverage_percent"] == 0.0
    assert metrics["zero_sell_material_positions"] == [
        {"orderbook_id": "ob-1", "holding_antal": 10.0}
    ]
    assert metrics["per_instrument"] == [
        {
            "orderbook_id": "ob-1",
            "holding_antal": 10.0,
            "active_sell_row_count": 0,
            "active_sell_antal": 0.0,
            "protected_antal": 0.0,
            "protection_target_antal": None,
            "retained_core_antal": None,
            "strategy_target_coverage_status": "NOT_APPLICABLE",
            "no_stop_exception_evidence_status": "CURRENT",
            "no_stop_exception_decision_current": True,
            "no_stop_exception_protection_choice": (
                "DELIBERATELY_UNPROTECTED_CORE"
            ),
            "no_stop_exception_protection_action_required": False,
            "no_stop_exception_evidence": no_stop_exception_evidence(),
            "active_sell_rows": [],
        }
    ]
    assert metrics["cross_instrument_antal_aggregated"] is False


def test_partial_two_sell_rows_preserve_exact_antal_without_cross_sum(tmp_path):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    state = live_state(active_sell_volume=5, active_sell_count=2)
    registry.register_many_existing(
        [candidate(state)],
        tenant_session_id="personal",
        source="unit_test",
    )

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
        [
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
                "stop_loss_id": "sl-two",
                "volume": 2,
            },
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-1",
                "stock": "Test Corp",
                "status": "ACTIVE",
                "side": "SELL",
                "stop_loss_id": "sl-three",
                "volume": 3,
            },
        ],
        [],
    )

    assert audit["protection_classification_complete"] is True
    assert audit["broker_sell_protected"] is True
    assert audit["broker_sell_presence_complete"] is True
    assert audit["broker_sell_protection_complete"] is False
    assert audit["protection_complete"] is True
    assert audit["complete"] is True
    assert audit["zero_sell_material_positions"] == []
    metrics = audit["broker_sell_protection"]
    assert metrics["assessment_status"] == "ASSESSED"
    assert metrics["coverage_status"] == "BROKER_SELL_PROTECTION_PARTIAL"
    assert metrics["review_status"] == "CURRENT"
    assert metrics["eligible_position_count"] == 1
    assert metrics["covered_position_count"] == 1
    assert metrics["positions_with_active_sell_count"] == 1
    assert metrics["fully_protected_position_count"] == 0
    assert metrics["active_sell_position_count"] == 1
    assert metrics["active_sell_row_count"] == 2
    assert "active_sell_antal" not in metrics
    assert metrics["active_sell_positions"] == [
        {
            "account_id": "acc-1",
            "orderbook_id": "ob-1",
            "stock": "Test Corp",
            "held_antal": 10.0,
                "active_sell_row_count": 2,
                "active_sell_antal": 5.0,
                "protection_target_antal": 5.0,
                "retained_core_antal": 5.0,
                "strategy_target_coverage_status": "MATCHED",
                "active_sell_rows": [
                {"stop_loss_id": "sl-three", "antal": 3.0},
                {"stop_loss_id": "sl-two", "antal": 2.0},
            ],
        }
    ]
    assert metrics["position_coverage_percent"] == 100.0
    assert metrics["zero_sell_material_positions"] == []
    assert metrics["per_instrument"] == [
        {
            "orderbook_id": "ob-1",
            "holding_antal": 10.0,
            "active_sell_row_count": 2,
            "active_sell_antal": 5.0,
            "protected_antal": 5.0,
            "protection_target_antal": 5.0,
            "retained_core_antal": 5.0,
            "strategy_target_coverage_status": "MATCHED",
            "no_stop_exception_evidence_status": "NOT_APPLICABLE",
            "no_stop_exception_decision_current": False,
            "no_stop_exception_protection_choice": None,
            "no_stop_exception_protection_action_required": None,
            "no_stop_exception_evidence": None,
            "active_sell_rows": [
                {"stop_loss_id": "sl-three", "antal": 3.0},
                {"stop_loss_id": "sl-two", "antal": 2.0},
            ],
        }
    ]
    assert metrics["material_positions"] == [
        {
            "account_id": "acc-1",
            "orderbook_id": "ob-1",
            "stock": "Test Corp",
            "status": "PARTIAL",
            "held_antal": 10.0,
            "active_sell_row_count": 2,
            "active_sell_antal": 5.0,
            "protected_antal": 5.0,
            "unprotected_antal": 5.0,
            "protection_target_antal": 5.0,
            "retained_core_antal": 5.0,
            "strategy_target_coverage_status": "MATCHED",
            "active_sell_rows": [
                {"stop_loss_id": "sl-three", "antal": 3.0},
                {"stop_loss_id": "sl-two", "antal": 2.0},
            ],
        }
    ]
    assert "active_sell_rows" not in audit["positions"][0][
        "recorded_live_state"
    ]


def test_canonical_broker_statuses_cover_full_and_not_applicable(tmp_path):
    full_registry = PositionStrategyRegistry(tmp_path / "full.json")
    full_state = live_state(
        active_buy_volume=0,
        active_sell_volume=10,
        active_buy_count=0,
        active_sell_count=1,
    )
    full_registry.register_many_existing(
        [candidate(full_state)],
        tenant_session_id="personal",
        source="unit_test",
    )
    full_audit = full_registry.reconcile_account(
        "acc-1",
        [
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-1",
                "stock": "Test Corp",
                "volume": 10,
            }
        ],
        [
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-1",
                "stock": "Test Corp",
                "status": "ACTIVE",
                "side": "SELL",
                "volume": 10,
            }
        ],
        [],
    )

    assert full_audit["broker_sell_protection_complete"] is True
    assert full_audit["broker_sell_protection"]["coverage_status"] == (
        "BROKER_SELL_PROTECTION_PRESENT"
    )

    marker_registry = PositionStrategyRegistry(tmp_path / "marker.json")
    marker_state = live_state(
        holding=1,
        active_buy_volume=0,
        active_sell_volume=0,
        active_buy_count=0,
        active_sell_count=0,
    )
    marker = candidate(marker_state)
    marker["protection_classification"] = "MARKER_EXCEPTION"
    marker["protection_reason"] = "One-unit marker is outside material scope."
    marker.pop("no_stop_exception_evidence", None)
    marker_registry.register_many_existing(
        [marker],
        tenant_session_id="personal",
        source="unit_test",
    )
    marker_audit = marker_registry.reconcile_account(
        "acc-1",
        [
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-1",
                "stock": "Test Corp",
                "volume": 1,
            }
        ],
        [],
        [],
    )

    marker_metrics = marker_audit["broker_sell_protection"]
    assert marker_metrics["coverage_status"] == "NOT_APPLICABLE"
    assert marker_metrics["review_status"] == "CURRENT"
    assert marker_metrics["eligible_position_count"] == 0
    assert marker_metrics["per_instrument"] == []


def test_h1_and_non_stop_rows_are_excluded_but_named_hgt1_is_not(tmp_path):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")

    marker_state = {
        **live_state(
            holding=1,
            active_buy_volume=0,
            active_sell_volume=0,
            active_buy_count=0,
            active_sell_count=0,
        ),
        "orderbook_id": "ob-marker",
        "stock": "Marker Corp",
    }
    marker = candidate(marker_state)
    marker["protection_classification"] = "MARKER_EXCEPTION"
    marker["protection_reason"] = "One-unit marker is outside material scope."
    marker.pop("no_stop_exception_evidence", None)

    fund_state = {
        **marker_state,
        "orderbook_id": "ob-fund",
        "stock": "Fund Corp",
        "holding": 100,
    }
    fund = candidate(fund_state)
    fund["protection_classification"] = "NON_STOP_ELIGIBLE"
    fund["protection_reason"] = "Broker does not support stock stops here."
    fund.pop("no_stop_exception_evidence", None)
    fund["non_stop_eligible_evidence"] = non_stop_eligible_evidence()

    named_state = {
        **marker_state,
        "orderbook_id": "ob-named",
        "stock": "Named Corp",
        "holding": 36,
    }
    named = candidate(named_state)
    named["protection_classification"] = "NAMED_EXCEPTION"
    named["protection_reason"] = "Named review does not create broker protection."

    registry.register_many_existing(
        [marker, fund, named],
        tenant_session_id="personal",
        source="unit_test",
    )
    audit = registry.reconcile_account(
        "acc-1",
        [
            {
                "account_id": "acc-1",
                "orderbook_id": state["orderbook_id"],
                "stock": state["stock"],
                "volume": state["holding"],
            }
            for state in (marker_state, fund_state, named_state)
        ],
        [],
        [],
    )

    by_id = {row["orderbook_id"]: row for row in audit["positions"]}
    assert by_id["ob-marker"]["broker_sell_protection"]["status"] == "EXCLUDED"
    assert by_id["ob-fund"]["broker_sell_protection"]["status"] == "EXCLUDED"
    assert by_id["ob-named"]["position_protection_status"] == "VALID"
    assert by_id["ob-named"]["broker_sell_protection"]["status"] == "MISSING"
    assert audit["broker_sell_protection"][
        "material_stop_eligible_position_count"
    ] == 1
    assert audit["zero_sell_material_position_count"] == 1
    assert audit["zero_sell_material_positions"][0]["orderbook_id"] == "ob-named"
    assert audit["broker_sell_protected"] is False


def test_no_stop_free_text_cannot_satisfy_typed_evidence_without_schema(
    tmp_path,
):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    state = live_state(
        holding=4,
        active_buy_volume=0,
        active_sell_volume=0,
        active_buy_count=0,
        active_sell_count=0,
    )
    reviewed = candidate(state)
    registry.register_many_existing(
        [reviewed],
        tenant_session_id="personal",
        source="unit_test",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["accounts"]["acc-1"]["positions"]["ob-1"].pop(
        "no_stop_exception_evidence"
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    registry = PositionStrategyRegistry(path)

    audit = registry.reconcile_account(
        "acc-1",
        [
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-1",
                "stock": "Test Corp",
                "volume": 4,
            }
        ],
        [],
        [],
    )

    assert audit["positions"][0]["position_protection_status"] == "MISSING"
    assert audit["protection_classification_complete"] is False
    assert audit["no_stop_exception_evidence_incomplete_count"] == 1
    assert audit["no_stop_exception_evidence_incomplete_orderbook_ids"] == [
        "ob-1"
    ]
    assert audit["zero_sell_material_positions"][0][
        "no_stop_exception_evidence_status"
    ] == "MISSING"
    assert audit["zero_sell_material_positions"][0][
        "no_stop_exception_evidence_issues"
    ] == [
        "decision_at",
        "evidence_as_of",
        "next_review_at",
        "valid_until",
        "gap_risk_statement",
        "evidence_source_ids",
        "protection_choice",
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
    assert audit["protection_classification_complete"] is True
    assert audit["protection_classification_governance_complete"] is True
    assert audit["protection_complete"] is False
    assert audit["broker_sell_protected"] is False
    assert audit["governance_complete"] is False


def test_exception_preserving_semantic_update_keeps_reviewed_fingerprint(tmp_path):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    reviewed_state = live_state(
        holding=9,
        active_buy_volume=7,
        active_sell_volume=0,
        active_buy_count=1,
        active_sell_count=0,
    )
    audit_exception = {
        "kind": "POST_MANUAL_EXIT_DRIFT",
        "reason": "User manually sold eight shares.",
        "owner": "user",
        "review_due": "SESSION_3_AND_SESSION_5_LIFECYCLE_REVIEW",
        "allowed_mismatches": ["holding"],
        "rebaseline_authorized": False,
    }
    reviewed = candidate(reviewed_state)
    reviewed["audit_exception"] = audit_exception
    registry.register_many_existing(
        [reviewed],
        tenant_session_id="darkcell",
        source="unit_test",
    )

    live_after_exit = live_state(
        holding=1,
        active_buy_volume=7,
        active_sell_volume=0,
        active_buy_count=1,
        active_sell_count=0,
    )
    update = candidate(live_after_exit)
    update.update(
        {
            "audit_status": "POST_MANUAL_EXIT_EXTENDED_RESIDUAL_ONLY",
            "bucket": "POST_EVENT_LOCKED_DEEP_RESIDUAL",
            "stance": "Retain the one-share marker and locked deep residual.",
            "next_gate": "Review only after a regular-session reversal.",
            "protection_classification": "MARKER_EXCEPTION",
            "protection_reason": (
                "The live post-exit exposure is one marker share without a SELL stop."
            ),
            "audit_exception": audit_exception,
            "preserve_audit_exception_fingerprint": True,
        }
    )
    update.pop("no_stop_exception_evidence", None)

    before_preview = path.read_bytes()
    preview = registry.preview_many_existing(
        [update],
        tenant_session_id="darkcell",
        source="unit_test",
    )
    assert path.read_bytes() == before_preview
    assert preview[0]["holding"] == 9
    assert preview[0]["audit_status"] == "POST_MANUAL_EXIT_EXTENDED_RESIDUAL_ONLY"
    assert preview[0]["audit_exception"] == audit_exception

    registry.register_many_existing(
        [update],
        tenant_session_id="darkcell",
        source="unit_test",
    )
    enriched = registry.enrich(live_after_exit)
    assert enriched["position_strategy_status"] == "STALE_MISMATCH"
    assert enriched["position_strategy_mismatches"] == ["holding"]
    assert (
        enriched["position_strategy_exception_status"]
        == "ACKNOWLEDGED_INTENTIONAL_DRIFT"
    )
    assert enriched["recorded_live_state"]["holding"] == 9
    assert (
        enriched["position_strategy"]["audit_status"]
        == "POST_MANUAL_EXIT_EXTENDED_RESIDUAL_ONLY"
    )
    assert (
        enriched["position_strategy"]["protection_classification"]
        == "MARKER_EXCEPTION"
    )
    assert enriched["position_protection_status"] == "VALID"
    assert enriched["position_strategy"]["audit_exception"] == audit_exception

    reloaded = PositionStrategyRegistry(path)
    persisted = reloaded.enrich(live_after_exit)
    assert persisted["recorded_live_state"]["holding"] == 9
    assert (
        persisted["position_strategy"]["audit_status"]
        == "POST_MANUAL_EXIT_EXTENDED_RESIDUAL_ONLY"
    )


def test_exception_preserving_update_refuses_missing_or_changed_exception(tmp_path):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    registry.register_many_existing(
        [candidate(live_state(holding=9))],
        tenant_session_id="darkcell",
        source="unit_test",
    )
    update = candidate(live_state(holding=1))
    update["preserve_audit_exception_fingerprint"] = True
    with pytest.raises(ValueError, match="holding-only audit_exception"):
        registry.preview_many_existing(
            [update],
            tenant_session_id="darkcell",
            source="unit_test",
        )

    path = tmp_path / "reviewed-position-strategies.json"
    reviewed_registry = PositionStrategyRegistry(path)
    reviewed = candidate(live_state(holding=9))
    reviewed["audit_exception"] = {
        "kind": "POST_MANUAL_EXIT_DRIFT",
        "reason": "Original reviewed reason.",
        "owner": "user",
        "review_due": "NEXT_LIFECYCLE_REVIEW",
        "allowed_mismatches": ["holding"],
    }
    reviewed_registry.register_many_existing(
        [reviewed],
        tenant_session_id="darkcell",
        source="unit_test",
    )
    changed = candidate(live_state(holding=1))
    changed["audit_exception"] = {
        **reviewed["audit_exception"],
        "reason": "Changed reason is not allowed.",
    }
    changed["preserve_audit_exception_fingerprint"] = True
    with pytest.raises(ValueError, match="cannot change"):
        reviewed_registry.preview_many_existing(
            [changed],
            tenant_session_id="darkcell",
            source="unit_test",
        )


def test_exception_preserving_update_refuses_non_holding_drift(tmp_path):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    reviewed = candidate(
        live_state(
            holding=9,
            active_buy_volume=7,
            active_buy_count=1,
        )
    )
    reviewed["audit_exception"] = {
        "kind": "POST_MANUAL_EXIT_DRIFT",
        "reason": "User controls the holding change.",
        "owner": "user",
        "review_due": "NEXT_LIFECYCLE_REVIEW",
        "allowed_mismatches": ["holding"],
    }
    registry.register_many_existing(
        [reviewed],
        tenant_session_id="darkcell",
        source="unit_test",
    )

    update = candidate(
        live_state(
            holding=1,
            active_buy_volume=8,
            active_buy_count=1,
        )
    )
    update["preserve_audit_exception_fingerprint"] = True
    with pytest.raises(ValueError, match="holding-only live drift"):
        registry.preview_many_existing(
            [update],
            tenant_session_id="darkcell",
            source="unit_test",
        )

    unchanged = candidate(
        live_state(
            holding=9,
            active_buy_volume=7,
            active_buy_count=1,
        )
    )
    unchanged["preserve_audit_exception_fingerprint"] = True
    with pytest.raises(ValueError, match="found: none"):
        registry.preview_many_existing(
            [unchanged],
            tenant_session_id="darkcell",
            source="unit_test",
        )


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


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda evidence: evidence.update(
                {"protection_choice": "UNREVIEWED_CHOICE"}
            ),
            "protection_choice must be",
        ),
        (
            lambda evidence: evidence.update(
                {"evidence_as_of": "2026-01-03T00:00:00+00:00"}
            ),
            "evidence_as_of must be at or before decision_at",
        ),
        (
            lambda evidence: evidence.update(
                {
                    "decision_at": "2099-01-02T00:00:00+00:00",
                    "evidence_as_of": "2099-01-01T00:00:00+00:00",
                    "next_review_at": "2099-02-01T00:00:00+00:00",
                }
            ),
            "must not be future-dated",
        ),
        (
            lambda evidence: evidence.update(
                {
                    "next_review_at": "2026-01-03T00:00:00+00:00",
                    "valid_until": "2099-12-31T00:00:00+00:00",
                }
            ),
            "next_review_at has elapsed",
        ),
        (
            lambda evidence: evidence.update(
                {"unexpected_field": "not allowed"}
            ),
            "unexpected field: unexpected_field",
        ),
        (
            lambda evidence: evidence.update(
                {"decision_at": "2026-01-02T00:00:00"}
            ),
            "timezone-aware ISO timestamp",
        ),
        (
            lambda evidence: evidence.update({"decision_at": 20260102}),
            "timezone-aware ISO timestamp",
        ),
        (
            lambda evidence: evidence.update({"gap_risk_statement": 123}),
            "gap_risk_statement must be nonblank",
        ),
        (
            lambda evidence: evidence.update({"evidence_source_ids": [123]}),
            "evidence_source_ids must be a nonempty unique string array",
        ),
    ],
)
def test_no_stop_evidence_rejects_invalid_or_noncurrent_payloads(
    tmp_path,
    mutate,
    message,
):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    state = live_state(
        active_buy_volume=0,
        active_sell_volume=0,
        active_buy_count=0,
        active_sell_count=0,
    )
    reviewed = candidate(state)
    mutate(reviewed["no_stop_exception_evidence"])

    with pytest.raises(ValueError, match=message):
        registry.register_many_existing(
            [reviewed],
            tenant_session_id="personal",
            source="unit_test",
        )


@pytest.mark.parametrize(
    "protection_choice",
    ["TACTICAL_PROFIT_SLICE", "WIDER_CALIBRATED_CORE_ROW"],
)
def test_protective_no_stop_choice_requires_broker_action_and_stays_incomplete(
    tmp_path,
    protection_choice,
):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    state = live_state(
        active_buy_volume=0,
        active_sell_volume=0,
        active_buy_count=0,
        active_sell_count=0,
    )
    reviewed = candidate(state)
    reviewed["no_stop_exception_evidence"] = no_stop_exception_evidence(
        protection_choice
    )
    registry.register_many_existing(
        [reviewed],
        tenant_session_id="personal",
        source="unit_test",
    )

    audit = registry.reconcile_account(
        "acc-1",
        [{"account_id": "acc-1", "orderbook_id": "ob-1", "volume": 10}],
        [],
        [],
    )

    assert audit["positions"][0]["position_protection_status"] == (
        "REPAIR_REQUIRED"
    )
    assert audit["zero_sell_material_positions"][0][
        "no_stop_exception_evidence_status"
    ] == "CURRENT"
    assert audit["zero_sell_material_positions"][0][
        "no_stop_exception_protection_action_required"
    ] is True
    assert audit["broker_sell_protection"]["covered_position_count"] == 0
    assert audit["protection_complete"] is False
    assert audit["governance_review_eligible"] is False


def test_calibrated_target_round_trip_and_live_under_overcoverage(tmp_path):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    state = live_state(
        active_buy_volume=0,
        active_buy_count=0,
        active_sell_volume=5,
        active_sell_count=1,
    )
    reviewed = candidate(state)

    preview = registry.preview_many_existing(
        [reviewed],
        tenant_session_id="personal",
        source="unit_test",
    )
    assert not path.exists()
    assert preview[0]["protection_target_antal"] == 5
    assert preview[0]["retained_core_antal"] == 5

    registry.register_many_existing(
        [reviewed],
        tenant_session_id="personal",
        source="unit_test",
    )
    restarted = PositionStrategyRegistry(path)
    persisted = restarted.enrich(state)["position_strategy"]
    assert persisted["protection_target_antal"] == 5
    assert persisted["retained_core_antal"] == 5

    def audit_at(active_antal: int) -> dict:
        return restarted.reconcile_account(
            "acc-1",
            [{"account_id": "acc-1", "orderbook_id": "ob-1", "volume": 10}],
            [
                {
                    "account_id": "acc-1",
                    "orderbook_id": "ob-1",
                    "status": "ACTIVE",
                    "side": "SELL",
                    "stop_loss_id": f"sell-{active_antal}",
                    "volume": active_antal,
                }
            ],
            [],
        )

    under = audit_at(3)
    assert under["strategy_target_coverage_mismatch_orderbook_ids"] == [
        "ob-1"
    ]
    assert under["broker_sell_protection"]["per_instrument"][0][
        "strategy_target_coverage_status"
    ] == "UNDERCOVERED"
    assert under["broker_sell_protection"]["review_status"] == (
        "PROTECTION_REVIEW_REQUIRED"
    )
    assert under["complete"] is False

    over = audit_at(7)
    assert over["broker_sell_protection"]["per_instrument"][0][
        "strategy_target_coverage_status"
    ] == "OVERCOVERED"
    assert over["complete"] is False


def test_expired_non_stop_evidence_no_longer_excludes_position(tmp_path):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    state = live_state(
        holding=100,
        active_buy_volume=0,
        active_sell_volume=0,
        active_buy_count=0,
        active_sell_count=0,
    )
    reviewed = candidate(state)
    reviewed.pop("no_stop_exception_evidence")
    reviewed["protection_classification"] = "NON_STOP_ELIGIBLE"
    reviewed["protection_reason"] = "Stops are unsupported for this instrument."
    reviewed["non_stop_eligible_evidence"] = non_stop_eligible_evidence()
    registry.register_many_existing(
        [reviewed],
        tenant_session_id="personal",
        source="unit_test",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    evidence = payload["accounts"]["acc-1"]["positions"]["ob-1"][
        "non_stop_eligible_evidence"
    ]
    evidence["valid_until"] = "2026-01-02T00:00:00+00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")

    audit = PositionStrategyRegistry(path).reconcile_account(
        "acc-1",
        [{"account_id": "acc-1", "orderbook_id": "ob-1", "volume": 100}],
        [],
        [],
    )
    row = audit["positions"][0]
    assert row["position_protection_status"] == "INVALID"
    assert row["broker_sell_protection"]["non_stop_eligible_verified"] is False
    assert row["broker_sell_protection"]["status"] == "MISSING"
    assert audit["zero_sell_material_position_count"] == 1
    assert audit["broker_sell_protected"] is False
    assert audit["complete"] is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "evidence_as_of",
            20260101,
            "evidence_as_of must be a timezone-aware ISO timestamp",
        ),
        ("capability_statement", 123, "capability_statement must be nonblank"),
        (
            "evidence_source_ids",
            [123],
            "evidence_source_ids must be a nonempty unique string array",
        ),
    ],
)
def test_non_stop_capability_evidence_rejects_non_string_values(
    tmp_path,
    field,
    value,
    message,
):
    registry = PositionStrategyRegistry(tmp_path / "position-strategies.json")
    state = live_state(
        holding=100,
        active_buy_volume=0,
        active_sell_volume=0,
        active_buy_count=0,
        active_sell_count=0,
    )
    reviewed = candidate(state)
    reviewed.pop("no_stop_exception_evidence")
    reviewed["protection_classification"] = "NON_STOP_ELIGIBLE"
    reviewed["protection_reason"] = "Stops are unsupported for this instrument."
    reviewed["non_stop_eligible_evidence"] = non_stop_eligible_evidence()
    reviewed["non_stop_eligible_evidence"][field] = value

    with pytest.raises(ValueError, match=message):
        registry.register_many_existing(
            [reviewed],
            tenant_session_id="personal",
            source="unit_test",
        )


def test_legacy_named_exception_with_token_sell_cannot_fabricate_completion(
    tmp_path,
):
    path = tmp_path / "position-strategies.json"
    registry = PositionStrategyRegistry(path)
    state = live_state(
        holding=100,
        active_buy_volume=0,
        active_sell_volume=1,
        active_buy_count=0,
        active_sell_count=1,
    )
    registry.register_many_existing(
        [candidate(state)],
        tenant_session_id="personal",
        source="unit_test",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    entry = payload["accounts"]["acc-1"]["positions"]["ob-1"]
    entry["protection_classification"] = "NAMED_EXCEPTION"
    entry["protection_reason"] = "Legacy named exception with token SELL row."
    entry.pop("protection_target_antal")
    entry.pop("retained_core_antal")
    path.write_text(json.dumps(payload), encoding="utf-8")

    audit = PositionStrategyRegistry(path).reconcile_account(
        "acc-1",
        [{"account_id": "acc-1", "orderbook_id": "ob-1", "volume": 100}],
        [
            {
                "account_id": "acc-1",
                "orderbook_id": "ob-1",
                "status": "ACTIVE",
                "side": "SELL",
                "stop_loss_id": "token-sell",
                "volume": 1,
            }
        ],
        [],
    )

    assert audit["positions"][0]["position_protection_status"] == (
        "CONTRADICTION"
    )
    assert audit["broker_sell_protection"]["per_instrument"][0][
        "strategy_target_coverage_status"
    ] == "MISSING"
    assert audit["strategy_target_coverage_mismatch_count"] == 1
    assert audit["protection_complete"] is False
    assert audit["governance_review_eligible"] is False
    assert audit["complete"] is False


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
