import copy
import json
import sys

from scripts.verify_goal_completion_audit import (
    _validate_sold_marker_strategy_reconciliation,
    main,
    validate,
)
from scripts.enrich_goal_completion_transaction_gate import enrich


def no_reentry_decision(*, expires_at="2026-08-26T00:08:00+02:00", contradiction_status="NONE"):
    return {
        "decision_id": "darkcell-7616265-1211627-2026-07-13-no-reentry",
        "tenant_session_id": "darkcell",
        "account_id": "7616265",
        "orderbook_id": "1211627",
        "sale_date": "2026-07-13",
        "sold_quantity": 16,
        "closed_quantity": 16,
        "decision_at": "2026-08-18T18:00:00+02:00",
        "last_revalidated_at": "2026-08-19T00:08:00+02:00",
        "expires_at": expires_at,
        "decision_basis": "Current risk-off evidence rejects rebuilding this exact sold slice.",
        "thesis_evidence": "The current crypto-risk thesis remains intentionally risk-off.",
        "event_evidence": "No newer issuer or regulatory event reverses the risk-off decision.",
        "technical_evidence": "Regular-session structure has not passed the required reclaim gate.",
        "path_evidence": "The full authenticated post-sale path and maximum drawdown were reviewed.",
        "newer_evidence_reviewed": True,
        "contradiction_status": contradiction_status,
    }


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
                "LADDER_DORMANT": 3,
                "LADDER_GAP": 1,
                "LEDGER_ONLY": 58,
                "NAMED_EXCEPTION": 4,
                "REPAIR_REQUIRED": 2,
            },
            "low_exposure_decision_counts": {
                "BUILD_REVIEW": 8,
                "EXIT_OR_NO_REENTRY_REVIEW": 2,
                "INTENTIONAL_MARKER_OR_CORE_HOLD": 55,
                "NAMED_EXCEPTION": 4,
                "NON_STOP_ELIGIBLE": 2,
                "REPAIR_REQUIRED": 2,
            },
            "percentage_ladders_with_supported_stages": 9,
            "percentage_not_set_rows": 64,
            "pending_r6a_cleanup_rows": 0,
        },
        "validation": {"status": "PASSED", "error_count": 0, "errors": []},
    }


def current_sold_marker_recovery():
    rows = [
        {
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "instrument": "Marvell Technology",
            "orderbook_id": "3340",
            "sale_date": "2026-07-02",
            "sold_quantity": 11,
            "sale_attributed_active_buy_quantity": 0,
            "remaining_open_quantity": 11,
            "state": "REPAIR_REQUIRED_HISTORICAL_PATH_MISSED",
        },
        {
            "tenant_session_id": "darkcell",
            "account_id": "7616265",
            "instrument": "Fastly A",
            "orderbook_id": "956885",
            "sale_date": "2026-08-10",
            "sold_quantity": 98,
            "sale_attributed_active_buy_quantity": 0,
            "remaining_open_quantity": 98,
            "state": "REPAIR_REQUIRED_RECLAIM_OBSERVED_AWAITING_FULL_PREFLIGHT",
        },
        {
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "instrument": "SoundHound AI",
            "orderbook_id": "1393460",
            "sale_date": "2026-07-28",
            "sold_quantity": 316,
            "sale_attributed_active_buy_quantity": 0,
            "remaining_open_quantity": 158,
            "state": "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET",
        },
        {
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "instrument": "Advanced Micro Devices",
            "orderbook_id": "529720",
            "sale_date": "2026-07-28",
            "sold_quantity": 7,
            "sale_attributed_active_buy_quantity": 3,
            "remaining_open_quantity": 4,
            "state": "PARTIAL_SOLD_SLICE_RECOVERY_ACTIVE_DEEP_STAGE",
        },
    ]
    reconciliation_rows = [
        {
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "3340",
            "recovery_state": "REPAIR_REQUIRED_HISTORICAL_PATH_MISSED",
            "dynamic_row_found": True,
            "dynamic_buyback_coverage_state": "REPAIR_REQUIRED",
            "dynamic_low_exposure_decision": "REPAIR_REQUIRED",
            "dynamic_protection_classification": "REPAIR_REQUIRED",
            "dynamic_active_buy_volume": 0,
        },
        {
            "tenant_session_id": "darkcell",
            "account_id": "7616265",
            "orderbook_id": "956885",
            "recovery_state": "REPAIR_REQUIRED_RECLAIM_OBSERVED_AWAITING_FULL_PREFLIGHT",
            "dynamic_row_found": True,
            "dynamic_buyback_coverage_state": "REPAIR_REQUIRED",
            "dynamic_low_exposure_decision": "REPAIR_REQUIRED",
            "dynamic_protection_classification": "REPAIR_REQUIRED",
            "dynamic_active_buy_volume": 0,
        },
        {
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "1393460",
            "recovery_state": "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET",
            "dynamic_row_found": True,
            "dynamic_buyback_coverage_state": "LADDER_GAP",
            "dynamic_stages_percent_below_sold_marker": "PERCENTAGE_NOT_SET",
            "dynamic_active_buy_volume": 0,
        },
        {
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "529720",
            "recovery_state": "PARTIAL_SOLD_SLICE_RECOVERY_ACTIVE_DEEP_STAGE",
            "dynamic_row_found": True,
            "dynamic_buyback_coverage_state": "LEDGER_ONLY",
            "dynamic_stages_percent_below_sold_marker": "PERCENTAGE_NOT_SET",
            "dynamic_active_buy_volume": 3,
        },
    ]
    return {
        "artifact": "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE",
        "source": "output/PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE_20260819_0010.json",
        "generated_at": "2026-08-19T00:10:00+02:00",
        "verified_at": "2026-08-19T00:15:00+02:00",
        "path_snapshot_at": "2026-08-19T00:05:00+02:00",
        "status": "ACTIVE_REPAIR_REQUIRED",
        "authority": {"broker_mutation": False, "paper_mutation": False, "trade_authority": False},
        "sources": ["output/PORTFOLIO_SOLD_MARKER_FULL_PATH_AUDIT_20260819_0005.json"],
        "controls": [
            "Evaluate the complete authenticated price path after every same-account sale.",
            "A rebound never erases a crossed but unserved stage.",
            "Durable metadata identifies the exact account and sale.",
            "PERCENTAGE_NOT_SET is fail-closed.",
            "An 8 percent sold-marker drawdown is a mandatory review alarm.",
            "Do not chase a rebound.",
            "No-reentry decisions expire and require exact-sale revalidation against newer evidence.",
        ],
        "row_count": 4,
        "summary": {
            "repair_required_missed_path_rows": 2,
            "percentage_not_set_open_rows": 1,
            "partial_sale_attributed_active_rows": 1,
            "explicit_no_reentry_rows": 0,
            "open_material_rows": 4,
            "remaining_open_quantity_across_material_rows": 271,
            "all_path_active_buy_attribution_gaps_after_registry_correction": 0,
            "silent_active_buy_attribution_gaps_in_material_rows": 0,
            "broker_mutations": 0,
        },
        "rows": rows,
        "dynamic_reconciliation": {
            "source": "output/PORTFOLIO_BUYBACK_LIVE_COVERAGE_20260819_0006.json",
            "generated_at": "2026-08-19T00:20:00+02:00",
            "row_count": 4,
            "rows": reconciliation_rows,
            "status": "PASSED",
            "error_count": 0,
            "errors": [],
        },
    }


