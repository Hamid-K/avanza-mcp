import copy
import json
from pathlib import Path

from scripts.build_buyback_daily_coverage_json import (
    candidate_metrics,
    extract_latest_refresh_attempt,
    extract_source_as_of,
    freshness_metadata,
)
from scripts.verify_buyback_ladder_artifact import (
    DYNAMIC_LIVE_GLOB,
    PLAN_PATH,
    TABLE_PATH,
    latest_dynamic_coverage_path,
    validate_candidate_rows,
    validate_dynamic_live_coverage,
    validate_staged_row,
    validate_live_refresh,
)


ROOT = Path(__file__).resolve().parents[1]
REPAIR_REFRESH_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_REPAIR_REFRESH_20260806.json"


def dynamic_buyback_payload():
    rows = [
        {
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "account_label": "Personal",
            "instrument": "Example Alpha",
            "orderbook_id": "1001",
            "live_holding": 1,
            "market_value_band": "BELOW_20000_SEK",
            "selection_reasons": ["ONE_SHARE", "BELOW_20000_SEK", "RECENT_SAME_ACCOUNT_SALE"],
            "active_buy_volume": 1,
            "active_sell_volume": 0,
            "current_protection_classification": "MARKER_EXCEPTION",
            "low_exposure_decision": "BUILD_REVIEW",
            "buyback_coverage_state": "LADDER_ACTIVE",
            "target_rebuild_quantity": 3,
            "stages_percent_below_sold_marker": [8.4, 14.7],
            "stage_quantities": [2, 1],
            "latest_recent_sale_date": "2026-08-18",
            "coverage_reason": "Individually calibrated from the account sale marker and current range.",
            "exact_next_gate": "No additional BUY; review only after the residual fills or thesis changes.",
            "pending_cleanup_id": None,
        },
        {
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "account_label": "Personal",
            "instrument": "Example Beta",
            "orderbook_id": "1002",
            "live_holding": 2,
            "market_value_band": "BELOW_20000_SEK",
            "selection_reasons": ["BELOW_20000_SEK"],
            "active_buy_volume": 0,
            "active_sell_volume": 0,
            "current_protection_classification": "CORE_HOLD_EXCEPTION",
            "low_exposure_decision": "INTENTIONAL_MARKER_OR_CORE_HOLD",
            "buyback_coverage_state": "LEDGER_ONLY",
            "target_rebuild_quantity": None,
            "stages_percent_below_sold_marker": "PERCENTAGE_NOT_SET",
            "stage_quantities": None,
            "latest_recent_sale_date": None,
            "coverage_reason": "No supported ladder exists under current instrument evidence.",
            "exact_next_gate": "Require a confirmed higher low, reclaim, intact thesis, capacity, and friction clearance.",
            "pending_cleanup_id": None,
        },
        {
            "tenant_session_id": "darkcell",
            "account_id": "7616265",
            "account_label": "DarkCell",
            "instrument": "Example Gamma",
            "orderbook_id": "1003",
            "live_holding": 0,
            "market_value_band": "ZERO_POSITION",
            "selection_reasons": ["BELOW_20000_SEK", "RECENT_SAME_ACCOUNT_SALE", "FULL_EXIT"],
            "active_buy_volume": 0,
            "active_sell_volume": 0,
            "current_protection_classification": "FULL_EXIT_REVIEW",
            "low_exposure_decision": "EXIT_OR_NO_REENTRY_REVIEW",
            "buyback_coverage_state": "LADDER_GAP",
            "target_rebuild_quantity": 4,
            "stages_percent_below_sold_marker": "PERCENTAGE_NOT_SET",
            "stage_quantities": None,
            "latest_recent_sale_date": "2026-08-18",
            "coverage_reason": "The full exit has no supported re-entry structure yet.",
            "exact_next_gate": "Require post-event support, a regular-session reclaim, and all risk and friction gates.",
            "pending_cleanup_id": None,
        },
    ]
    return {
        "artifact": "PORTFOLIO_BUYBACK_LIVE_COVERAGE",
        "generated_at": "2026-08-19T00:10:00+02:00",
        "live_state_as_of": "2026-08-19T00:10:00+02:00",
        "authority": "REVIEW_ONLY",
        "broker_mutation_authorized": False,
        "universe_contract": (
            "Dynamic union from current holdings and account sales. "
            "No fixed historical candidate count is trusted."
        ),
        "user_facing_output_contract": {
            "percentage_only": True,
            "raw_prices_prohibited": True,
            "raw_triggers_prohibited": True,
            "monetary_order_values_prohibited": True,
            "unsupported_stages": "PERCENTAGE_NOT_SET",
        },
        "scope": [
            {"tenant_session_id": "personal", "account_id": "5227886", "label": "Personal"},
            {"tenant_session_id": "darkcell", "account_id": "7616265", "label": "DarkCell"},
        ],
        "live_governance": {
            "sessions_verified": True,
            "personal_position_rows": 2,
            "darkcell_position_rows": 1,
            "personal_unresolved_position_drift": 0,
            "darkcell_unresolved_position_drift": 0,
            "authorization_off": {"personal": True, "darkcell": True},
            "runtime_version": "0.2.36",
            "contract_revision": "test",
        },
        "summary": {
            "exact_account_rows": 3,
            "personal_rows": 2,
            "darkcell_rows": 1,
            "current_one_share_rows": 1,
            "below_20000_sek_rows": 2,
            "full_exit_rows": 1,
            "buyback_coverage_state_counts": {
                "LADDER_ACTIVE": 1,
                "LEDGER_ONLY": 1,
                "LADDER_GAP": 1,
            },
            "low_exposure_decision_counts": {
                "BUILD_REVIEW": 1,
                "INTENTIONAL_MARKER_OR_CORE_HOLD": 1,
                "EXIT_OR_NO_REENTRY_REVIEW": 1,
            },
            "percentage_ladders_with_supported_stages": 1,
            "percentage_not_set_rows": 2,
            "pending_r6a_cleanup_rows": 0,
        },
        "rows": rows,
    }


