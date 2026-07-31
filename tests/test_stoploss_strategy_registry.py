import json
import stat

import pytest

from avanza_mcp.stoploss_strategy_registry import StopLossStrategyRegistry


def stop_row(
    *,
    stop_loss_id: str = "sl-1",
    volume: float = 5,
    trigger_type: str = "LESS_OR_EQUAL",
    trigger_value: float = 90,
    trigger_value_type: str = "MONETARY",
    side: str = "BUY",
    order_price: float = 90.5,
    order_price_type: str = "MONETARY",
) -> dict:
    return {
        "account_id": "acc-1",
        "stop_loss_id": stop_loss_id,
        "orderbook_id": "ob-1",
        "stock": "Test",
        "status": "ACTIVE",
        "side": side,
        "volume": volume,
        "trigger_type": trigger_type,
        "trigger_value": trigger_value,
        "trigger_value_type": trigger_value_type,
        "order_price": order_price,
        "order_price_type": order_price_type,
        "valid_until": "2026-09-30",
    }


def test_registry_persists_exact_metadata_and_detects_row_drift(tmp_path):
    path = tmp_path / "strategies.json"
    registry = StopLossStrategyRegistry(path)
    registry.register_existing(
        stop_row(),
        strategy_intent="DEEP_RESIDUAL",
        strategy_reason="Exact fixed residual from a reviewed sold slice.",
        tenant_session_id="personal",
        source="unit_test",
    )

    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    reloaded = StopLossStrategyRegistry(path)
    enriched = reloaded.enrich(stop_row())
    assert enriched["strategy_metadata_status"] == "RECORDED"
    assert enriched["strategy_intent"] == "DEEP_RESIDUAL"
    assert enriched["strategy_source"] == "UNIT_TEST"

    drifted = reloaded.enrich(stop_row(volume=4))
    assert drifted["strategy_metadata_status"] == "STALE_MISMATCH"
    assert drifted["strategy_metadata_mismatches"] == ["volume"]


def test_registry_batch_is_atomic_when_any_candidate_is_invalid(tmp_path):
    path = tmp_path / "strategies.json"
    registry = StopLossStrategyRegistry(path)
    with pytest.raises(ValueError, match="incompatible"):
        registry.register_many_existing(
            [
                {
                    "row": stop_row(stop_loss_id="sl-good"),
                    "strategy_intent": "CORE_RESTORATION",
                    "strategy_reason": "Reviewed core restoration.",
                },
                {
                    "row": stop_row(stop_loss_id="sl-bad"),
                    "strategy_intent": "TACTICAL_HARVEST",
                    "strategy_reason": "Wrong side on purpose.",
                },
            ],
            tenant_session_id="personal",
            source="unit_test",
        )

    assert registry.health()["entry_count"] == 0
    assert not path.exists()


def test_registry_reconcile_prunes_only_missing_exact_ids(tmp_path):
    registry = StopLossStrategyRegistry(tmp_path / "strategies.json")
    for stop_loss_id in ("sl-live", "sl-stale"):
        registry.register_existing(
            stop_row(stop_loss_id=stop_loss_id),
            strategy_intent="CORE_RESTORATION",
            strategy_reason="Reviewed core restoration.",
            tenant_session_id="personal",
            source="unit_test",
        )

    summary = registry.reconcile_account(
        "acc-1",
        [stop_row(stop_loss_id="sl-live")],
        prune_missing=True,
    )
    assert summary["complete"] is True
    assert summary["pruned_stop_loss_ids"] == ["sl-stale"]
    assert registry.lookup("acc-1", "sl-live") is not None
    assert registry.lookup("acc-1", "sl-stale") is None


def test_corrupt_registry_is_read_only_until_repaired(tmp_path):
    path = tmp_path / "strategies.json"
    path.write_text("{not-json", encoding="utf-8")
    registry = StopLossStrategyRegistry(path)

    assert registry.health()["available"] is False
    enriched = registry.enrich(stop_row())
    assert enriched["strategy_metadata_status"] == "REGISTRY_UNAVAILABLE"
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        registry.register_existing(
            stop_row(),
            strategy_intent="CORE_RESTORATION",
            strategy_reason="Should not overwrite corrupt data.",
            tenant_session_id="personal",
            source="unit_test",
        )
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_registry_json_is_versioned_and_account_scoped(tmp_path):
    path = tmp_path / "strategies.json"
    registry = StopLossStrategyRegistry(path)
    registry.register_existing(
        stop_row(),
        strategy_intent="CORE_RESTORATION",
        strategy_reason="Reviewed core restoration.",
        tenant_session_id="personal",
        source="unit_test",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert set(payload["accounts"]) == {"acc-1"}
    assert set(payload["accounts"]["acc-1"]["stops"]) == {"sl-1"}