def full_history_governance_link(*, repair_required=0):
    return {
        "artifact": "PORTFOLIO_FULL_HISTORY_GOVERNANCE_LINK",
        "schema_version": 1,
        "canonical": {
            "artifact": "PORTFOLIO_FULL_HISTORY_CANONICAL",
            "source": "output/PORTFOLIO_FULL_HISTORY_CANONICAL_20260903_2030.json",
            "generated_at": "2026-09-03T20:30:00+02:00",
            "payload_sha256": "a" * 64,
            "source_identity_count": 4,
            "effective_lineage_count": 4,
            "immutable_sale_lot_count": 4,
            "unique_sale_transaction_id_count": 4,
            "open_sale_lot_count": 0,
            "open_sale_quantity_exact": "0",
        },
        "dynamic_mirror": {
            "artifact": "PORTFOLIO_FULL_DYNAMIC_GOVERNANCE_MIRROR",
            "source": "output/PORTFOLIO_FULL_DYNAMIC_GOVERNANCE_MIRROR_20260903_2030.json",
            "generated_at": "2026-09-03T20:30:00+02:00",
            "payload_sha256": "b" * 64,
            "dynamic_row_count": 4,
            "mirrored_effective_lineage_count": 4,
            "mirrored_source_identity_count": 4,
            "mirrored_immutable_sale_lot_count": 4,
            "repair_required_row_count": repair_required,
        },
        "official_close": {
            "artifact": "PORTFOLIO_R376_OFFICIAL_CLOSE_REACHABILITY",
            "source": "output/PORTFOLIO_R376_OFFICIAL_CLOSE_REACHABILITY_20260902_2224.json",
            "generated_at": "2026-09-02T22:24:00+02:00",
            "payload_sha256": "c" * 64,
            "row_count": 2,
            "state_counts": {"FIRST_STAGE_NOT_REACHED": 2},
            "later_rebound_erases_crossing": False,
        },
        "raw_boundary": {
            "artifact": "PORTFOLIO_R386_FULL_RAW_BOUNDARY_AFTER_ETH_SETTLEMENT",
            "source": "output/PORTFOLIO_R386_FULL_RAW_BOUNDARY_20260903_1454.json",
            "generated_at": "2026-09-03T14:54:00+02:00",
            "payload_sha256": "d" * 64,
            "truncation_risk": False,
        },
        "validation": {"status": "PASSED", "error_count": 0, "errors": []},
        "authority": {
            "trade_authority": False,
            "broker_mutation": False,
            "paper_mutation": False,
        },
        "objective_complete": False,
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
        "current_sold_marker_recovery": current_sold_marker_recovery(),
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
            "top_level_stop_recovery_rows": 65,
            "top_level_review_schedule_rows": 65,
            "account_semantic_rows": 107,
            "account_semantic_stop_recovery_rows": 107,
            "dynamic_identity_contract": {
                "schema_version": 1,
                "instrument_count": 65,
                "account_position_count": 107,
                "instrument_identity_sha256": "e" * 64,
                "account_position_identity_sha256": "f" * 64,
                "objective_audit_identity_parity": True,
            },
        },
        "portfolio_control_coverage": {
            "factor": {"artifact": "PORTFOLIO_FACTOR_EXPOSURE", "instrument_rows": 65, "unique_instruments": 65, "account_position_rows": 107, "exact_account_scope_rows": 107, "freshness": {"status": "STAMPED_ANALYSIS_SNAPSHOT", "live_state_current": False, "live_refresh_verified": False, "requires_new_scoped_live_refresh_before_action": True}},
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
            "unverified_upcoming_rows": 0,
            "stale_unverified_rows": 0,
            "publication_state_current": True,
            "requires_new_scoped_live_refresh_before_action": True,
        },
        "completion_blockers": [
            {"id": "B1", "condition_to_close": "refresh"},
            {"id": "B4", "condition_to_close": "refresh transactions"},
            {"id": "B5", "condition_to_close": "archive"},
            {"id": "B6", "condition_to_close": "run audits"},
            {"id": "B11", "condition_to_close": "close complete-path sold-marker gaps"},
        ],
        "next_required_checks": [{"date": "2026-08-06", "purpose": "review"}],
        "requirements": [
            {"id": f"R{index}", "requirement": "field", "status": "OPEN", "remaining_proof": "evidence"}
            for index in range(1, 9)
        ],
    }


def test_completion_audit_passes_only_when_open_work_is_explicit():
    assert validate(complete_payload()) == []


def test_goal_audit_require_complete_rejects_a_valid_fail_closed_audit(tmp_path, monkeypatch, capsys):
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(complete_payload()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["verify_goal_completion_audit.py", "--audit", str(path)])
    assert main() == 0
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_goal_completion_audit.py", "--audit", str(path), "--require-complete"],
    )
    assert main() == 1
    assert "objective remains incomplete" in capsys.readouterr().out