def test_buyback_validator_defaults_to_current_refresh_artifacts():
    assert PLAN_PATH.name == "PORTFOLIO_BUYBACK_LADDER_LIVE_REFRESH_20260806.json"
    assert TABLE_PATH.name == "PORTFOLIO_BUYBACK_LADDER_TABLE_20260806.md"


def test_dynamic_buyback_validator_accepts_variable_size_live_universe():
    payload = dynamic_buyback_payload()

    assert payload["summary"]["exact_account_rows"] == 3
    assert validate_dynamic_live_coverage(payload) == []


def test_dynamic_buyback_validator_rejects_count_drift():
    payload = dynamic_buyback_payload()
    payload["summary"]["personal_rows"] = 18

    errors = validate_dynamic_live_coverage(payload)

    assert "dynamic summary Personal count mismatch" in errors


def test_dynamic_buyback_validator_rejects_cross_instrument_percentage_copy():
    payload = dynamic_buyback_payload()
    copied = copy.deepcopy(payload["rows"][0])
    copied.update({
        "tenant_session_id": "darkcell",
        "account_id": "7616265",
        "account_label": "DarkCell",
        "instrument": "Example Delta",
        "orderbook_id": "1004",
    })
    payload["rows"].append(copied)
    payload["summary"].update({
        "exact_account_rows": 4,
        "darkcell_rows": 2,
        "current_one_share_rows": 2,
        "below_20000_sek_rows": 3,
        "percentage_ladders_with_supported_stages": 2,
    })
    payload["summary"]["buyback_coverage_state_counts"]["LADDER_ACTIVE"] = 2
    payload["summary"]["low_exposure_decision_counts"]["BUILD_REVIEW"] = 2

    errors = validate_dynamic_live_coverage(payload)

    assert any("duplicated across different instruments" in error for error in errors)


def test_dynamic_buyback_selector_ignores_legacy_recheck_names():
    path = latest_dynamic_coverage_path()

    assert DYNAMIC_LIVE_GLOB == "PORTFOLIO_BUYBACK_LIVE_COVERAGE_[0-9]*.json"
    assert path is not None
    assert "RECHECK" not in path.name


def test_ladder_source_records_the_verified_current_refresh():
    payload = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    freshness = payload["freshness"]

    assert freshness["status"] == "CURRENT_LIVE_REFRESH"
    assert freshness["live_state_current"] is True
    assert freshness["live_refresh_verified"] is True
    assert freshness["requires_new_scoped_live_refresh_before_action"] is False
    assert freshness["last_refresh_attempt_status"] == "LIVE_REFRESH_VERIFIED"


