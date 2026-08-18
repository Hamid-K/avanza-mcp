from scripts.verify_goal_completion_audit import validate
from scripts.enrich_goal_completion_transaction_gate import enrich


def current_buyback_coverage():
    return {
        "artifact": "PORTFOLIO_BUYBACK_LIVE_COVERAGE",
        "source": "output/PORTFOLIO_BUYBACK_LIVE_COVERAGE_20260819_0006.json",
        "generated_at": "2026-08-19T00:04:46+02:00",
        "live_state_as_of": "2026-08-19T00:04:46+02:00",
        "authority": "REVIEW_ONLY",
        "broker_mutation_authorized": False,
        "universe_contract": (
            "Dynamic union of current holdings and exact account sales. "
            "No fixed historical candidate count is trusted."
        ),
        "scope": [
            {"tenant_session_id": "personal", "account_id": "5227886", "label": "Personal"},
            {"tenant_session_id": "darkcell", "account_id": "7616265", "label": "DarkCell"},
        ],
        "live_governance": {
            "sessions_verified": True,
            "personal_unresolved_position_drift": 0,
            "darkcell_unresolved_position_drift": 0,
            "authorization_off": {"personal": True, "darkcell": True},
        },
        "row_count": 73,
        "summary": {
            "exact_account_rows": 73,
            "personal_rows": 30,
            "darkcell_rows": 43,
            "current_one_share_rows": 42,
            "below_20000_sek_rows": 68,
            "full_exit_rows": 2,
            "buyback_coverage_state_counts": {
                "LADDER_ACTIVE": 5,
                "LADDER_DORMANT": 4,
                "LADDER_GAP": 3,
                "LEDGER_ONLY": 57,
                "NAMED_EXCEPTION": 4,
                "REPAIR_REQUIRED": 0,
            },
            "low_exposure_decision_counts": {
                "BUILD_REVIEW": 9,
                "EXIT_OR_NO_REENTRY_REVIEW": 2,
                "INTENTIONAL_MARKER_OR_CORE_HOLD": 56,
                "NAMED_EXCEPTION": 4,
                "REPAIR_REQUIRED": 2,
            },
            "percentage_ladders_with_supported_stages": 9,
            "percentage_not_set_rows": 64,
            "pending_r6a_cleanup_rows": 0,
        },
        "validation": {"status": "PASSED", "error_count": 0, "errors": []},
    }


