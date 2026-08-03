from __future__ import annotations

import pytest

from avanza_mcp.strategy_semantic_sync import synchronize_strategy_semantics
from tests.test_strategy_semantic_audit import _fixtures


def test_sync_updates_only_semantics_and_embeds_account_plan():
    master, clean, registry = _fixtures()
    registered = registry["accounts"]["1"]["positions"]["101"]
    registered["recommendation"] = "New reviewed account plan."
    original_holding = clean["accounts"]["Personal"]["positions"][0]["holding"]

    updated_master, updated_clean, report = synchronize_strategy_semantics(
        master,
        clean,
        registry,
        {"Personal": "1"},
        reason="Later reviewed registry semantics supersede legacy wording.",
    )

    clean_position = updated_clean["accounts"]["Personal"]["positions"][0]
    master_position = updated_master["instruments"][0]["accounts"][0]
    assert clean_position["recommendation"] == "New reviewed account plan."
    assert clean_position["holding"] == original_holding
    assert master_position["semantic_plan"]["recommendation"] == "New reviewed account plan."
    assert updated_master["semantic_schema_version"] == 1
    assert updated_master["instruments"][0]["thesis"] == "Specific thesis."
    assert report["registry_to_clean_field_changes"] == 1
    assert report["broker_mutation"] is False
    assert report["registry_mutation"] is False
    assert report["trade_authority"] is False


def test_sync_updates_master_account_audit_status():
    master, clean, registry = _fixtures()
    clean_position = clean["accounts"]["Personal"]["positions"][0]
    clean_position["audit_status"] = "AWAITING_EXPLICIT_DECISION"
    registry["accounts"]["1"]["positions"]["101"]["audit_status"] = (
        "AWAITING_EXPLICIT_DECISION"
    )

    updated_master, _, report = synchronize_strategy_semantics(
        master,
        clean,
        registry,
        {"Personal": "1"},
        reason="A later reviewed account decision supersedes the aggregate label.",
    )

    account_row = updated_master["instruments"][0]["accounts"][0]
    assert account_row["audit_status"] == "AWAITING_EXPLICIT_DECISION"
    assert account_row["semantic_plan"]["audit_status"] == (
        "AWAITING_EXPLICIT_DECISION"
    )
    assert report["master_account_state_changes"] == 1


def test_sync_fails_closed_on_live_fingerprint_drift():
    master, clean, registry = _fixtures()
    registry["accounts"]["1"]["positions"]["101"]["holding"] = 3

    with pytest.raises(ValueError, match="Live fingerprint mismatch"):
        synchronize_strategy_semantics(
            master,
            clean,
            registry,
            {"Personal": "1"},
            reason="Attempted reconciliation.",
        )


def test_sync_requires_documented_reason():
    master, clean, registry = _fixtures()

    with pytest.raises(ValueError, match="reason is required"):
        synchronize_strategy_semantics(
            master,
            clean,
            registry,
            {"Personal": "1"},
            reason="",
        )