def test_repair_refresh_keeps_account_specific_designs_review_only():
    payload = json.loads(REPAIR_REFRESH_PATH.read_text(encoding="utf-8"))

    assert payload["authority"] == {
        "trade_authority": False,
        "broker_mutation": False,
        "paper_mutation": False,
        "statement": (
            "These are review-stage ladder designs only. They do not authorize replacement, "
            "cancellation, repricing, editing, or stop mutation."
        ),
    }
    designs = {(row["account_id"], row["ticker"]): row for row in payload["designs"]}
    assert set(designs) == {("5227886", "FFIV"), ("5227886", "MS"), ("7616265", "MS")}
    assert [stage["pullback_percent"] for stage in designs[("5227886", "FFIV")]["ladder_design"]["stages"]] == [5.0, 9.0, 12.0]
    assert [stage["pullback_percent"] for stage in designs[("5227886", "MS")]["ladder_design"]["stages"]] == [5.0, 10.0]
    assert [stage["pullback_percent"] for stage in designs[("7616265", "MS")]["ladder_design"]["stages"]] == [5.0, 10.0]
    assert all(row["coverage_state"] == "REPAIR_REQUIRED" for row in payload["designs"])


def test_buyback_validator_accepts_an_explicit_current_live_refresh():
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    table = TABLE_PATH.read_text(encoding="utf-8")
    plan = copy.deepcopy(plan)
    plan["freshness"].update(
        {
            "status": "CURRENT_LIVE_REFRESH",
            "live_state_current": True,
            "live_refresh_verified": True,
            "requires_new_scoped_live_refresh_before_action": False,
            "last_refresh_attempt_status": "LIVE_REFRESH_VERIFIED",
        }
    )
    table = table.replace(
        "Latest stamped source snapshot: 2026-08-06 03:52 CEST",
        "Fresh exact live snapshot: 2026-08-06 03:52 CEST",
    ).replace("## Snapshot controls (not current live)", "## Snapshot controls (current live)")

    assert validate_live_refresh(plan, table) == []


def test_rendered_ladder_table_is_explicitly_current_but_read_only():
    table = TABLE_PATH.read_text(encoding="utf-8")

    assert "Fresh exact live snapshot:" in table
    assert "## Live snapshot controls" in table
    assert "no broker or paper mutation is authorized" in table


def test_candidate_metrics_are_derived_from_rows():
    rows = [
        {"account_id": "5227886", "holding": 1, "value_sek": 1000, "existing_buy": "None"},
        {"account_id": "5227886", "holding": 2, "value_sek": 5000, "existing_buy": "2 fixed"},
        {"account_id": "7616265", "holding": 4, "value_sek": 5000.01, "existing_buy": ""},
    ]

    assert candidate_metrics(rows) == {
        "count": 3,
        "account_rows": {"personal_5227886": 2, "darkcell_7616265": 1},
        "one_share_rows": 1,
        "low_sek_rows": 2,
        "without_active_buy_rows": 2,
    }


def test_freshness_contract_is_fail_closed_until_scoped_live_refresh():
    freshness = freshness_metadata("2026-08-06 03:38 CEST")

    assert freshness == {
        "status": "STAMPED_REVIEW_SNAPSHOT",
        "source_as_of": "2026-08-06 03:38 CEST",
        "live_state_current": False,
        "live_refresh_verified": False,
        "requires_new_scoped_live_refresh_before_action": True,
        "statement": (
            "This ledger is derived from a rendered snapshot. It is review evidence only; "
            "both exact tenant/account snapshots must succeed before any action proposal "
            "can be treated as current."
        ),
    }


def test_freshness_metadata_supports_explicit_current_live_state():
    freshness = freshness_metadata("2026-08-06 14:00 CEST", current_live=True)

    assert freshness["status"] == "CURRENT_LIVE_REFRESH"
    assert freshness["live_state_current"] is True
    assert freshness["live_refresh_verified"] is True
    assert freshness["requires_new_scoped_live_refresh_before_action"] is False


def test_source_timestamp_survives_buyback_label_migration():
    assert extract_source_as_of("Stamped source snapshot: 2026-08-06 03:38 CEST\n") == "2026-08-06 03:38 CEST"
    assert extract_source_as_of("Fresh exact live snapshot: 2026-08-06 03:52 CEST\n") == "2026-08-06 03:52 CEST"
    assert extract_source_as_of("Live refresh: 2026-08-06 03:38 CEST\n") == "2026-08-06 03:38 CEST"
    assert extract_source_as_of("No timestamp\n") == "UNSPECIFIED"