def _apply_schema7_r19_parity(payload):
    buyback = payload["current_buyback_coverage"]
    buyback["schema_version"] = 7
    recovery = payload["current_sold_marker_recovery"]
    recovery["schema_version"] = 5
    recovery["summary"].update({
        "exact_account_rows_with_prior_same_account_sales": 4,
        "modeled_sale_lots": 4,
        "percentage_not_set_open_rows": 4,
        "material_path_open_rows": 1,
        "named_exception_path_review_rows": 0,
    })
    summary = recovery["summary"]
    payload["current_live_reconciliation"] = {
        "source": "Exact-scoped MCP plus the approved R19 schema-7 dynamic buyback and sold-marker artifacts",
        "dynamic_buyback_schema_version": 7,
        "sold_marker_schema_version": 5,
        "sold_marker_summary": {
            field: summary[field]
            for field in (
                "exact_account_rows_with_prior_same_account_sales",
                "modeled_sale_lots",
                "open_material_rows",
                "remaining_open_quantity_across_material_rows",
                "repair_required_missed_path_rows",
                "material_path_open_rows",
                "named_exception_path_review_rows",
                "explicit_no_reentry_rows",
            )
        },
    }
    requirements = {row["id"]: row for row in payload["requirements"]}
    for requirement_id in ("R3", "R4", "R7"):
        requirements[requirement_id]["governance_revision"] = "R19_MIXED_LOT"
        requirements[requirement_id]["evidence"] = "Approved R19 mixed-lot evidence is current."
    requirements["R3"]["evidence"] = (
        "Approved R19 mixed-lot evidence models 4 immutable sale lots; "
        "4 rows totaling 271 shares remain open, with 2 missed-path repairs, "
        "1 unsupported material gaps, and 0 named-exception reviews."
    )
    requirements["R4"]["evidence"] = (
        "Current exact-account audits retain 2 missed-path repairs and "
        "1 unsupported material gaps under fail-closed review."
    )


def test_schema7_audit_rejects_stale_pre_r19_headline():
    payload = complete_payload()
    _apply_schema7_r19_parity(payload)
    payload["current_live_reconciliation"]["source"] = "Approved R18 schema-6 snapshot"
    payload["requirements"][2]["governance_revision"] = "R18_SEMANTIC_REPAIR"

    errors = validate(payload)

    assert "current live reconciliation still names a pre-R19 source" in errors
    assert "requirement R3 retains stale pre-R19 evidence" in errors


def test_schema7_audit_accepts_current_r19_machine_and_narrative_parity():
    payload = complete_payload()
    _apply_schema7_r19_parity(payload)

    assert validate(payload) == []


def test_enrichment_refreshes_r19_checkpoint_for_new_dynamic_schema():
    payload = complete_payload()
    _apply_schema7_r19_parity(payload)
    buyback = current_buyback_coverage()
    buyback["schema_version"] = 8
    recovery = current_sold_marker_recovery()
    recovery["schema_version"] = 5
    recovery["summary"].update(
        {
            "exact_account_rows_with_prior_same_account_sales": 105,
            "modeled_sale_lots": 1980,
            "material_path_open_rows": 23,
            "named_exception_path_review_rows": 2,
        }
    )

    enriched = enrich(
        payload,
        {},
        live_strategy_audit={"generated_at": "2026-08-28T13:55:24+02:00", "scopes": []},
        current_buyback=buyback,
        current_buyback_source="output/PORTFOLIO_BUYBACK_LIVE_COVERAGE_20260828_1348.json",
        sold_marker_recovery=recovery,
        sold_marker_recovery_source=(
            "output/PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE_20260828_1348.json"
        ),
    )

    checkpoint = enriched["current_live_reconciliation"]
    assert checkpoint["dynamic_buyback_schema_version"] == 8
    assert checkpoint["sold_marker_schema_version"] == 5
    assert checkpoint["dynamic_buyback_source"].endswith("20260828_1348.json")
    assert checkpoint["sold_marker_source"].endswith("20260828_1348.json")
    assert checkpoint["sold_marker_summary"]["modeled_sale_lots"] == 1980
    assert "R19 schema-8 dynamic buyback" in checkpoint["source"]
    assert enriched["current_live_audit"].endswith("2026-08-28T13:55:24+02:00")
    assert enriched["current_live_governance_overlay"].endswith("20260828_1348.json")
    refreshed_r3 = {row["id"]: row for row in enriched["requirements"]}["R3"]
    assert "1,980 immutable sale lots" in refreshed_r3["evidence"]
    assert "4 inner remediation rows totaling 271 shares remain open" in refreshed_r3["evidence"]
    assert "2 missed-path repairs" in refreshed_r3["evidence"]
    assert "23 unsupported material gaps" in refreshed_r3["evidence"]
    assert "2 named-exception reviews" in refreshed_r3["evidence"]
    assert "99,999" not in refreshed_r3["evidence"]


def test_schema7_audit_rejects_stale_r4_machine_totals():
    payload = complete_payload()
    _apply_schema7_r19_parity(payload)
    requirements = {row["id"]: row for row in payload["requirements"]}
    requirements["R4"]["evidence"] = (
        "Current exact-account audits retain 9 missed-path repairs and "
        "8 unsupported material gaps under fail-closed review."
    )

    errors = validate(payload)

    assert "requirement R4 evidence is missing current total: 2 missed-path repairs" in errors
    assert "requirement R4 evidence is missing current total: 1 unsupported material gaps" in errors


def test_schema4_sold_cycle_repair_does_not_rewrite_position_protection_class():
    payload = complete_payload()
    recovery = payload["current_sold_marker_recovery"]
    recovery["schema_version"] = 4
    recovery["summary"].update({
        "percentage_not_set_open_rows": 4,
        "material_path_open_rows": 1,
    })
    for row in recovery["dynamic_reconciliation"]["rows"]:
        if str(row.get("recovery_state") or "").startswith("REPAIR_REQUIRED"):
            row["dynamic_protection_classification"] = "CORE_HOLD_EXCEPTION"

    assert validate(payload) == []


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
    current["summary"]["buyback_coverage_state_counts"]["LEDGER_ONLY"] = 66
    current["summary"]["low_exposure_decision_counts"]["INTENTIONAL_MARKER_OR_CORE_HOLD"] = 63

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
            "protection_repair_required_count": 0,
            "protection_repair_required_orderbook_ids": [],
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
            "protection_repair_required_count": 0,
            "protection_repair_required_orderbook_ids": [],
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