def complete_payload():
    return {
        "artifact": "PORTFOLIO_REQUIREMENT_LEVEL_COMPLETION_AUDIT",
        "complete": False,
        "overall_status": "ACTIVE_NOT_COMPLETE",
        "goal_completion_claim": False,
        "broker_mutation": False,
        "registry_mutation": False,
        "current_buyback_coverage": current_buyback_coverage(),
        "strategy_audit_coverage": {
            "artifact": "PER_ACCOUNT_STRATEGY_AUDIT_COVERAGE",
            "status": "REQUIRES_NEW_SCOPED_LIVE_REFRESH",
            "live_refresh_verified": False,
            "audits": [
                {"tool": tool, "tenant_session_id": tenant, "account_id": account, "current_run_status": "NOT_RUN_SESSION_UNAVAILABLE"}
                for tool in ("avanza_position_strategy_audit", "avanza_stoploss_strategy_audit")
                for tenant, account in (("personal", "5227886"), ("darkcell", "7616265"))
            ],
        },
        "current_control_state": {
            "broker_mutation": False,
            "paper_mutation": False,
            "trade_authority": False,
            "live_authorization": {"personal": False, "darkcell": False},
            "live_state_current": False,
            "live_refresh_required_before_action": True,
            "live_checkpoint_status": "STAMPED_SNAPSHOT_REQUIRES_NEW_SCOPED_REFRESH",
        },
        "strategy_coverage": {
            "artifact": "PORTFOLIO_INSTRUMENT_STRATEGY_MASTER",
            "unique_instruments": 65,
            "account_position_rows": 107,
            "exact_account_scope_rows": 107,
            "exact_account_scope_complete": True,
            "generic_recommendation_rows_remaining": 0,
            "current_drift_or_error_rows": "3 holding-drift rows; 0 stop/order error rows",
        },
        "portfolio_control_coverage": {
            "factor": {"artifact": "PORTFOLIO_FACTOR_EXPOSURE", "instrument_rows": 65, "unique_instruments": 65, "account_position_rows": 107, "freshness": {"status": "STAMPED_ANALYSIS_SNAPSHOT", "live_state_current": False, "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True}},
            "pending_order": {"artifact": "PORTFOLIO_PENDING_ORDER_IMPLEMENTATION", "active_rows": 54, "unique_stop_ids": 54, "buy_rows": 46, "sell_rows": 8, "generic_implementation_rows": 0, "all_strategy_intents_recorded": True, "freshness": {"status": "STAMPED_ANALYSIS_SNAPSHOT", "live_state_current": False, "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True}},
            "displacement": {"artifact": "PORTFOLIO_CAPITAL_DISPLACEMENT", "rows": 23, "candidate_before_cancellation_remains_binding": True, "freshness": {"status": "STAMPED_ANALYSIS_SNAPSHOT", "live_state_current": False, "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True}},
            "risk": {"artifact": "PORTFOLIO_RISK_GOVERNANCE", "authorization": "ANALYSIS_AND_POLICY_ONLY", "hard_churn_brake_active": True, "freshness": {"status": "STAMPED_ANALYSIS_SNAPSHOT", "live_state_current": False, "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True}},
            "buyback": {"artifact": "PORTFOLIO_BUYBACK_DAILY_COVERAGE", "role": "HISTORICAL_STAMPED_SNAPSHOT", "historical_snapshot": True, "candidate_rows": 44, "personal_rows": 18, "darkcell_rows": 26, "one_share_rows": 42, "low_sek_rows": 43, "without_active_buy_rows": 14, "ladder_dormant": 8, "ledger_only": 32, "ladder_gaps": 0, "repair_required": 3, "named_exceptions": 1, "freshness": {"status": "STAMPED_REVIEW_SNAPSHOT", "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True}},
            "artifact_reconciliation": {"artifact": "PORTFOLIO_CONTROL_ARTIFACT_RECONCILIATION", "status": "BLOCKED_STALE_ARTIFACT_CONTRADICTION", "live_reconciliation_counts": {"active_rows": 62, "buy_rows": 48, "sell_rows": 14}, "pending_order_counts": {"active_rows": 54, "buy_rows": 46, "sell_rows": 8}, "count_delta_live_minus_pending": {"active_rows": 8, "buy_rows": 2, "sell_rows": 6}, "freshness": {"status": "STAMPED_ANALYSIS_SNAPSHOT", "live_state_current": False, "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True}},
            "buy_governance": {"artifact": "PORTFOLIO_ACTIVE_BUY_GOVERNANCE_AUDIT", "active_buy_rows": 46, "active_sell_rows": 8, "fixed_monetary_buy_rows": 43, "relative_buy_rows": 3, "validated_ladder_count": 0, "relative_child_cap_defects": [{}, {}, {}], "freshness": {"status": "STAMPED_ANALYSIS_SNAPSHOT", "live_state_current": False, "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True}},
            "forward_kpi": {"artifact": "PORTFOLIO_FORWARD_KPI_COVERAGE_AUDIT", "status": "INCOMPLETE_OUTCOME_EVIDENCE", "scorecard_measure_count": 12, "completed_forward_scorecard_measures": 0, "forward_outcome_proven": False, "hard_churn_brake_active": True, "freshness": {"status": "STAMPED_ANALYSIS_SNAPSHOT", "live_state_current": False, "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True}},
        },
        "transaction_coverage": {
            "artifact": "PORTFOLIO_TRANSACTION_COVERAGE_AUDIT",
            "status": "HISTORICAL_SUMMARY_RECONCILED_RECENT_LIVE_READBACK_REQUIRED",
            "historical_account_position_rows": 107,
            "same_day_buy_fill_attribution": "REQUIRES_NEW_SCOPED_LIVE_REFRESH",
            "source_raw_rows_available": False,
            "same_day_buy_fill_review_status": "NOT_PROVABLE_FROM_STAMPED_SUMMARY",
            "manual_exit_rows": [
                {"tenant_session_id": "personal", "account_id": "5227886", "ticker": "PLTR", "quantity": 18},
                {"tenant_session_id": "darkcell", "account_id": "7616265", "ticker": "PLTR", "quantity": 26},
                {"tenant_session_id": "darkcell", "account_id": "7616265", "ticker": "W", "quantity": 34},
                {"tenant_session_id": "darkcell", "account_id": "7616265", "ticker": "SHOP", "quantity": 8},
                {"tenant_session_id": "darkcell", "account_id": "7616265", "ticker": "NEM", "quantity": 26},
            ],
        },
        "scheduler_coverage": {
            "artifact": "PORTFOLIO_SCHEDULER_COVERAGE_AUDIT",
            "canonical_approval_c_rows": 18,
            "terminal_rows_in_active_section": 5,
            "requires_new_scoped_live_refresh_before_action": True,
            "archive_proposal": {
                "status": "AWAITING_USER_SCHEDULER_AUTHORITY",
                "destination": "Completed Archive",
                "preserve_planned_action_semantics": True,
                "row_ids": ["A", "B", "C", "D", "E"],
            },
        },
        "catalyst_coverage": {
            "artifact": "PORTFOLIO_CATALYST_COVERAGE_AUDIT",
            "verified_upcoming_rows": 21,
            "unverified_upcoming_rows": 1,
            "requires_new_scoped_live_refresh_before_action": True,
        },
        "completion_blockers": [
            {"id": "B1", "condition_to_close": "refresh"},
            {"id": "B4", "condition_to_close": "refresh transactions"},
            {"id": "B5", "condition_to_close": "archive"},
            {"id": "B6", "condition_to_close": "run audits"},
        ],
        "next_required_checks": [{"date": "2026-08-06", "purpose": "review"}],
        "requirements": [
            {"id": f"R{index}", "requirement": "field", "status": "OPEN", "remaining_proof": "evidence"}
            for index in range(1, 9)
        ],
    }


def test_completion_audit_passes_only_when_open_work_is_explicit():
    assert validate(complete_payload()) == []


def test_incomplete_audit_rejects_promoting_the_historical_buyback_snapshot():
    payload = complete_payload()
    payload["portfolio_control_coverage"]["buyback"]["freshness"] = {
        "status": "CURRENT_LIVE_REFRESH",
        "live_state_current": True,
        "live_refresh_verified": True,
        "requires_new_scoped_live_refresh_before_action": False,
    }

    errors = validate(payload)

    assert "historical buyback control freshness must remain stamped" in errors


def test_incomplete_audit_accepts_a_different_dynamic_live_universe_size():
    payload = complete_payload()
    current = payload["current_buyback_coverage"]
    current["row_count"] = 81
    current["summary"].update({
        "exact_account_rows": 81,
        "personal_rows": 33,
        "darkcell_rows": 48,
        "current_one_share_rows": 47,
        "below_20000_sek_rows": 76,
        "percentage_not_set_rows": 72,
    })
    current["summary"]["buyback_coverage_state_counts"]["LEDGER_ONLY"] = 65
    current["summary"]["low_exposure_decision_counts"]["INTENTIONAL_MARKER_OR_CORE_HOLD"] = 64

    assert validate(payload) == []


def test_incomplete_audit_accepts_current_scoped_refresh_with_open_gates():
    payload = complete_payload()
    payload["current_control_state"].update({
        "live_state_current": True,
        "live_refresh_required_before_action": True,
        "live_checkpoint_status": "CURRENT_SCOPED_LIVE_REFRESH_BUT_OTHER_GATES_OPEN",
    })
    payload["strategy_audit_coverage"].update({
        "status": "LIVE_REFRESH_VERIFIED_REVIEW_REQUIRED",
        "live_refresh_verified": True,
    })
    payload["strategy_audit_coverage"]["audits"] = [
        {
            "tool": "avanza_position_strategy_audit",
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "unresolved_mismatch_count": 0,
        },
        {
            "tool": "avanza_stoploss_strategy_audit",
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "error_count": 0,
        },
        {
            "tool": "avanza_position_strategy_audit",
            "tenant_session_id": "darkcell",
            "account_id": "7616265",
            "unresolved_mismatch_count": 0,
        },
        {
            "tool": "avanza_stoploss_strategy_audit",
            "tenant_session_id": "darkcell",
            "account_id": "7616265",
            "error_count": 0,
        },
    ]
    payload["strategy_audit_coverage"]["holding_only_exception_metadata"] = {
        "status": "COMPLETE",
        "expected_count": 3,
        "count": 3,
        "entries": [
            {
                "orderbook_id": orderbook_id,
                "metadata_status": "COMPLETE",
                "owner": "user",
                "reason": "Intentional holding-only drift.",
                "review_due": "NEXT_LIFECYCLE_REVIEW",
                "allowed_mismatches": ["holding"],
                "rebaseline_authorized": False,
            }
            for orderbook_id in ("41567", "3968", "564535")
        ],
    }

    assert validate(payload) == []


def test_live_enrichment_removes_obsolete_unavailable_audit_claims():
    payload = complete_payload()
    payload["requirements"][0]["evidence"] = (
        "Historical summary transaction coverage is separately reconciled to 107 exact account-position rows, "
        "but raw/recent scoped rows remain open."
    )
    payload["requirements"][3]["evidence"] = (
        "Separate per-account position and stop audit calls are represented as a required control, but the current "
        "bridge session is unavailable and the four exact audits have not run."
    )
    live = {
        "scopes": [
            {"tool": "avanza_position_strategy_audit", "tenant_session_id": "personal", "account_id": "5227886"},
            {"tool": "avanza_stoploss_strategy_audit", "tenant_session_id": "personal", "account_id": "5227886"},
            {"tool": "avanza_position_strategy_audit", "tenant_session_id": "darkcell", "account_id": "7616265"},
            {"tool": "avanza_stoploss_strategy_audit", "tenant_session_id": "darkcell", "account_id": "7616265"},
        ],
        "reconciliation": {"next_gate": "retain exceptions"},
    }
    transaction = {
        "status": "LIVE_SCOPED_REFRESH_RAW_SOURCE_GAP",
        "validation": {"historical_account_position_rows": 107, "source_raw_rows_available": False},
        "recent_transaction_evidence": {"manual_exit_rows": []},
        "same_day_buy_fill_review": {"status": "OPEN"},
    }
    enriched = enrich(payload, transaction, live_strategy_audit=live)
    r1 = next(row for row in enriched["requirements"] if row["id"] == "R1")
    r4 = next(row for row in enriched["requirements"] if row["id"] == "R4")
    assert "but raw/recent scoped rows remain open" not in r1["evidence"]
    assert "current bridge session is unavailable" not in r4["evidence"]
    assert "historical raw source remains unavailable" in r1["evidence"]
    assert "Separate per-account position and stop audits were run" in r4["evidence"]
    assert r1["remaining_proof"].count("Complete raw-source recovery") == 1
    assert r4["remaining_proof"].count("Run and reconcile both audit tools") == 0
    assert ";;z" not in r1["remaining_proof"]
    assert "  " not in r4["evidence"]
    assert r4["evidence"].count("Separate per-account position and stop audits were run") == 1


def test_live_manual_exit_reconciliation_is_linked_to_transaction_coverage():
    payload = complete_payload()
    transaction = {
        "artifact": "PORTFOLIO_TRANSACTION_COVERAGE_AUDIT",
        "status": "LIVE_SCOPED_REFRESH_RAW_SOURCE_GAP",
        "validation": {
            "historical_account_position_rows": 107,
            "recent_manual_exit_rows": 5,
            "source_raw_rows_available": False,
            "same_day_buy_fill_attribution": "PROVEN_SCOPED_RECONCILIATION",
        },
        "recent_transaction_evidence": {"manual_exit_rows": [{"ticker": "PLTR"}] * 5},
        "same_day_buy_fill_review": {"status": "PROVEN_SCOPED_RECONCILIATION"},
    }
    live_exit = {
        "artifact": "PORTFOLIO_MANUAL_EXIT_LIVE_RECONCILIATION",
        "as_of": "2026-08-06 19:36 CEST",
        "transaction_window": {"source_raw_rows_available": False},
        "exits": [
            {"current_holding": 1, "active_buy_quantity": 6, "active_sell_quantity": 0},
            {"current_holding": 1, "active_buy_quantity": 0, "active_sell_quantity": 0},
        ],
    }
    enriched = enrich(payload, transaction, manual_exit_live_reconciliation=live_exit)
    linked = enriched["transaction_coverage"]["live_instrument_reconciliation"]
    assert linked["artifact"] == "PORTFOLIO_MANUAL_EXIT_LIVE_RECONCILIATION"
    assert linked["exit_count"] == 2
    assert linked["current_holding_readback_complete"] is True
    assert linked["active_order_state_readback_complete"] is True
    assert linked["source_raw_rows_available"] is False
    exception_metadata = enriched["strategy_audit_coverage"]["holding_only_exception_metadata"]
    assert exception_metadata["status"] == "COMPLETE"
    assert exception_metadata["expected_count"] == 0
    assert exception_metadata["count"] == 0


def test_completion_audit_rejects_false_completion_or_authority():
    payload = complete_payload()
    payload["complete"] = True
    payload["current_control_state"]["trade_authority"] = True

    errors = validate(payload)

    assert "completed audit must set overall_status=COMPLETE" in errors
    assert "current trade authority flag must be false" in errors


def completed_contract_payload():
    payload = complete_payload()
    payload.update({
        "complete": True,
        "overall_status": "COMPLETE",
        "goal_completion_claim": True,
    })
    payload["current_control_state"].update({
        "live_state_current": True,
        "live_refresh_required_before_action": False,
    })
    payload["strategy_coverage"].update({
        "top_level_stop_recovery_rows": 65,
        "top_level_review_schedule_rows": 65,
        "account_semantic_rows": 107,
        "account_semantic_stop_recovery_rows": 107,
    })
    payload["strategy_audit_coverage"].update({
        "status": "CURRENT_SCOPED_LIVE_REFRESH",
        "live_refresh_verified": True,
    })
    for row in payload["strategy_audit_coverage"]["audits"]:
        row["current_run_status"] = "RECORDED_WITH_ZERO_RELEVANT_DRIFT_OR_ERROR"
    for name, control in payload["portfolio_control_coverage"].items():
        if name == "buyback":
            continue
        control["freshness"].update({
            "live_state_current": True,
            "live_refresh_verified": True,
            "requires_new_scoped_live_refresh_before_action": False,
        })
    payload["portfolio_control_coverage"]["forward_kpi"].update({
        "completed_forward_scorecard_measures": 12,
        "forward_outcome_proven": True,
    })
    payload["portfolio_control_coverage"]["artifact_reconciliation"].update({
        "status": "RECONCILED_CURRENT_LIVE_STATE",
        "count_delta_live_minus_pending": {"active_rows": 0, "buy_rows": 0, "sell_rows": 0},
    })
    payload["transaction_coverage"].update({
        "requires_new_scoped_live_refresh_before_action": False,
        "source_raw_rows_available": True,
        "same_day_buy_fill_attribution": "PROVEN_SCOPED_RECONCILIATION",
    })
    payload["scheduler_coverage"].update({
        "requires_new_scoped_live_refresh_before_action": False,
        "terminal_rows_in_active_section": 0,
    })
    payload["catalyst_coverage"]["requires_new_scoped_live_refresh_before_action"] = False
    payload["current_buyback_coverage"]["summary"]["low_exposure_decision_counts"].update({
        "INTENTIONAL_MARKER_OR_CORE_HOLD": 58,
        "REPAIR_REQUIRED": 0,
    })
    payload["completion_blockers"] = []
    for row in payload["requirements"]:
        row["status"] = "COMPLETED"
        row["remaining_proof"] = ""
    return payload


def test_completion_audit_accepts_a_proven_current_live_contract():
    assert validate(completed_contract_payload()) == []


def test_completion_audit_rejects_completed_contract_with_unresolved_audit():
    payload = completed_contract_payload()
    payload["strategy_audit_coverage"]["audits"][0]["current_run_status"] = "NOT_RUN_SESSION_UNAVAILABLE"

    errors = validate(payload)

    assert "every exact position and stop audit must be recorded with zero relevant drift or error" in errors


def test_completion_audit_rejects_completed_contract_with_stale_cross_artifact_counts():
    payload = completed_contract_payload()
    payload["portfolio_control_coverage"]["artifact_reconciliation"]["status"] = "BLOCKED_STALE_ARTIFACT_CONTRADICTION"

    errors = validate(payload)

    assert "cross-artifact reconciliation must be current and reconciled" in errors


def test_completion_audit_rejects_missing_blocker_and_proof():
    payload = complete_payload()
    payload["completion_blockers"] = []
    payload["requirements"][0]["remaining_proof"] = ""

    errors = validate(payload)

    assert "completion blockers must remain explicit" in errors
    assert "remaining proof missing for R1" in errors


def test_completion_audit_rejects_stop_or_order_error_in_drift_summary():
    payload = complete_payload()
    payload["strategy_coverage"]["current_drift_or_error_rows"] = "3 holding-drift rows; 1 stop/order error row"

    errors = validate(payload)

    assert "strategy drift summary must report zero stop/order error rows" in errors


def test_completion_audit_rejects_missing_buyback_coverage():
    payload = complete_payload()
    payload["portfolio_control_coverage"].pop("buyback")

    errors = validate(payload)

    assert "buyback coverage link is missing" in errors


def test_completion_audit_rejects_missing_current_dynamic_buyback_coverage():
    payload = complete_payload()
    payload.pop("current_buyback_coverage")

    errors = validate(payload)

    assert "current dynamic buyback coverage link is missing" in errors


def test_completion_audit_rejects_missing_forward_kpi_coverage():
    payload = complete_payload()
    payload["portfolio_control_coverage"].pop("forward_kpi")

    errors = validate(payload)

    assert "forward KPI coverage link is missing" in errors


def test_completion_audit_rejects_missing_scheduler_archive_proposal():
    payload = complete_payload()
    payload["scheduler_coverage"].pop("archive_proposal")

    errors = validate(payload)

    assert "scheduler archive proposal must remain pending explicit authority" in errors


def test_completion_audit_rejects_hidden_stale_row_contradiction():
    payload = complete_payload()
    payload["portfolio_control_coverage"]["artifact_reconciliation"]["status"] = "RECONCILED_STAMPED_COUNTS"

    errors = validate(payload)

    assert "stale active-row contradiction must remain explicitly blocked" in errors


def test_completion_audit_rejects_incomplete_per_account_audit_coverage():
    payload = complete_payload()
    payload["strategy_audit_coverage"]["audits"] = payload["strategy_audit_coverage"]["audits"][:3]

    errors = validate(payload)

    assert "per-account audit coverage must contain four exact audit calls" in errors


def test_completion_audit_rejects_incomplete_manual_exit_identity_coverage():
    payload = complete_payload()
    payload["transaction_coverage"]["manual_exit_rows"].pop()

    errors = validate(payload)

    assert "central audit manual-exit identities or quantities are incomplete" in errors


def test_completion_enrichment_is_idempotent_for_transaction_and_scheduler_blockers():
    payload = complete_payload()
    transaction = {
        "artifact": "PORTFOLIO_TRANSACTION_COVERAGE_AUDIT",
        "status": "HISTORICAL_SUMMARY_RECONCILED_RECENT_LIVE_READBACK_REQUIRED",
        "validation": {
            "historical_account_position_rows": 107,
            "recent_manual_exit_rows": 5,
            "same_day_buy_fill_attribution": "REQUIRES_NEW_SCOPED_LIVE_REFRESH",
            "requires_new_scoped_live_refresh_before_action": True,
        },
    }
    scheduler = {
        "artifact": "PORTFOLIO_SCHEDULER_COVERAGE_AUDIT",
        "status": "VALIDATED_CONTRACT_WITH_ARCHIVE_GAP",
        "validation": {"active_section_rows": 22, "canonical_approval_c_rows": 18, "terminal_rows_in_active_section": 5},
        "freshness": {"requires_new_scoped_live_refresh_before_action": True},
    }

    catalyst = {
        "artifact": "PORTFOLIO_CATALYST_COVERAGE_AUDIT",
        "status": "VALIDATED_FAIL_CLOSED",
        "validation": {"verified_upcoming_rows": 21, "unverified_upcoming_rows": 1, "event_refresh_rows": 4},
        "freshness": {"requires_new_scoped_live_refresh_before_action": True},
    }
    buyback = {
        "artifact": "PORTFOLIO_BUYBACK_DAILY_COVERAGE",
        "freshness": {"status": "STAMPED_REVIEW_SNAPSHOT", "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True},
        "candidate_universe": {"count": 44, "account_rows": {"personal_5227886": 18, "darkcell_7616265": 26}, "one_share_rows": 42, "low_sek_rows": 43, "without_active_buy_rows": 14},
        "coverage_states": {"LADDER_DORMANT": 8, "LEDGER_ONLY": 32, "LADDER_GAP": 0, "REPAIR_REQUIRED": 3, "NAMED_EXCEPTION": 1},
    }
    once = enrich(payload, transaction, scheduler, catalyst, buyback=buyback, generated_at="2026-08-06T12:00:00+02:00")
    twice = enrich(once, transaction, scheduler, catalyst, buyback=buyback, generated_at="2026-08-06T12:00:00+02:00")
    ids = [row["id"] for row in twice["completion_blockers"]]

    assert ids.count("B4") == 1
    assert ids.count("B5") == 1
    r1 = next(row for row in twice["requirements"] if row["id"] == "R1")
    r5 = next(row for row in twice["requirements"] if row["id"] == "R5")
    assert r1["evidence"].count("Historical summary transaction coverage") == 1
    assert r1["remaining_proof"].count("Complete raw-source recovery") == 1
    assert r5["evidence"].count("The scheduler contract is validated separately") == 1
    assert r5["evidence"].count("Catalyst coverage separately") == 1
    assert r5["remaining_proof"].count("Resolve the active/archive ledger gap") == 1
    assert r5["remaining_proof"].count("Complete actual publication/call") == 1
    assert twice["current_control_state"]["live_state_current"] is False
    assert twice["current_control_state"]["live_refresh_required_before_action"] is True
    assert twice["current_control_state"]["live_checkpoint_status"] == "STAMPED_SNAPSHOT_REQUIRES_NEW_SCOPED_REFRESH"
