import hashlib

from scripts.verify_portfolio_control_artifacts import validate
from scripts.enrich_portfolio_artifact_scope import enrich_artifact, strategy_identity_contract


def _contract(instrument_count: int, account_position_count: int) -> dict:
    if instrument_count == 0:
        empty_digest = hashlib.sha256(b"[]").hexdigest()
        return {
            "schema_version": 1,
            "instrument_count": 0,
            "account_position_count": account_position_count,
            "instrument_name_sha256": empty_digest,
            "account_position_identity_sha256": empty_digest,
        }
    master = {
        "instruments": [
            {
                "instrument": f"I{index}",
                "accounts": [
                    {
                        "tenant_session_id": "personal",
                        "account_id": "5227886",
                        "orderbook_id": str(index),
                    }
                    for _ in range(1)
                ],
            }
            for index in range(instrument_count)
        ]
    }
    contract = strategy_identity_contract(master)
    contract["account_position_count"] = account_position_count
    return contract


def test_portfolio_controls_reject_non_authoritative_factor_overlay():
    factor = {
        "freshness": {
            "status": "STAMPED_ANALYSIS_SNAPSHOT",
            "live_state_current": False,
            "live_refresh_verified": False,
            "requires_new_scoped_live_refresh_before_action": True,
        },
        "current_governance_overlay": {"supersedes_dynamic_factor_values": False},
        "instrument_postfill": [],
        "validation": {
            "unique_instruments": 0,
            "account_position_rows": 0,
            "dynamic_identity_contract": _contract(0, 0),
        },
        "august5_live_factor_overlay_latest": {
            "broker_mutation": False,
            "trade_authority": False,
        },
    }
    empty = {
        "freshness": {
            "status": "STAMPED_ANALYSIS_SNAPSHOT",
            "live_state_current": False,
            "live_refresh_verified": False,
            "requires_new_scoped_live_refresh_before_action": True,
        },
        "current_governance_overlay": {},
        "validation": {},
        "rows": [],
    }
    risk = {
        "freshness": empty["freshness"],
        "authorization": "ANALYSIS_AND_POLICY_ONLY",
        "current_governance_overlay": {},
    }

    errors = validate(factor, empty, empty, risk)

    assert any("current overlay authoritative" in error for error in errors)
    assert any("dynamic instrument identity" in error for error in errors)


def test_portfolio_controls_reject_sell_activation_without_hard_brake():
    factor = {
        "freshness": {
            "status": "STAMPED_ANALYSIS_SNAPSHOT",
            "live_state_current": False,
            "live_refresh_verified": False,
            "requires_new_scoped_live_refresh_before_action": True,
        },
        "current_governance_overlay": {"supersedes_dynamic_factor_values": True},
        "instrument_postfill": [{}] * 65,
        "validation": {
            "unique_instruments": 65,
            "account_position_rows": 107,
            "dynamic_identity_contract": _contract(65, 107),
        },
        "august5_live_factor_overlay_latest": {
            "broker_mutation": False,
            "trade_authority": False,
        },
    }
    empty = {
        "freshness": factor["freshness"],
        "current_governance_overlay": {
            "broker_mutation": False,
            "trade_authority": False,
        },
        "validation": {"active_rows": 54, "unique_stop_ids": 54, "buy_rows": 46, "sell_rows": 8, "generic_implementation_rows": 0, "all_strategy_intents_recorded": True},
        "rows": [{}] * 23,
    }
    risk = {
        "freshness": factor["freshness"],
        "authorization": "ANALYSIS_AND_POLICY_ONLY",
        "current_governance_overlay": {
            "broker_mutation": False,
            "trade_authority": False,
            "current_sell_activation": "NEW_SELL",
            "hard_churn_brake_active": False,
        },
    }

    errors = validate(factor, empty, empty, risk)

    assert any("sell activation" in error for error in errors)
    assert any("hard churn brake" in error for error in errors)


def test_portfolio_controls_require_both_exact_account_scopes():
    factor = {
        "freshness": {
            "status": "STAMPED_ANALYSIS_SNAPSHOT",
            "live_state_current": False,
            "live_refresh_verified": False,
            "requires_new_scoped_live_refresh_before_action": True,
        },
        "current_governance_overlay": {"supersedes_dynamic_factor_values": True},
        "instrument_postfill": [{}] * 65,
        "validation": {
            "unique_instruments": 65,
            "account_position_rows": 107,
            "dynamic_identity_contract": _contract(65, 107),
        },
        "august5_live_factor_overlay_latest": {
            "broker_mutation": False,
            "trade_authority": False,
            "Personal 5227886": {},
        },
    }
    pending = {
        "freshness": factor["freshness"],
        "current_governance_overlay": {},
        "validation": {"current_live_scope": "Personal 5227886 only"},
    }
    empty = {"freshness": factor["freshness"], "current_governance_overlay": {}, "validation": {}, "rows": []}
    risk = {"freshness": factor["freshness"], "authorization": "ANALYSIS_AND_POLICY_ONLY", "scope": [{"tenant_session_id": "personal", "account_id": "5227886"}], "current_governance_overlay": {}}

    errors = validate(factor, pending, empty, risk)

    assert any("exact tenant/account scope" in error for error in errors)
    assert any("DarkCell 7616265" in error for error in errors)
    assert any("both exact account scopes" in error for error in errors)


def test_portfolio_scope_enricher_adds_exact_pending_row_scope():
    payload = {
        "rows": [{"account": "DarkCell", "account_id": "7616265", "ticker": "IONQ"}],
        "validation": {},
    }

    enriched = enrich_artifact("pending", payload, identity_contract=_contract(3, 4))

    assert enriched["rows"][0]["tenant_session_id"] == "darkcell"
    assert enriched["scope"] == [
        {"tenant_session_id": "personal", "account_id": "5227886"},
        {"tenant_session_id": "darkcell", "account_id": "7616265"},
    ]


def test_portfolio_controls_accept_dynamic_factor_universe_size():
    master = {
        "instruments": [
            {
                "instrument": name,
                "accounts": [{
                    "tenant_session_id": "personal",
                    "account_id": "5227886",
                    "orderbook_id": str(index),
                }],
            }
            for index, name in enumerate(("Alpha", "Beta"), 1)
        ]
    }
    contract = strategy_identity_contract(master)
    freshness = {
        "status": "STAMPED_ANALYSIS_SNAPSHOT",
        "live_state_current": False,
        "live_refresh_verified": False,
        "requires_new_scoped_live_refresh_before_action": True,
    }
    factor = {
        "freshness": freshness,
        "scope": [
            {"tenant_session_id": "personal", "account_id": "5227886"},
            {"tenant_session_id": "darkcell", "account_id": "7616265"},
        ],
        "current_governance_overlay": {"supersedes_dynamic_factor_values": True},
        "instrument_postfill": [{"instrument": "Alpha"}, {"instrument": "Beta"}],
        "validation": {
            "unique_instruments": 2,
            "account_position_rows": 2,
            "dynamic_identity_contract": contract,
        },
        "august5_live_factor_overlay_latest": {
            "broker_mutation": False,
            "trade_authority": False,
            "Personal 5227886": {},
            "DarkCell 7616265": {},
            "limits": {},
        },
    }

    assert not any(
        "dynamic identity" in error
        for error in validate(factor, {}, {}, {}, master)
    )
