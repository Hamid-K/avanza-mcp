from __future__ import annotations

import json

from avanza_mcp.strategy_semantic_audit import audit_strategy_semantics, main


def _fixtures():
    clean_position = {
        "account": "Personal",
        "instrument": "Example",
        "ticker": "EX",
        "venue": "NYSE",
        "orderbook_id": "101",
        "holding": 2,
        "active_buy_volume": 1,
        "active_sell_volume": 0,
        "active_rows": [{"side": "BUY", "volume": 1}],
        "strategy_class": "GROWTH_CORE",
        "horizon": "6-24m",
        "thesis": "Specific thesis.",
        "gate": "ADD: Exact gate. SELL/EXIT: Exact gate.",
        "audit_status": "VALID_CURRENT_PLAN",
        "recommendation": "Hold exact state.",
        "priority": "B",
        "bucket": "CORE_HOLD",
        "next_gate": "Review on exact evidence.",
        "proposed_correction": None,
        "method": "Thesis-led management.",
        "primary_factor": "SOFTWARE",
        "overlapping_themes": ["QUALITY"],
        "catalyst": "Official results.",
        "add_gate": "Exact add gate.",
        "sell_gate": "Exact sell gate.",
        "invalidation": "Exact invalidation.",
        "risk_budget_rule": "Exact risk rule.",
        "friction_rule": "Expected edge above 3x friction.",
        "loss_recovery_rule": "Losses never authorize risk.",
    }
    clean = {"accounts": {"Personal": {"positions": [clean_position]}}}
    master = {
        "instruments": [
            {
                "key": "EX",
                "orderbook_ids": ["101"],
                "priority": "B",
                "bucket": "CORE_HOLD",
                "strategy_classes": ["GROWTH_CORE"],
                "method": "Thesis-led management.",
                "primary_factor": "SOFTWARE",
                "overlapping_themes": ["QUALITY"],
                "thesis": "Specific thesis.",
                "decision_gate": "ADD: Exact gate. SELL/EXIT: Exact gate.",
                "decision": "Hold exact state.",
                "catalyst": "Official results.",
                "add_gate": "Exact add gate.",
                "sell_gate": "Exact sell gate.",
                "invalidation": "Exact invalidation.",
                "risk_budget_rule": "Exact risk rule.",
                "next_review": "Review on exact evidence.",
                "friction_rule": "Expected edge above 3x friction.",
                "loss_recovery_rule": "Losses never authorize risk.",
                "accounts": [
                    {
                        "account": "Personal",
                        "holding": 2,
                        "active_buy_volume": 1,
                        "active_sell_volume": 0,
                        "audit_status": "VALID_CURRENT_PLAN",
                    }
                ],
            }
        ]
    }
    registry_position = {
        key: clean_position.get(key)
        for key in (
            "instrument",
            "strategy_class",
            "horizon",
            "thesis",
            "gate",
            "audit_status",
            "recommendation",
            "priority",
            "bucket",
            "next_gate",
            "ticker",
            "venue",
            "proposed_correction",
        )
    }
    registry_position.update(
        {
            "holding": 2,
            "active_buy_volume": 1,
            "active_sell_volume": 0,
            "active_buy_count": 1,
            "active_sell_count": 0,
        }
    )
    registry = {"accounts": {"1": {"positions": {"101": registry_position}}}}
    return master, clean, registry


def test_semantic_audit_accepts_exact_coverage():
    master, clean, registry = _fixtures()
    report = audit_strategy_semantics(
        master,
        clean,
        registry,
        {"Personal": "1"},
        expected_instruments=1,
        expected_positions=1,
    )

    assert report["ok"] is True
    assert report["coverage"] == {
        "master_instruments": 1,
        "clean_positions": 1,
        "registry_positions": 1,
        "audited_positions": 1,
    }
    assert report["issue_count"] == 0
    assert report["broker_mutation"] is False
    assert report["trade_authority"] is False


def test_semantic_audit_fails_on_registry_and_master_meaning_drift():
    master, clean, registry = _fixtures()
    registry["accounts"]["1"]["positions"]["101"]["recommendation"] = "Stale plan."
    master["instruments"][0]["accounts"][0]["audit_status"] = "PENDING_EVENT"

    report = audit_strategy_semantics(master, clean, registry, {"Personal": "1"})

    assert report["ok"] is False
    assert report["issue_counts"] == {
        "REGISTRY_SEMANTIC_MISMATCH": 1,
        "MASTER_ACCOUNT_STATE_MISMATCH": 1,
    }
    assert {issue["field"] for issue in report["issues"]} == {
        "recommendation",
        "audit_status",
    }


def test_semantic_audit_supports_explicit_account_specific_master_plan():
    master, clean, registry = _fixtures()
    clean_position = clean["accounts"]["Personal"]["positions"][0]
    clean_position["bucket"] = "EXECUTION_HYGIENE_AWAITING_APPROVAL"
    clean_position["recommendation"] = "Delete only after exact approval."
    clean_position["next_gate"] = "Fresh exact approval."
    registered = registry["accounts"]["1"]["positions"]["101"]
    registered["bucket"] = clean_position["bucket"]
    registered["recommendation"] = clean_position["recommendation"]
    registered["next_gate"] = clean_position["next_gate"]
    master["instruments"][0]["accounts"][0]["semantic_plan"] = {
        "bucket": clean_position["bucket"],
        "recommendation": clean_position["recommendation"],
        "next_gate": clean_position["next_gate"],
    }

    report = audit_strategy_semantics(master, clean, registry, {"Personal": "1"})

    assert report["ok"] is True


def test_semantic_audit_cli_returns_nonzero_and_writes_report(tmp_path):
    master, clean, registry = _fixtures()
    registry["accounts"]["1"]["positions"]["101"]["holding"] = 3
    paths = {}
    for name, payload in (("master", master), ("clean", clean), ("registry", registry)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path
    output = tmp_path / "report.json"

    result = main(
        [
            "--master",
            str(paths["master"]),
            "--clean-sheet",
            str(paths["clean"]),
            "--registry",
            str(paths["registry"]),
            "--account-map",
            "Personal=1",
            "--expected-instruments",
            "1",
            "--expected-positions",
            "1",
            "--output",
            str(output),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert result == 1
    assert report["ok"] is False
    assert report["issue_counts"] == {"REGISTRY_LIVE_STATE_MISMATCH": 1}
    assert report["sources"]["master_sha256"]
