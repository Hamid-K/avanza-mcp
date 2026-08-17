from avanza_mcp.recovery_reachability import (
    audit_buy_reachability,
    classify_buy_reachability,
    govern_recovery_reachability,
)


def _row(**overrides):
    row = {
        "account_id": "acc-1",
        "orderbook_id": "ob-1",
        "stock": "Example",
        "side": "BUY",
        "status": "ACTIVE",
        "volume": 3,
        "trigger_type": "LESS_OR_EQUAL",
        "trigger_value": 95,
        "trigger_value_type": "MONETARY",
        "order_price": 95.5,
        "order_price_type": "MONETARY",
        "strategy_intent": "TACTICAL_RECOVERY",
    }
    row.update(overrides)
    return row


def _plan(**overrides):
    plan = {
        "account_id": "acc-1",
        "orderbook_id": "ob-1",
        "active_buy_volume": 3,
        "active_buy_count": 1,
        "audit_status": "VALID_REACHABLE_PARTICIPATION",
        "bucket": "VALID_REACHABLE_PARTICIPATION",
        "protection_classification": "CORE_HOLD_EXCEPTION",
        "gate": "Keep only while the reviewed instrument gate remains valid.",
        "stance": "Current exact-account plan.",
        "recommendation": "Preserve the reviewed row.",
        "next_gate": "Review on fill or a material thesis, technical, or risk change.",
    }
    plan.update(overrides)
    return plan


def test_classifies_reachable_and_deep_fixed_buy_rows():
    reachable = classify_buy_reachability(_row(), last_price=100)
    assert reachable["reachability_classification"] == "REACHABLE_FIXED_REVIEW"
    assert reachable["distance_to_trigger_percent"] == 5
    assert reachable["trade_authority"] is False

    deep = classify_buy_reachability(
        _row(trigger_value=70),
        last_price=100,
    )
    assert deep["reachability_classification"] == "DEEP_FIXED_REVIEW"
    assert deep["distance_to_trigger_percent"] == 30
    assert deep["reachability_issue"] == "DEEP_FIXED_REVIEW"


def test_classifies_secondary_fixed_row_outside_practical_band():
    secondary = classify_buy_reachability(
        _row(trigger_value=90),
        last_price=100,
    )
    assert secondary["reachability_classification"] == "SECONDARY_FIXED_REVIEW"
    assert secondary["distance_to_trigger_percent"] == 10
    assert secondary["reachability_issue"] is None


def test_classifies_reversal_trigger_width_without_inferring_entry():
    reachable = classify_buy_reachability(
        _row(
            trigger_type="FOLLOW_DOWNWARDS",
            trigger_value=2.5,
            trigger_value_type="PERCENTAGE",
            order_price=100.5,
            order_price_type="PERCENTAGE",
        ),
        last_price=100,
    )
    assert reachable["reachability_classification"] == "REACHABLE_REVERSAL_REVIEW"

    wide = classify_buy_reachability(
        _row(
            trigger_type="FOLLOW_DOWNWARDS",
            trigger_value=8,
            trigger_value_type="PERCENTAGE",
            order_price=100.5,
            order_price_type="PERCENTAGE",
        ),
        last_price=100,
    )
    assert wide["reachability_classification"] == "WIDE_REVERSAL_REVIEW"


def test_audit_rejects_deep_only_and_unpaired_deep_residual():
    report = audit_buy_reachability(
        [_row(trigger_value=70, strategy_intent="DEEP_RESIDUAL")],
        quotes_by_orderbook={"ob-1": 100},
    )
    assert report["complete"] is False
    assert report["review_required"] is True
    assert report["issue_count"] == 2
    assert report["instruments"][0]["issues"] == [
        "DEEP_ONLY_RECOVERY",
        "DEEP_RESIDUAL_WITHOUT_PARTICIPATION",
    ]
    assert report["broker_mutation"] is False
    assert report["trade_authority"] is False


def test_audit_accepts_deep_residual_only_when_paired_with_reachable_path():
    report = audit_buy_reachability(
        [
            _row(stop_loss_id="near", trigger_value=95),
            _row(
                stop_loss_id="deep",
                trigger_value=70,
                strategy_intent="DEEP_RESIDUAL",
            ),
        ],
        quotes_by_orderbook={"ob-1": 100},
    )
    assert report["complete"] is True
    assert report["issue_count"] == 0
    assert report["instruments"][0]["reachable_count"] == 1
    assert report["instruments"][0]["deep_count"] == 1


def test_audit_blocks_secondary_only_recovery_without_practical_participation():
    report = audit_buy_reachability(
        [_row(trigger_value=90)],
        quotes_by_orderbook={"ob-1": 100},
    )
    assert report["complete"] is False
    assert report["issue_count"] == 1
    assert report["instruments"][0]["issues"] == ["SECONDARY_ONLY_RECOVERY"]
    assert report["instruments"][0]["secondary_count"] == 1


def test_audit_flags_wide_reversal_even_when_another_row_is_reachable():
    report = audit_buy_reachability(
        [
            _row(trigger_value=95),
            _row(
                stop_loss_id="wide",
                trigger_type="FOLLOW_DOWNWARDS",
                trigger_value=8,
                trigger_value_type="PERCENTAGE",
                order_price=100.5,
                order_price_type="PERCENTAGE",
            ),
        ],
        quotes_by_orderbook={"ob-1": 100},
    )
    assert report["complete"] is False
    assert report["instruments"][0]["issues"] == ["WIDE_REVERSAL_ROW"]


