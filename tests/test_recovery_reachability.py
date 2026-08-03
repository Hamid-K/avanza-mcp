from avanza_mcp.recovery_reachability import (
    audit_buy_reachability,
    classify_buy_reachability,
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
