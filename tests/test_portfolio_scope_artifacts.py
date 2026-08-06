from scripts.verify_portfolio_scope_artifacts import validate


FRESHNESS = {
    "status": "STAMPED_ANALYSIS_SNAPSHOT",
    "live_state_current": False,
    "live_refresh_verified": False,
    "requires_new_scoped_live_refresh_before_action": True,
}
SCOPE = [
    {"tenant_session_id": "personal", "account_id": "5227886"},
    {"tenant_session_id": "darkcell", "account_id": "7616265"},
]


def _payloads():
    accounts = {
        "Personal": {"account_id": "5227886", "tenant_session_id": "personal"},
        "DarkCell": {"account_id": "7616265", "tenant_session_id": "darkcell"},
    }
    common = {
        "freshness": dict(FRESHNESS),
        "scope": list(SCOPE),
        "validation": {
            "exact_account_scope": list(SCOPE),
            "exact_account_scope_complete": True,
        },
    }
    clean = {**common, "accounts": accounts, "validation": {
        **common["validation"],
        "live_authorization_personal": False,
        "live_authorization_darkcell": False,
    }}
    factor = {**common}
    pending = {**common, "rows": []}
    displacement = {
        "freshness": dict(FRESHNESS),
        "scope": [SCOPE[1]],
        "validation": {
            "exact_account_scope": [SCOPE[1]],
            "exact_account_scope_complete": True,
        },
        "rows": [],
    }
    risk = {**common}
    live = {**common, "accounts": accounts, "validation": {
        **common["validation"],
        "broker_mutation": False,
        "live_authorization_personal": False,
        "live_authorization_darkcell": False,
    }}
    return clean, factor, pending, displacement, risk, live


def test_scope_validator_requires_stamped_freshness_on_clean_and_live():
    payloads = _payloads()
    payloads[0].pop("freshness")
    errors = validate(*payloads)
    assert any("clean: freshness status" in error for error in errors)

    payloads = _payloads()
    payloads[-1]["freshness"]["live_refresh_verified"] = True
    errors = validate(*payloads)
    assert any("live: live refresh is not explicitly unverified" in error for error in errors)


def test_scope_validator_requires_stamped_freshness_on_displacement():
    payloads = _payloads()
    payloads[3].pop("freshness")
    errors = validate(*payloads)
    assert any("displacement: freshness status" in error for error in errors)