def test_latest_refresh_failure_boundary_survives_daily_ledger_rebuild():
    result = extract_latest_refresh_attempt(
        "Latest refresh attempt: `2026-08-06 13:52:45 CEST`; avanza_status failed because the MCP session was unavailable.\n"
    )

    assert result == {
        "last_refresh_attempt_as_of": "2026-08-06 13:52:45 CEST",
        "last_refresh_attempt_status": "SESSION_UNAVAILABLE",
        "last_refresh_error": "avanza_status failed because the MCP session was unavailable.",
    }


def test_staged_row_requires_stock_specific_gate_and_reachable_three_stage_shape():
    row = {
        "account_id": "5227886",
        "tenant_session_id": "personal",
        "ticker": "PLTR",
        "instrument": "Palantir Technologies",
        "holding": 1,
        "current_value_sek": 1502.55,
        "reference": "account sale reference",
        "classification": "DORMANT_STAGED_REBUILD_NOT_VALIDATED_LADDER",
        "fx_presentation_rate_usd_sek": 9.5343,
        "promotion_gate": "event and higher-low review",
        "stages": [
            {"volume": 6, "pullback_percent": 8, "review_price_usd": 149.84, "mark_implied_sek": 1428.62},
            {"volume": 6, "pullback_percent": 12, "review_price_usd": 138.44, "mark_implied_sek": 1319.93},
            {"volume": 6, "pullback_percent": 15, "review_price_usd": 130.30, "mark_implied_sek": 1242.32},
        ],
    }

    assert validate_staged_row(row, (6, 6, 6)) == []


def test_staged_row_rejects_missing_gate_and_non_monotonic_pullback():
    row = {
        "account_id": "5227886",
        "ticker": "PLTR",
        "classification": "DORMANT_STAGED_REBUILD_NOT_VALIDATED_LADDER",
        "stages": [
            {"volume": 6, "pullback_percent": 12, "review_price_usd": 138, "mark_implied_sek": 1300},
            {"volume": 6, "pullback_percent": 8, "review_price_usd": 149, "mark_implied_sek": 1400},
            {"volume": 6, "pullback_percent": 15, "review_price_usd": 130, "mark_implied_sek": 1200},
        ],
    }

    errors = validate_staged_row(row, (6, 6, 6))

    assert any(error.startswith("stock-specific ladder fields missing") for error in errors)
    assert any(error.startswith("promotion gate missing") for error in errors)
    assert any(error.startswith("pullback percentages must increase by stage") for error in errors)


def test_daily_ledger_requires_next_evidence_for_every_candidate():
    valid = {
        "account_id": "7616265",
        "tenant_session_id": "darkcell",
        "ticker": "AMD",
        "instrument": "Advanced Micro Devices",
        "orderbook_id": "529720",
        "holding": 1,
        "value_sek": 4500,
        "existing_buy": "3 fixed",
        "coverage_state": "LEDGER_ONLY",
        "next_daily_evidence": "Review event and regular-session reclaim.",
        "promotion_evidence": "A verified event followed by a regular-session higher low/reclaim and full-friction clearance.",
        "rejection_evidence": "Reject on a failed reclaim, thesis break, or failed full-friction hurdle.",
    }
    invalid = {**valid, "next_daily_evidence": "", "tenant_session_id": "personal"}

    errors = validate_candidate_rows([valid, invalid])

    assert any(error.startswith("candidate tenant scope mismatch") for error in errors)
    assert any(error.startswith("candidate next evidence missing") for error in errors)


def test_daily_ledger_requires_explicit_promotion_and_rejection_evidence():
    row = {
        "account_id": "7616265",
        "tenant_session_id": "darkcell",
        "ticker": "W",
        "instrument": "Wayfair A",
        "orderbook_id": "508521",
        "holding": 1,
        "value_sek": 1000,
        "existing_buy": "None",
        "coverage_state": "LEDGER_ONLY",
        "next_daily_evidence": "Review the event response.",
    }

    errors = validate_candidate_rows([row])

    assert any(error.startswith("candidate promotion evidence missing") for error in errors)
    assert any(error.startswith("candidate rejection evidence missing") for error in errors)