def test_position_protection_repairs_are_independent_from_sold_cycle_repairs():
    payload = {
        "strategy_audit_coverage": {
            "live_refresh_verified": True,
            "audits": [
                {
                    "tool": "avanza_position_strategy_audit",
                    "tenant_session_id": "personal",
                    "protection_repair_required_count": 1,
                    "protection_repair_required_orderbook_ids": ["3674"],
                },
                {
                    "tool": "avanza_position_strategy_audit",
                    "tenant_session_id": "darkcell",
                    "protection_repair_required_count": 1,
                    "protection_repair_required_orderbook_ids": ["1211627"],
                },
            ],
        },
        "current_sold_marker_recovery": {
            "summary": {"unmodeled_prior_sale_identity_count": 0},
            "rows": [],
        },
    }
    errors: list[str] = []

    _validate_sold_marker_strategy_reconciliation(payload, errors, require_clean=False)

    assert errors == []


def test_incomplete_audit_accepts_exact_raw_transaction_recovery_with_other_gates_open():
    payload = complete_payload()
    payload["transaction_coverage"].update({
        "source": "output/PORTFOLIO_RAW_TRANSACTION_RECOVERY_20260820_1046.json",
        "status": "EXACT_ACCOUNT_RAW_SOURCE_VERIFIED",
        "source_raw_rows_available": True,
        "same_day_buy_fill_attribution": "PROVEN_SCOPED_RECONCILIATION",
        "same_day_buy_fill_review_status": "PROVEN_SCOPED_RECONCILIATION",
        "requires_new_scoped_live_refresh_before_action": False,
        "raw_row_shape_verified": True,
        "raw_account_coverage": [
            {
                "tenant_session_id": "personal",
                "account_id": "5227886",
                "exact_account_scope": True,
                "raw_rows": 492,
                "returned_rows": 492,
                "truncation_risk": False,
            },
            {
                "tenant_session_id": "darkcell",
                "account_id": "7616265",
                "exact_account_scope": True,
                "raw_rows": 720,
                "returned_rows": 720,
                "truncation_risk": False,
            },
        ],
    })
    payload["completion_blockers"] = [
        row for row in payload["completion_blockers"] if row["id"] != "B4"
    ]
    payload["closed_blockers"] = [
        {"id": "B4", "evidence": "Exact raw rows recaptured for both accounts."}
    ]

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


def test_raw_transaction_recovery_closes_b4_without_hiding_other_blockers():
    payload = complete_payload()
    transaction = {
        "artifact": "PORTFOLIO_TRANSACTION_COVERAGE_AUDIT",
        "status": "LIVE_SCOPED_REFRESH_RAW_SOURCE_GAP",
        "validation": {
            "historical_account_position_rows": 107,
            "source_raw_rows_available": False,
        },
    }
    raw_recovery = {
        "artifact": "PORTFOLIO_RAW_TRANSACTION_RECOVERY",
        "status": "COMPLETE_EXACT_ACCOUNT_RAW_SOURCE_RECAPTURED",
        "verified_raw_row_shape": ["id", "tradeDate", "account"],
        "accounts": [
            {
                "tenant_session_id": "personal",
                "account_id": "5227886",
                "exact_account_scope": True,
                "raw_rows": 492,
                "returned_rows": 492,
                "truncation_risk": False,
            },
            {
                "tenant_session_id": "darkcell",
                "account_id": "7616265",
                "exact_account_scope": True,
                "raw_rows": 720,
                "returned_rows": 720,
                "truncation_risk": False,
            },
        ],
        "manual_exit_raw_proof": [
            {
                "tenant_session_id": tenant,
                "account_id": account,
                "ticker": ticker,
                "orderbook_id": "1",
                "trade_date": "2026-08-04",
                "sell_quantity": quantity,
                "same_day_buy_quantity": 0,
                "raw_transaction_id": f"{tenant}-{ticker}",
                "cancelled": False,
            }
            for (tenant, account, ticker), quantity in {
                ("personal", "5227886", "PLTR"): 18,
                ("darkcell", "7616265", "PLTR"): 26,
                ("darkcell", "7616265", "W"): 34,
                ("darkcell", "7616265", "SHOP"): 8,
                ("darkcell", "7616265", "NEM"): 26,
            }.items()
        ],
    }
    payload["closed_blockers"] = [{"id": "B4", "evidence": "Raw recovery."}]

    enriched = enrich(
        payload,
        transaction,
        raw_transaction_recovery=raw_recovery,
        raw_transaction_recovery_source="output/PORTFOLIO_RAW_TRANSACTION_RECOVERY_20260820_1046.json",
    )

    assert enriched["transaction_coverage"]["status"] == "EXACT_ACCOUNT_RAW_SOURCE_VERIFIED"
    assert enriched["transaction_coverage"]["source_raw_rows_available"] is True
    assert all(row["id"] != "B4" for row in enriched["completion_blockers"])
    assert any(row["id"] == "B6" for row in enriched["completion_blockers"])
    r1 = next(row for row in enriched["requirements"] if row["id"] == "R1")
    assert "Exact-account raw BUY/SELL history is recaptured" in r1["evidence"]
    assert "Complete raw-source recovery" not in r1["remaining_proof"]


def test_completion_audit_rejects_false_completion_or_authority():
    payload = complete_payload()
    payload["complete"] = True
    payload["current_control_state"]["trade_authority"] = True

    errors = validate(payload)

    assert "completed audit must set overall_status=COMPLETE" in errors
    assert "current trade authority flag must be false" in errors


