from scripts.enrich_instrument_strategy_scope import enrich
from scripts.verify_instrument_strategy_master import validate, validate_objective_audit


def _plan(ticker: str) -> dict[str, str]:
    return {
        "ticker": ticker,
        "instrument": ticker,
        "strategy_class": "GROWTH_CORE",
        "portfolio_role": "GROWTH_CORE",
        "intended_exposure": {"current_holding": 1, "conditional_buy_volume": 0, "conditional_sell_volume": 0},
        "stop_and_recovery_design": {
            "current_design": "No stop or recovery row is active.",
            "future_sell_rule": "Named sell gate only.",
            "future_recovery_rule": "Named recovery gate only.",
            "gap_halt_limit": "Gaps and halts are not protected.",
        },
        "catalyst": "Named evidence",
        "thesis": "Forward thesis",
        "add_gate": "Specific technical and capacity gate",
        "sell_gate": "Specific retained-core gate",
        "invalidation": "Specific thesis break",
        "risk_budget_rule": "Bounded risk",
        "friction_rule": "Above 3x full friction",
        "loss_recovery_rule": "Process evidence only",
        "next_gate": "Named event and technical evidence",
        "recommendation": "Hold the classified exposure under the named gate",
        "audit_status": "VALID_CURRENT_PLAN",
    }


def _complete_master() -> dict[str, object]:
    instruments = []
    for index in range(65):
        ticker = f"T{index:02d}"
        account_count = 2 if index < 42 else 1
        accounts = []
        for n in range(account_count):
            label = "Personal" if n == 0 else "DarkCell"
            accounts.append({
                "account": label,
                "tenant_session_id": "personal" if label == "Personal" else "darkcell",
                "account_id": "5227886" if label == "Personal" else "7616265",
                "orderbook_id": str(index),
                "holding": 1,
                "active_buy_volume": 0,
                "active_sell_volume": 0,
                "semantic_plan": _plan(ticker),
            })
        exposure_accounts = [
            {
                "account": account["account"],
                "tenant_session_id": account["tenant_session_id"],
                "account_id": account["account_id"],
                "orderbook_id": account["orderbook_id"],
                "current_holding": account["holding"],
                "conditional_buy_volume": 0,
                "conditional_sell_volume": 0,
            }
            for account in accounts
        ]
        instruments.append(
            {
                "ticker": ticker,
                "instrument": ticker,
                "orderbook_ids": [str(index)],
                "primary_factor": "QUALITY",
                "portfolio_role": "GROWTH_CORE",
                "intended_exposure": {"role": "GROWTH_CORE", "accounts": exposure_accounts},
                "stop_and_recovery_design": {
                    "current_design": "No stop or recovery row is active.",
                    "future_sell_rule": "Named sell gate only.",
                    "future_recovery_rule": "Named recovery gate only.",
                    "gap_halt_limit": "Gaps and halts are not protected.",
                },
                "next_review_schedule": {"date": "2026-08-06", "timezone": "Europe/Stockholm"},
                "decision": "Classified decision",
                "catalyst": "Named evidence",
                "add_gate": "Specific add gate",
                "sell_gate": "Specific sell gate",
                "invalidation": "Specific invalidation",
                "risk_budget_rule": "Bounded risk",
                "friction_rule": "Above 3x full friction",
                "loss_recovery_rule": "Process evidence only",
                "next_review": "Next named review",
                "accounts": accounts,
            }
        )
    return {
        "instruments": instruments,
        "validation": {
            "unique_instruments": 65,
            "account_position_rows": 107,
            "thin_strategy_fields": [],
            "generic_recommendation_rows_remaining": 0,
            "exact_account_scope": [
                {"tenant_session_id": "personal", "account_id": "5227886"},
                {"tenant_session_id": "darkcell", "account_id": "7616265"},
            ],
            "exact_account_scope_rows": 107,
            "exact_account_scope_complete": True,
        },
    }


def test_complete_master_passes_independent_contract():
    assert validate(_complete_master()) == []


def test_scope_enricher_adds_exact_account_fields():
    master = _complete_master()
    for instrument in master["instruments"]:
        for account in instrument["accounts"]:
            account.pop("account_id", None)
            account.pop("tenant_session_id", None)
            account.pop("orderbook_id", None)
    enriched = enrich(master)
    assert enriched["validation"]["exact_account_scope_rows"] == 107
    assert all(
        {"account_id", "tenant_session_id", "orderbook_id"}.issubset(account)
        for instrument in enriched["instruments"]
        for account in instrument["accounts"]
    )


def test_generic_recommendation_is_rejected():
    master = _complete_master()
    master["instruments"][0]["accounts"][0]["semantic_plan"]["recommendation"] = "Keep"

    assert any("generic recommendation" in error for error in validate(master))


def test_objective_audit_rejects_missing_required_field():
    audit = {
        "instruments": [{
            "key": "A",
            "ticker": "A",
            "thesis": "x",
            "catalyst": "x",
            "intended_exposure": {"accounts": [{
                "account": "Personal",
                "tenant_session_id": "personal",
                "account_id": "5227886",
                "orderbook_id": "1",
                "current_holding": 1,
                "conditional_buy_volume": 0,
                "conditional_sell_volume": 0,
            }]},
            "next_review_schedule": {"date": "2026-08-06", "timezone": "Europe/Stockholm"},
        }],
        "validation": {
            "unique_instruments": 65,
            "exact_account_position_rows": 107,
            "required_fields": ["thesis", "catalyst", "intended_exposure"],
            "deficient_fields": [],
            "invalid_review_dates": [],
            "broker_mutation": False,
            "source_code_mutation": False,
            "live_authorization": {"personal": False, "darkcell": False},
        },
    }

    errors = validate_objective_audit(audit)
    assert any("expected 65 instruments" in error for error in errors)
    assert any("expected 107 intended-exposure rows" in error for error in errors)