def test_governance_keeps_raw_issue_but_explains_named_exception():
    raw = audit_buy_reachability(
        [_row(trigger_value=70)],
        quotes_by_orderbook={"ob-1": 100},
    )
    report = govern_recovery_reachability(
        raw,
        plans_by_orderbook={
            "ob-1": _plan(
                audit_status="ETH_WIDE_PROTECTION_ACTIVE",
                bucket="NAMED_EXCEPTION_WIDE_PROTECTION_ACTIVE",
                protection_classification="NAMED_EXCEPTION",
            )
        },
    )
    instrument = report["instruments"][0]
    assert report["complete"] is False
    assert report["issue_count"] == 1
    assert report["governance_complete"] is True
    assert report["unresolved_issue_count"] == 0
    assert instrument["issues"] == ["DEEP_ONLY_RECOVERY"]
    assert instrument["explained_issues"] == ["DEEP_ONLY_RECOVERY"]
    assert instrument["governance_classification"] == "EXPLAINED_NAMED_EXCEPTION"


def test_governance_explains_locked_residual_and_explicit_review_bands():
    locked_raw = audit_buy_reachability(
        [_row(trigger_value=70, strategy_intent="DEEP_RESIDUAL")],
        quotes_by_orderbook={"ob-1": 100},
    )
    locked = govern_recovery_reachability(
        locked_raw,
        plans_by_orderbook={
            "ob-1": _plan(
                audit_status="WAITING_REVERSAL_LOCKED_RESIDUAL",
                bucket="LOCKED_REACHABLE_RESIDUAL",
            )
        },
    )
    assert locked["governance_complete"] is True
    assert locked["explained_issue_count"] == 2
    assert (
        locked["instruments"][0]["governance_classification"]
        == "EXPLAINED_LOCKED_RESIDUAL"
    )

    secondary_raw = audit_buy_reachability(
        [_row(trigger_value=90)],
        quotes_by_orderbook={"ob-1": 100},
    )
    secondary = govern_recovery_reachability(
        secondary_raw,
        plans_by_orderbook={
            "ob-1": _plan(
                audit_status="SECONDARY_FIXED_REVIEW",
                bucket="SECONDARY_QUALITY_REBUILD",
            )
        },
    )
    assert secondary["governance_complete"] is True
    assert (
        secondary["instruments"][0]["governance_classification"]
        == "EXPLAINED_SECONDARY_REVIEW"
    )

    dormant_raw = audit_buy_reachability(
        [_row(trigger_value=70)],
        quotes_by_orderbook={"ob-1": 100},
    )
    dormant = govern_recovery_reachability(
        dormant_raw,
        plans_by_orderbook={
            "ob-1": _plan(
                audit_status="DEEP_DORMANT_REVIEW",
                bucket="DORMANT_QUALITY_REBUILD",
            )
        },
    )
    assert dormant["governance_complete"] is True
    assert (
        dormant["instruments"][0]["governance_classification"]
        == "EXPLAINED_DORMANT_REVIEW"
    )


def test_governance_fails_closed_on_missing_repair_or_contradictory_plan():
    raw = audit_buy_reachability(
        [_row(trigger_value=70)],
        quotes_by_orderbook={"ob-1": 100},
    )

    missing = govern_recovery_reachability(raw, plans_by_orderbook={})
    assert missing["governance_complete"] is False
    assert missing["instruments"][0]["unresolved_governance_issues"] == [
        "POSITION_PLAN_MISSING"
    ]

    repair = govern_recovery_reachability(
        raw,
        plans_by_orderbook={
            "ob-1": _plan(protection_classification="REPAIR_REQUIRED")
        },
    )
    assert repair["governance_complete"] is False
    assert "POSITION_PLAN_REPAIR_REQUIRED" in repair["instruments"][0][
        "unresolved_governance_issues"
    ]

    contradiction = govern_recovery_reachability(
        raw,
        plans_by_orderbook={"ob-1": _plan()},
    )
    assert contradiction["governance_complete"] is False
    assert "POSITION_PLAN_REACHABILITY_CONTRADICTION" in contradiction[
        "instruments"
    ][0]["unresolved_governance_issues"]

    stale = govern_recovery_reachability(
        raw,
        plans_by_orderbook={"ob-1": _plan(active_buy_volume=2)},
    )
    assert stale["governance_complete"] is False
    assert "POSITION_PLAN_ACTIVE_BUY_DRIFT" in stale["instruments"][0][
        "unresolved_governance_issues"
    ]


def test_governance_does_not_explain_a_dormant_plan_that_still_requires_cleanup():
    raw = audit_buy_reachability(
        [_row(trigger_value=70)],
        quotes_by_orderbook={"ob-1": 100},
    )
    report = govern_recovery_reachability(
        raw,
        plans_by_orderbook={
            "ob-1": _plan(
                audit_status="WAITING_REVERSAL_REDESIGN",
                bucket="DORMANT_REVERSAL_REDESIGN",
                next_gate=(
                    "Seek fresh exact approval only for deleting the non-practical row."
                ),
            )
        },
    )
    instrument = report["instruments"][0]
    assert report["governance_complete"] is False
    assert instrument["governance_classification"] == "UNRESOLVED_REPAIR"
    assert instrument["unresolved_governance_issues"] == [
        "POSITION_PLAN_REDESIGN_UNRESOLVED"
    ]