def completed_contract_payload():
    payload = complete_payload()
    _apply_schema7_r19_parity(payload)
    payload.update({
        "complete": True,
        "overall_status": "COMPLETE",
        "goal_completion_claim": True,
    })
    payload["current_control_state"].update({
        "live_state_current": True,
        "live_refresh_required_before_action": False,
    })
    payload["strategy_audit_coverage"].update({
        "status": "CURRENT_SCOPED_LIVE_REFRESH",
        "live_refresh_verified": True,
    })
    for row in payload["strategy_audit_coverage"]["audits"]:
        row["current_run_status"] = "RECORDED_WITH_ZERO_RELEVANT_DRIFT_OR_ERROR"
        if row["tool"] == "avanza_position_strategy_audit":
            row["protection_repair_required_count"] = 0
            row["protection_repair_required_orderbook_ids"] = []
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
    payload["current_buyback_coverage"]["summary"]["buyback_coverage_state_counts"].update({
        "LADDER_GAP": 0,
        "LEDGER_ONLY": 61,
        "REPAIR_REQUIRED": 0,
    })
    payload["current_buyback_coverage"]["summary"]["low_exposure_decision_counts"].update({
        "INTENTIONAL_MARKER_OR_CORE_HOLD": 57,
        "REPAIR_REQUIRED": 0,
    })
    payload["current_buyback_coverage"]["schema_version"] = 9
    payload["current_buyback_coverage"]["summary"].update({
        "small_hold_revalidation_required_rows": 57,
        "small_hold_revalidation_status_counts": {
            "CURRENT": 57,
            "EXPIRED": 0,
            "MISSING": 0,
            "INVALID": 0,
        },
    })
    payload["current_sold_marker_recovery"]["status"] = "COMPLETE"
    payload["current_sold_marker_recovery"]["rows"] = []
    payload["current_sold_marker_recovery"]["row_count"] = 0
    payload["current_sold_marker_recovery"]["summary"].update({
        "repair_required_missed_path_rows": 0,
        "percentage_not_set_open_rows": 0,
        "partial_sale_attributed_active_rows": 0,
        "explicit_no_reentry_rows": 0,
        "open_material_rows": 0,
        "material_path_open_rows": 0,
        "named_exception_path_review_rows": 0,
        "remaining_open_quantity_across_material_rows": 0,
    })
    payload["current_sold_marker_recovery"]["dynamic_reconciliation"].update({
        "row_count": 0,
        "rows": [],
    })
    sold_summary = payload["current_sold_marker_recovery"]["summary"]
    payload["current_live_reconciliation"].update({
        "source": "Exact-scoped MCP plus the approved R19 schema-9 dynamic buyback and sold-marker artifacts",
        "dynamic_buyback_schema_version": 9,
        "sold_marker_summary": {
            field: sold_summary[field]
            for field in (
                "exact_account_rows_with_prior_same_account_sales",
                "modeled_sale_lots",
                "open_material_rows",
                "remaining_open_quantity_across_material_rows",
                "repair_required_missed_path_rows",
                "material_path_open_rows",
                "named_exception_path_review_rows",
                "explicit_no_reentry_rows",
            )
        },
    })
    full_history = full_history_governance_link()
    payload["full_history_governance"] = full_history
    payload["current_live_reconciliation"]["full_history_governance"] = copy.deepcopy(
        full_history
    )
    requirements = {row["id"]: row for row in payload["requirements"]}
    requirements["R3"]["governance_revision"] = "R390_DYNAMIC_FULL_HISTORY"
    requirements["R3"]["evidence"] = (
        "Approved R19 mixed-lot evidence models 4 source identities, 4 effective lineages, "
        "and 4 immutable sale lots across 4 dynamic rows; "
        "0 inner remediation rows totaling 0 shares remain open, with 0 missed-path repairs, "
        "0 unsupported material gaps, and 0 named-exception reviews."
    )
    requirements["R4"]["evidence"] = (
        "Current exact-account audits retain 0 missed-path repairs and "
        "0 unsupported material gaps under fail-closed review."
    )
    payload["completion_blockers"] = []
    for row in payload["requirements"]:
        row["status"] = "COMPLETED"
        row["remaining_proof"] = ""
    return payload


def sync_completed_r19_checkpoint(payload):
    summary = payload["current_sold_marker_recovery"]["summary"]
    checkpoint = payload["current_live_reconciliation"]
    checkpoint["sold_marker_summary"] = {
        field: summary[field]
        for field in (
            "exact_account_rows_with_prior_same_account_sales",
            "modeled_sale_lots",
            "open_material_rows",
            "remaining_open_quantity_across_material_rows",
            "repair_required_missed_path_rows",
            "material_path_open_rows",
            "named_exception_path_review_rows",
            "explicit_no_reentry_rows",
        )
    }
    requirements = {row["id"]: row for row in payload["requirements"]}
    full_history = payload.get("full_history_governance", {})
    canonical = full_history.get("canonical", {}) if isinstance(full_history, dict) else {}
    mirror = full_history.get("dynamic_mirror", {}) if isinstance(full_history, dict) else {}
    full_prefix = (
        f"{canonical['source_identity_count']} source identities, "
        f"{canonical['effective_lineage_count']} effective lineages, and "
        f"{canonical['immutable_sale_lot_count']} immutable sale lots across "
        f"{mirror['dynamic_row_count']} dynamic rows"
        if canonical
        else f"{summary['modeled_sale_lots']} immutable sale lots"
    )
    open_row_label = "inner remediation rows" if canonical else "rows"
    requirements["R3"]["governance_revision"] = (
        "R390_DYNAMIC_FULL_HISTORY" if canonical else "R19_MIXED_LOT"
    )
    requirements["R3"]["evidence"] = (
        f"Approved R19 mixed-lot evidence models {full_prefix}; "
        f"{summary['open_material_rows']} {open_row_label} totaling "
        f"{summary['remaining_open_quantity_across_material_rows']} shares remain open, with "
        f"{summary['repair_required_missed_path_rows']} missed-path repairs, "
        f"{summary['material_path_open_rows']} unsupported material gaps, and "
        f"{summary['named_exception_path_review_rows']} named-exception reviews."
    )
    requirements["R4"]["evidence"] = (
        f"Current exact-account audits retain {summary['repair_required_missed_path_rows']} "
        f"missed-path repairs and {summary['material_path_open_rows']} unsupported material gaps "
        "under fail-closed review."
    )


def add_governed_dormant_sold_marker_ladder(payload):
    recovery = {
        "tenant_session_id": "personal",
        "account_id": "5227886",
        "instrument": "SoundHound AI",
        "orderbook_id": "1393460",
        "sale_date": "2026-07-28",
        "sold_quantity": 316,
        "sale_attributed_active_buy_quantity": 0,
        "remaining_open_quantity": 158,
        "recorded_stage_percentages_below_marker": [8.2, 15.3],
        "recorded_stage_quantities": [79, 79],
        "state": "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED",
    }
    reconciliation = {
        "tenant_session_id": "personal",
        "account_id": "5227886",
        "orderbook_id": "1393460",
        "recovery_state": "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED",
        "remaining_open_quantity": 158,
        "sale_attributed_active_buy_quantity": 0,
        "recovery_recorded_stage_percentages_below_marker": [8.2, 15.3],
        "recovery_recorded_stage_quantities": [79, 79],
        "dynamic_row_found": True,
        "dynamic_buyback_coverage_state": "LADDER_DORMANT",
        "dynamic_low_exposure_decision": "BUILD_REVIEW",
        "dynamic_protection_classification": "CORE_HOLD_EXCEPTION",
        "dynamic_active_buy_volume": 0,
        "dynamic_target_rebuild_quantity": 158,
        "dynamic_stages_percent_below_sold_marker": [8.2, 15.3],
        "dynamic_stage_quantities": [79, 79],
        "dynamic_coverage_reason": "Stock-specific dormant review ladder for the remaining same-sale slice.",
    }
    current = payload["current_sold_marker_recovery"]
    current.update({"rows": [recovery], "row_count": 1})
    current["summary"].update({
        "repair_required_missed_path_rows": 0,
        "percentage_not_set_open_rows": 0,
        "partial_sale_attributed_active_rows": 0,
        "explicit_no_reentry_rows": 0,
        "open_material_rows": 1,
        "remaining_open_quantity_across_material_rows": 158,
    })
    current["dynamic_reconciliation"].update({"rows": [reconciliation], "row_count": 1})
    sync_completed_r19_checkpoint(payload)
    return recovery, reconciliation


def add_valid_no_reentry_sold_marker_decision(payload, *, decision=None):
    decision = decision or no_reentry_decision()
    recovery = {
        "tenant_session_id": "darkcell",
        "account_id": "7616265",
        "instrument": "Coinbase",
        "orderbook_id": "1211627",
        "sale_date": "2026-07-13",
        "sold_quantity": 16,
        "closed_no_reentry_quantity": 16,
        "sale_attributed_active_buy_quantity": 0,
        "remaining_open_quantity": 0,
        "state": "EXPLICIT_NO_REENTRY_CURRENT_THESIS",
        "no_reentry_decision": decision,
    }
    reconciliation = {
        "tenant_session_id": "darkcell",
        "account_id": "7616265",
        "orderbook_id": "1211627",
        "sale_date": "2026-07-13",
        "sold_quantity": 16,
        "closed_no_reentry_quantity": 16,
        "remaining_open_quantity": 0,
        "sale_attributed_active_buy_quantity": 0,
        "recovery_state": "EXPLICIT_NO_REENTRY_CURRENT_THESIS",
        "recovery_artifact_generated_at": "2026-08-19T00:10:00+02:00",
        "recovery_artifact_verified_at": "2026-08-19T00:15:00+02:00",
        "recovery_no_reentry_decision": decision,
        "dynamic_row_found": True,
        "dynamic_buyback_coverage_state": "LEDGER_ONLY",
        "dynamic_low_exposure_decision": "EXIT_OR_NO_REENTRY_REVIEW",
        "dynamic_protection_classification": "MARKER_EXCEPTION",
        "dynamic_active_buy_volume": 0,
        "dynamic_target_rebuild_quantity": None,
        "dynamic_latest_recent_sale_date": "2026-07-13",
        "dynamic_stages_percent_below_sold_marker": "PERCENTAGE_NOT_SET",
        "dynamic_stage_quantities": None,
        "dynamic_coverage_reason": "Explicit no-reentry decision closes the exact sold slice under current evidence.",
        "dynamic_artifact_generated_at": "2026-08-19T00:20:00+02:00",
        "dynamic_no_reentry_decision": decision,
    }
    current = payload["current_sold_marker_recovery"]
    current.update({"rows": [recovery], "row_count": 1})
    current["summary"].update({
        "repair_required_missed_path_rows": 0,
        "percentage_not_set_open_rows": 0,
        "partial_sale_attributed_active_rows": 0,
        "explicit_no_reentry_rows": 1,
        "open_material_rows": 0,
        "remaining_open_quantity_across_material_rows": 0,
    })
    current["dynamic_reconciliation"].update({"rows": [reconciliation], "row_count": 1})
    sync_completed_r19_checkpoint(payload)
    return recovery, reconciliation


def test_completion_audit_accepts_a_proven_current_live_contract():
    assert validate(completed_contract_payload()) == []


def test_completion_audit_accepts_row_derived_strategy_counts():
    payload = completed_contract_payload()
    strategy = payload["strategy_coverage"]
    strategy.update({
        "unique_instruments": 66,
        "account_position_rows": 108,
        "exact_account_scope_rows": 108,
        "top_level_stop_recovery_rows": 66,
        "top_level_review_schedule_rows": 66,
        "account_semantic_rows": 108,
        "account_semantic_stop_recovery_rows": 108,
    })
    strategy["dynamic_identity_contract"].update({
        "instrument_count": 66,
        "account_position_count": 108,
    })
    factor = payload["portfolio_control_coverage"]["factor"]
    factor.update({
        "instrument_rows": 66,
        "unique_instruments": 66,
        "account_position_rows": 108,
        "exact_account_scope_rows": 108,
    })
    payload["transaction_coverage"]["historical_account_position_rows"] = 108

    assert validate(payload) == []


def test_completion_audit_rejects_missing_full_history_governance_link():
    payload = completed_contract_payload()
    del payload["full_history_governance"]

    errors = validate(payload)

    assert "full-history governance link is missing" in errors


def test_completion_audit_rejects_full_history_mirror_count_drift():
    payload = completed_contract_payload()
    payload["full_history_governance"]["dynamic_mirror"][
        "mirrored_immutable_sale_lot_count"
    ] = 3
    payload["current_live_reconciliation"]["full_history_governance"] = copy.deepcopy(
        payload["full_history_governance"]
    )

    errors = validate(payload)

    assert "full-history mirror sale-lot count mismatch" in errors


def test_completion_audit_rejects_truncated_full_history_boundary():
    payload = completed_contract_payload()
    payload["full_history_governance"]["raw_boundary"]["truncation_risk"] = True
    payload["current_live_reconciliation"]["full_history_governance"] = copy.deepcopy(
        payload["full_history_governance"]
    )

    errors = validate(payload)

    assert "full-history raw boundary retains truncation risk" in errors


def test_completion_audit_rejects_full_history_validation_failure():
    payload = completed_contract_payload()
    payload["full_history_governance"]["validation"] = {
        "status": "FAILED",
        "error_count": 1,
        "errors": ["source parity mismatch"],
    }
    payload["current_live_reconciliation"]["full_history_governance"] = copy.deepcopy(
        payload["full_history_governance"]
    )

    errors = validate(payload)

    assert "full-history canonical or mirror validation did not pass" in errors


def test_completion_audit_rejects_full_history_repairs():
    payload = completed_contract_payload()
    payload["full_history_governance"]["dynamic_mirror"]["repair_required_row_count"] = 1
    payload["current_live_reconciliation"]["full_history_governance"] = copy.deepcopy(
        payload["full_history_governance"]
    )

    errors = validate(payload)

    assert "completed goal retains full-history REPAIR_REQUIRED rows" in errors


def test_incomplete_audit_accepts_valid_full_history_with_open_repairs():
    payload = complete_payload()
    _apply_schema7_r19_parity(payload)
    payload["full_history_governance"] = full_history_governance_link(
        repair_required=1
    )
    payload["current_live_reconciliation"]["full_history_governance"] = copy.deepcopy(
        payload["full_history_governance"]
    )
    sync_completed_r19_checkpoint(payload)

    assert validate(payload) == []


def test_incomplete_audit_rejects_present_invalid_full_history_link():
    payload = complete_payload()
    _apply_schema7_r19_parity(payload)
    payload["full_history_governance"] = full_history_governance_link(
        repair_required=1
    )
    payload["full_history_governance"]["canonical"]["payload_sha256"] = "not-a-hash"
    payload["current_live_reconciliation"]["full_history_governance"] = copy.deepcopy(
        payload["full_history_governance"]
    )
    sync_completed_r19_checkpoint(payload)

    errors = validate(payload)

    assert "full-history canonical payload digest is invalid" in errors


def test_completion_audit_rejects_expired_small_hold_revalidation():
    payload = completed_contract_payload()
    statuses = payload["current_buyback_coverage"]["summary"][
        "small_hold_revalidation_status_counts"
    ]
    statuses["CURRENT"] = 56
    statuses["EXPIRED"] = 1

    errors = validate(payload)

    assert "completed goal retains stale or invalid small-hold revalidation rows" in errors


def test_completion_audit_accepts_valid_time_bounded_no_reentry_decision():
    payload = completed_contract_payload()
    add_valid_no_reentry_sold_marker_decision(payload)

    assert validate(payload) == []


def test_completion_audit_rejects_expired_no_reentry_decision():
    payload = completed_contract_payload()
    add_valid_no_reentry_sold_marker_decision(
        payload,
        decision=no_reentry_decision(expires_at="2026-08-19T00:19:00+02:00"),
    )

    errors = validate(payload)

    assert "current sold-marker no-reentry evidence is missing, expired, or contradicted" in errors
    assert "completed goal retains sold-marker governance gaps" in errors


def test_completion_audit_rejects_newer_no_reentry_contradiction():
    payload = completed_contract_payload()
    add_valid_no_reentry_sold_marker_decision(
        payload,
        decision=no_reentry_decision(contradiction_status="NEWER_EVIDENCE_CONTRADICTS"),
    )

    errors = validate(payload)

    assert "current sold-marker no-reentry evidence is missing, expired, or contradicted" in errors


def test_completion_audit_rejects_exit_no_reentry_with_active_recovery():
    payload = completed_contract_payload()
    recovery, reconciliation = add_valid_no_reentry_sold_marker_decision(payload)
    recovery["sale_attributed_active_buy_quantity"] = 3
    reconciliation["sale_attributed_active_buy_quantity"] = 3
    reconciliation["dynamic_active_buy_volume"] = 3

    errors = validate(payload)

    assert "current sold-marker exit/no-reentry classification conflicts with active recovery inventory" in errors


def test_completion_audit_accepts_open_fully_governed_dormant_ladder():
    payload = completed_contract_payload()
    add_governed_dormant_sold_marker_ladder(payload)

    assert validate(payload) == []


def test_enrichment_does_not_block_a_fully_governed_dormant_ladder():
    payload = completed_contract_payload()
    add_governed_dormant_sold_marker_ladder(payload)

    enriched = enrich(payload, {})

    assert all(row.get("id") != "B11" for row in enriched["completion_blockers"])
    assert all(row.get("id") != "B9" for row in enriched["completion_blockers"])


def test_enrichment_replaces_stale_global_percentage_not_set_blocker():
    payload = complete_payload()
    payload["completion_blockers"].append({
        "id": "B9",
        "type": "BUYBACK_EVIDENCE_GAPS",
        "item": "Sixty-one dynamic rows remain PERCENTAGE_NOT_SET",
        "condition_to_close": "Replace stale semantics.",
    })

    enriched = enrich(payload, {})
    b9 = [row for row in enriched["completion_blockers"] if row.get("id") == "B9"]

    assert len(b9) == 1
    assert "Sixty-one" not in b9[0]["item"]
    assert "valid dated terminal no-reentry and named-exception rows are excluded" in b9[0]["item"]


def test_enrichment_blocks_expired_no_reentry_in_b9_and_b11():
    payload = completed_contract_payload()
    add_valid_no_reentry_sold_marker_decision(
        payload,
        decision=no_reentry_decision(expires_at="2026-08-19T00:19:00+02:00"),
    )

    enriched = enrich(payload, {})
    blockers = {row.get("id"): row for row in enriched["completion_blockers"]}

    assert "B9" in blockers
    assert "no-reentry closure(s) lack current exact-sale evidence" in blockers["B9"]["item"]
    assert "B11" in blockers
    assert "invalid or expired no-reentry closure(s)" in blockers["B11"]["item"]


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


def test_completion_audit_rejects_missing_complete_path_sold_marker_link():
    payload = complete_payload()
    payload.pop("current_sold_marker_recovery")

    errors = validate(payload)

    assert "current sold-marker recovery link is missing" in errors


def test_completion_audit_rejects_open_sold_marker_work_without_b11():
    payload = complete_payload()
    payload["completion_blockers"] = [
        blocker for blocker in payload["completion_blockers"] if blocker["id"] != "B11"
    ]

    errors = validate(payload)

    assert "complete-path sold-marker blocker B11 must remain explicit" in errors


def test_completion_audit_rejects_rebound_snapshot_that_hides_repair_identity():
    payload = complete_payload()
    row = payload["current_sold_marker_recovery"]["dynamic_reconciliation"]["rows"][0]
    row.update({
        "dynamic_buyback_coverage_state": "LEDGER_ONLY",
        "dynamic_low_exposure_decision": "INTENTIONAL_MARKER_OR_CORE_HOLD",
        "dynamic_protection_classification": "MARKER_EXCEPTION",
    })

    errors = validate(payload)

    assert any("missed path is not retained as REPAIR_REQUIRED" in error for error in errors)


def test_completion_audit_keeps_sold_cycle_repair_independent_from_current_exposure_intent():
    payload = complete_payload()
    row = payload["current_sold_marker_recovery"]["dynamic_reconciliation"]["rows"][0]
    row.update({
        "dynamic_buyback_coverage_state": "REPAIR_REQUIRED",
        "dynamic_low_exposure_decision": "INTENTIONAL_MARKER_OR_CORE_HOLD",
        "dynamic_protection_classification": "CORE_HOLD_EXCEPTION",
    })

    errors = validate(payload)

    assert not any("missed path is not retained as REPAIR_REQUIRED" in error for error in errors)


def test_completion_audit_rejects_clean_claim_with_partial_uncovered_remainder():
    payload = completed_contract_payload()
    recovery, reconciliation = add_governed_dormant_sold_marker_ladder(payload)
    recovery.update({
        "sold_quantity": 7,
        "sale_attributed_active_buy_quantity": 3,
        "remaining_open_quantity": 4,
        "state": "PARTIAL_SOLD_SLICE_RECOVERY_ACTIVE_DEEP_STAGE",
    })
    recovery.pop("recorded_stage_percentages_below_marker")
    recovery.pop("recorded_stage_quantities")
    reconciliation.update({
        "recovery_state": "PARTIAL_SOLD_SLICE_RECOVERY_ACTIVE_DEEP_STAGE",
        "remaining_open_quantity": 4,
        "sale_attributed_active_buy_quantity": 3,
        "recovery_recorded_stage_percentages_below_marker": None,
        "recovery_recorded_stage_quantities": None,
        "dynamic_buyback_coverage_state": "LEDGER_ONLY",
        "dynamic_low_exposure_decision": "INTENTIONAL_MARKER_OR_CORE_HOLD",
        "dynamic_active_buy_volume": 3,
        "dynamic_target_rebuild_quantity": 7,
        "dynamic_stages_percent_below_sold_marker": "PERCENTAGE_NOT_SET",
        "dynamic_stage_quantities": None,
    })
    payload["current_sold_marker_recovery"]["summary"].update({
        "partial_sale_attributed_active_rows": 1,
        "remaining_open_quantity_across_material_rows": 4,
    })

    errors = validate(payload)

    assert "completed goal retains sold-marker governance gaps" in errors


def test_completion_audit_rejects_underquantified_dormant_ladder():
    payload = completed_contract_payload()
    _, reconciliation = add_governed_dormant_sold_marker_ladder(payload)
    reconciliation["dynamic_stage_quantities"] = [79, 78]

    errors = validate(payload)

    assert "completed goal retains sold-marker governance gaps" in errors


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
        "validation": {
            "verified_upcoming_rows": 21,
            "unverified_upcoming_rows": 0,
            "stale_unverified_rows": 0,
            "publication_state_current": True,
            "event_refresh_rows": 4,
        },
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
    assert ids.count("B11") == 1
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


def test_current_catalyst_and_scheduler_migration_remove_stale_prose():
    payload = complete_payload()
    r5 = next(row for row in payload["requirements"] if row["id"] == "R5")
    r5["evidence"] = (
        "The legacy catalyst-coverage snapshot still contains a stale SoundHound WAITING_OFFICIAL_DATE row even though "
        "the official August 5 release is now verified elsewhere, so R5 remains fail-closed pending a regenerated current catalyst audit. "
        "The scheduler contract is validated separately; five terminal rows remain in its active section and are explicitly blocked from silent completion."
    )
    r5["remaining_proof"] = (
        "Regenerate current catalyst coverage so verified publications supersede stale date labels, then continue issuer-first call and regular-session reversal review for every due non-terminal row; refresh quote, spread, technical, factor, capacity, and friction evidence before any proposal. "
        "Resolve the active/archive ledger gap and complete the next scoped publication/reversal scan."
    )
    scheduler = {
        "artifact": "PORTFOLIO_SCHEDULER_COVERAGE_AUDIT",
        "validation": {
            "canonical_approval_c_rows": 18,
            "terminal_rows_in_active_section": 0,
        },
        "freshness": {"requires_new_scoped_live_refresh_before_action": True},
    }
    catalyst = {
        "artifact": "PORTFOLIO_CATALYST_COVERAGE_AUDIT",
        "validation": {
            "verified_upcoming_rows": 21,
            "unverified_upcoming_rows": 0,
            "stale_unverified_rows": 0,
            "publication_state_current": True,
        },
        "freshness": {"requires_new_scoped_live_refresh_before_action": True},
    }

    enriched = enrich(payload, {}, scheduler=scheduler, catalyst=catalyst)
    r5 = next(row for row in enriched["requirements"] if row["id"] == "R5")

    assert "stale SoundHound" not in r5["evidence"]
    assert "five terminal rows" not in r5["evidence"]
    assert "Regenerate current catalyst coverage" not in r5["remaining_proof"]
    assert "Resolve the active/archive ledger gap" not in r5["remaining_proof"]
