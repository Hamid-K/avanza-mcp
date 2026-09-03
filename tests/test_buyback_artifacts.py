import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from scripts.build_buyback_daily_coverage_json import (
    candidate_metrics,
    extract_latest_refresh_attempt,
    extract_source_as_of,
    freshness_metadata,
)
from scripts.enrich_r17_economic_classification import (
    apply_terminal_decisions_to_dynamic_rows,
    apply_terminal_decisions_to_remediation,
    enrich_payload,
    enrich_remediation_payload,
    filter_path_evidence_to_open_remediation,
)
from scripts.verify_buyback_ladder_artifact import (
    DYNAMIC_LIVE_GLOB,
    SOLD_MARKER_REMEDIATION_GLOB,
    PLAN_PATH,
    TABLE_PATH,
    _canonical_json_sha256,
    latest_dynamic_coverage_path,
    latest_sold_marker_remediation_path,
    governed_dormant_ladder_count,
    sold_marker_dynamic_reconciliation_rows,
    sold_marker_governance_gap_rows,
    validate_candidate_rows,
    validate_dynamic_against_sold_marker_recovery,
    validate_dynamic_live_coverage,
    validate_full_dynamic_governance_mirror,
    validate_full_history_canonical,
    validate_r17_open_path_evidence,
    validate_r17_path_links,
    validate_sold_marker_remediation,
    validate_sold_marker_remediation_against_worklist,
    validate_sold_marker_universe_against_full_path,
    validate_staged_row,
    validate_live_refresh,
)


ROOT = Path(__file__).resolve().parents[1]
REPAIR_REFRESH_PATH = ROOT / "output" / "PORTFOLIO_BUYBACK_REPAIR_REFRESH_20260806.json"


def no_reentry_decision():
    return {
        "decision_id": "darkcell-7616265-1211627-2026-07-13-no-reentry",
        "tenant_session_id": "darkcell",
        "account_id": "7616265",
        "orderbook_id": "1211627",
        "sale_date": "2026-07-13",
        "sale_lot_id": "darkcell-7616265-1211627-2026-07-13-lot-1",
        "sale_transaction_id": "raw-sell-darkcell-7616265-1211627-2026-07-13-1",
        "sale_timestamp": "2026-07-13T00:00:00+02:00",
        "original_sold_quantity": 16,
        "recovered_before_decision_quantity": 0,
        "sold_quantity": 16,
        "closed_quantity": 16,
        "decision_at": "2026-08-18T18:00:00+02:00",
        "last_revalidated_at": "2026-08-19T00:08:00+02:00",
        "expires_at": "2026-08-26T00:08:00+02:00",
        "decision_basis": "Current risk-off evidence rejects rebuilding this exact sold slice.",
        "thesis_evidence": "The current crypto-risk thesis remains intentionally risk-off.",
        "event_evidence": "No newer issuer or regulatory event reverses the risk-off decision.",
        "technical_evidence": "Regular-session structure has not passed the required reclaim gate.",
        "path_evidence": "The full authenticated post-sale path and maximum drawdown were reviewed.",
        "newer_evidence_reviewed": True,
        "contradiction_status": "NONE",
    }


def dynamic_buyback_payload():
    rows = [
        {
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "account_label": "Personal",
            "instrument": "Example Alpha",
            "orderbook_id": "1001",
            "live_holding": 1,
            "live_market_value_sek": 1000.0,
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
            "coverage_reason": "Example Alpha is individually calibrated from its account sale marker and current range.",
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
            "live_market_value_sek": 19999.0,
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
            "live_market_value_sek": 0.0,
            "market_value_band": "ZERO_POSITION",
            "selection_reasons": ["RECENT_SAME_ACCOUNT_SALE", "FULL_EXIT"],
            "active_buy_volume": 0,
            "active_sell_volume": 0,
            "current_protection_classification": "FULL_EXIT_REVIEW",
            "low_exposure_decision": "EXIT_OR_NO_REENTRY_REVIEW",
            "buyback_coverage_state": "LADDER_GAP",
            "target_rebuild_quantity": 4,
            "stages_percent_below_sold_marker": "PERCENTAGE_NOT_SET",
            "stage_quantities": None,
            "latest_recent_sale_date": "2026-08-18",
            "coverage_reason": "Example Gamma's full exit has no supported re-entry structure yet.",
            "exact_next_gate": "Require post-event support, a regular-session reclaim, and all risk and friction gates.",
            "pending_cleanup_id": None,
        },
    ]
    return {
        "artifact": "PORTFOLIO_BUYBACK_LIVE_COVERAGE",
        "schema_version": 3,
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
            "at_or_above_20000_sek_rows": 0,
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


def r17_path_row(
    *,
    tenant_session_id,
    account_id,
    orderbook_id,
    instrument,
    quantity,
    crossed,
    named=False,
):
    maximum = 12.5 if crossed else 5.0
    transaction_id = f"sale-{tenant_session_id}-{account_id}-{orderbook_id}"
    return {
        "tenant_session_id": tenant_session_id,
        "account_id": account_id,
        "orderbook_id": orderbook_id,
        "instrument": instrument,
        "remaining_open_quantity": quantity,
        "remaining_open_lot_count": 1,
        "chart_from": "2026-01-01",
        "chart_to": "2026-08-26",
        "chart_point_count": 100,
        "current_close": 100.0,
        "weighted_open_sale_marker": 105.0,
        "current_drop_below_weighted_marker_percent": 4.7619,
        "maximum_open_lot_drop_percent": maximum,
        "open_lots_crossing_8pct_alarm": 1 if crossed else 0,
        "crossed_8pct_review_alarm": crossed,
        "active_buy_quantity": 0,
        "sale_attributed_active_buy_quantity": 0,
        "technical": "Neutral",
        "rsi": 50.0,
        "atr20_percent_of_current_close": 4.0,
        "named_exception": named,
        "path_state": (
            "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
            if crossed and named
            else "MISSED_PATH_REPAIR_REQUIRED"
            if crossed
            else "LADDER_GAP_PERCENTAGE_NOT_SET"
        ),
        "exact_lots": [
            {
                "sale_lot_id": transaction_id,
                "sale_transaction_id": transaction_id,
                "sale_date": "2026-08-01",
                "remaining_open_quantity": quantity,
                "maximum_drop_below_marker_percent": maximum,
                "crossed_8pct_review_alarm": crossed,
            }
        ],
    }


def r17_path_payload(rows):
    return {
        "artifact": "PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE",
        "schema_version": 1,
        "generated_at": "2026-08-26T17:54:00+02:00",
        "authority": "ANALYSIS_ONLY",
        "broker_mutation": False,
        "scope": [
            {"tenant_session_id": "personal", "account_id": "5227886"},
            {"tenant_session_id": "darkcell", "account_id": "7616265"},
        ],
        "summary": {
            "exact_account_rows": len(rows),
            "unique_orderbooks": len({row["orderbook_id"] for row in rows}),
            "exact_open_sale_lots": sum(row["remaining_open_lot_count"] for row in rows),
            "remaining_open_quantity": sum(row["remaining_open_quantity"] for row in rows),
            "rows_crossing_8pct_review_alarm": sum(row["crossed_8pct_review_alarm"] for row in rows),
            "lots_crossing_8pct_review_alarm": sum(row["open_lots_crossing_8pct_alarm"] for row in rows),
            "named_exception_rows_with_crossing": sum(
                row["crossed_8pct_review_alarm"] and row["named_exception"] for row in rows
            ),
            "path_or_marker_errors": 0,
        },
        "rows": rows,
    }


def schema5_inputs(*, named_crossing=False):
    payload = dynamic_buyback_payload()
    alpha = payload["rows"][0]
    alpha.update({
        "active_buy_volume": 0,
        "buyback_coverage_state": "LADDER_GAP",
        "low_exposure_decision": "REPAIR_REQUIRED",
        "target_rebuild_quantity": 3,
        "stages_percent_below_sold_marker": "PERCENTAGE_NOT_SET",
        "stage_quantities": None,
    })
    gamma = payload["rows"][2]
    if named_crossing:
        gamma["current_protection_classification"] = "NAMED_EXCEPTION"
        gamma["low_exposure_decision"] = "NAMED_EXCEPTION"
    payload["summary"].update({
        "buyback_coverage_state_counts": {"LADDER_GAP": 2, "LEDGER_ONLY": 1},
        "low_exposure_decision_counts": {
            "REPAIR_REQUIRED": 1,
            "INTENTIONAL_MARKER_OR_CORE_HOLD": 1,
            "NAMED_EXCEPTION" if named_crossing else "EXIT_OR_NO_REENTRY_REVIEW": 1,
        },
        "percentage_ladders_with_supported_stages": 0,
        "percentage_not_set_rows": 3,
    })
    registry = {
        "updated_at": "2026-08-26T17:20:00+02:00",
        "accounts": {
            "5227886": {
                "positions": {
                    "1002": {
                        "instrument": "Example Beta",
                        "strategy_class": "CORE_COMPOUNDER",
                        "thesis": (
                            "The current quality-core exposure remains economically intentional "
                            "while its operating thesis is intact."
                        ),
                        "audit_status": "VALID_CURRENT_PLAN",
                        "bucket": "CORE_HOLD",
                        "stance": (
                            "Retain the current quality core because exiting would discard intact "
                            "long-term participation without a thesis break."
                        ),
                        "protection_classification": "CORE_HOLD_EXCEPTION",
                        "protection_reason": (
                            "The reviewed core intentionally has no active SELL while its quality "
                            "thesis and long-term participation remain intact."
                        ),
                        "next_gate": (
                            "Revalidate at earnings or sooner after a thesis-changing event, and "
                            "rebuild only after risk, capacity, and friction gates pass."
                        ),
                        "updated_at": "2026-08-26T17:20:00+02:00",
                    }
                }
            }
        },
    }
    path_rows = [
        r17_path_row(
            tenant_session_id="personal",
            account_id="5227886",
            orderbook_id="1001",
            instrument="Example Alpha",
            quantity=3,
            crossed=True,
        ),
        r17_path_row(
            tenant_session_id="darkcell",
            account_id="7616265",
            orderbook_id="1003",
            instrument="Example Gamma",
            quantity=4,
            crossed=named_crossing,
            named=named_crossing,
        ),
    ]
    return payload, registry, r17_path_payload(path_rows)


def sold_marker_reconciled_payloads():
    dynamic = dynamic_buyback_payload()
    dynamic["generated_at"] = "2026-08-19T00:20:00+02:00"
    dynamic["live_state_as_of"] = "2026-08-19T00:20:00+02:00"
    template = dynamic["rows"][1]
    rows = []

    def add_row(**updates):
        row = copy.deepcopy(template)
        row.update(updates)
        rows.append(row)

    add_row(
        tenant_session_id="personal",
        account_id="5227886",
        account_label="Personal",
        instrument="Marvell Technology",
        orderbook_id="3340",
        selection_reasons=["ONE_SHARE", "BELOW_20000_SEK", "SOLD_SLICE"],
        current_protection_classification="REPAIR_REQUIRED",
        low_exposure_decision="REPAIR_REQUIRED",
        buyback_coverage_state="REPAIR_REQUIRED",
        target_rebuild_quantity=11,
        coverage_reason="Complete post-sale path crossed two review bands without service; rebound does not erase the repair.",
        exact_next_gate="Wait for the event and a fresh regular-session higher low/reclaim; do not chase.",
    )
    add_row(
        tenant_session_id="darkcell",
        account_id="7616265",
        account_label="DarkCell",
        instrument="Fastly A",
        orderbook_id="956885",
        selection_reasons=["ONE_SHARE", "BELOW_20000_SEK", "RECENT_SAME_ACCOUNT_SALE"],
        current_protection_classification="REPAIR_REQUIRED",
        low_exposure_decision="REPAIR_REQUIRED",
        buyback_coverage_state="REPAIR_REQUIRED",
        target_rebuild_quantity=98,
        stages_percent_below_sold_marker=[11.1, 19.3, 25.4],
        stage_quantities=[33, 33, 32],
        latest_recent_sale_date="2026-08-10",
        coverage_reason="Stage 1 crossed without service; the later reclaim remains REPAIR_REQUIRED.",
        exact_next_gate="Confirm reclaim persistence and pass every risk, capacity, churn, spread, and friction gate.",
    )
    add_row(
        tenant_session_id="personal",
        account_id="5227886",
        account_label="Personal",
        instrument="SoundHound AI",
        orderbook_id="1393460",
        selection_reasons=["BELOW_20000_SEK", "SOLD_SLICE"],
        current_protection_classification="CORE_HOLD_EXCEPTION",
        low_exposure_decision="INTENTIONAL_MARKER_OR_CORE_HOLD",
        buyback_coverage_state="LADDER_GAP",
        target_rebuild_quantity=158,
        coverage_reason="Material complete-path gap remains PERCENTAGE_NOT_SET pending stock-specific evidence.",
        exact_next_gate="Set stages only after support, catalyst, risk, capacity, churn, spread, and friction pass.",
    )
    add_row(
        tenant_session_id="personal",
        account_id="5227886",
        account_label="Personal",
        instrument="Advanced Micro Devices",
        orderbook_id="529720",
        selection_reasons=["ONE_SHARE", "BELOW_20000_SEK", "SOLD_SLICE"],
        active_buy_volume=3,
        current_protection_classification="MARKER_EXCEPTION",
        low_exposure_decision="INTENTIONAL_MARKER_OR_CORE_HOLD",
        buyback_coverage_state="LEDGER_ONLY",
        target_rebuild_quantity=4,
        coverage_reason="Sale-attributed active BUY 3 from 2026-07-28 serves part of sold Antal 7; remaining 4 stays open.",
        exact_next_gate="Keep BUY 3 unchanged; remaining 4 requires a fresh base and full gate clearance.",
    )
    add_row(
        tenant_session_id="darkcell",
        account_id="7616265",
        account_label="DarkCell",
        instrument="Coinbase",
        orderbook_id="1211627",
        selection_reasons=["ONE_SHARE", "BELOW_20000_SEK", "SOLD_SLICE"],
        current_protection_classification="MARKER_EXCEPTION",
        low_exposure_decision="EXIT_OR_NO_REENTRY_REVIEW",
        buyback_coverage_state="LEDGER_ONLY",
        target_rebuild_quantity=None,
        latest_recent_sale_date="2026-07-13",
        no_reentry_decision=no_reentry_decision(),
        coverage_reason="Explicit no-reentry decision preserves the marker and closes the sold quantity under the current thesis.",
        exact_next_gate="Reopen only after a new catalyst, uptrend, risk, capacity, churn, and friction pass.",
    )
    dynamic["rows"] = rows
    dynamic["summary"] = {
        "exact_account_rows": 5,
        "personal_rows": 3,
        "darkcell_rows": 2,
        "current_one_share_rows": 0,
        "below_20000_sek_rows": 5,
        "at_or_above_20000_sek_rows": 0,
        "full_exit_rows": 0,
        "buyback_coverage_state_counts": dict(Counter(row["buyback_coverage_state"] for row in rows)),
        "low_exposure_decision_counts": dict(Counter(row["low_exposure_decision"] for row in rows)),
        "percentage_ladders_with_supported_stages": 1,
        "percentage_not_set_rows": 4,
        "pending_r6a_cleanup_rows": 0,
    }
    remediation_rows = [
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
        {
            "tenant_session_id": "darkcell",
            "account_id": "7616265",
            "instrument": "Coinbase",
            "orderbook_id": "1211627",
            "sale_date": "2026-07-13",
            "sold_quantity": 16,
            "sale_attributed_active_buy_quantity": 0,
            "remaining_open_quantity": 0,
            "state": "EXPLICIT_NO_REENTRY_CURRENT_THESIS",
            "no_reentry_decision": no_reentry_decision(),
        },
    ]

    for recovery in remediation_rows:
        tenant = recovery["tenant_session_id"]
        account = recovery["account_id"]
        orderbook = recovery["orderbook_id"]
        sale_date = recovery["sale_date"]
        lot_id = f"{tenant}-{account}-{orderbook}-{sale_date}-lot-1"
        sale_transaction_id = f"raw-sell-{tenant}-{account}-{orderbook}-{sale_date}-1"
        active_quantity = recovery["sale_attributed_active_buy_quantity"]
        decision = recovery.get("no_reentry_decision")
        closed_quantity = decision.get("closed_quantity", 0) if isinstance(decision, dict) else 0
        filled_quantity = (
            recovery["sold_quantity"]
            - active_quantity
            - closed_quantity
            - recovery["remaining_open_quantity"]
        )
        if isinstance(decision, dict):
            decision.update({
                "sale_lot_id": lot_id,
                "sale_transaction_id": sale_transaction_id,
                "sale_timestamp": f"{sale_date}T00:00:00+02:00",
            })
        fill_allocations = []
        if filled_quantity:
            fill_allocations.append({
                "allocation_id": f"fill-{tenant}-{account}-{orderbook}-{sale_date}-1",
                "buy_transaction_id": f"raw-buy-{tenant}-{account}-{orderbook}-{sale_date}-1",
                "buy_timestamp": f"{sale_date}T12:00:00+02:00",
                "source_quantity": filled_quantity,
                "sale_lot_id": lot_id,
                "quantity": filled_quantity,
            })
        active_allocations = []
        if active_quantity:
            active_allocations.append({
                "allocation_id": f"active-{tenant}-{account}-{orderbook}-{sale_date}-1",
                "stop_loss_id": f"stop-{tenant}-{account}-{orderbook}-{sale_date}-1",
                "source_quantity": active_quantity,
                "sale_lot_id": lot_id,
                "quantity": active_quantity,
                "strategy_intent": "SOLD_SLICE_RECOVERY",
            })
        recovery.update({
            "recovery_cycle_id": f"cycle-{tenant}-{account}-{orderbook}-2026q3",
            "cycle_boundary_evidence": {
                "source": "authenticated raw broker transaction chronology",
                "cycle_start": "2026-07-01T00:00:00+02:00",
                "cycle_end": "2026-08-19T00:15:00+02:00",
                "boundary_basis": "All exact-account transactions in the requested window were returned.",
                "exact_account_scope": True,
                "truncation_risk": False,
                "all_sale_transactions_in_cycle_included": True,
            },
            "raw_sale_transaction_count": 1,
            "raw_sale_quantity_total": recovery["sold_quantity"],
            "later_filled_quantity": filled_quantity,
            "closed_no_reentry_quantity": closed_quantity,
            "pre_sale_active_buy_quantity": 0,
            "unattributed_active_buy_quantity": 0,
            "unattributed_later_buy_quantity": 0,
            "sale_lots": [{
                "sale_lot_id": lot_id,
                "sale_transaction_id": sale_transaction_id,
                "sale_timestamp": f"{sale_date}T00:00:00+02:00",
                "sold_quantity": recovery["sold_quantity"],
                "qualifying_filled_quantity": filled_quantity,
                "active_recovery_quantity": active_quantity,
                "closed_no_reentry_quantity": closed_quantity,
                "remaining_open_quantity": recovery["remaining_open_quantity"],
                "state": recovery["state"],
                **({"no_reentry_decision": decision} if isinstance(decision, dict) else {}),
            }],
            "qualifying_fill_allocations": fill_allocations,
            "active_recovery_allocations": active_allocations,
            "pre_sale_active_buy_inventory": [],
            "unattributed_active_buy_inventory": [],
            "unattributed_later_buy_inventory": [],
        })

        dynamic_row = next(
            row
            for row in dynamic["rows"]
            if row["tenant_session_id"] == tenant and row["orderbook_id"] == orderbook
        )
        dynamic_row.update({
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "sale_lot_ids": [lot_id],
            "sale_attributed_active_buy_quantity": active_quantity,
            "pre_sale_active_buy_quantity": 0,
            "unattributed_active_buy_quantity": 0,
            "unattributed_later_buy_quantity": 0,
            "latest_recent_sale_date": sale_date,
        })
        if isinstance(decision, dict):
            dynamic_row["no_reentry_decision"] = copy.deepcopy(decision)

    remediation = {
        "artifact": "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE",
        "schema_version": 2,
        "status": "ACTIVE_REPAIR_REQUIRED",
        "generated_at": "2026-08-19T00:10:00+02:00",
        "verified_at": "2026-08-19T00:15:00+02:00",
        "path_snapshot_at": "2026-08-19T00:05:00+02:00",
        "sources": ["output/PORTFOLIO_RAW_TRANSACTION_RECOVERY_20260820_1046.json"],
        "authority": {"broker_mutation": False, "paper_mutation": False, "trade_authority": False},
        "controls": [
            "Evaluate the complete authenticated price path after every same-account sale, not only the latest quote.",
            "A rebound never erases a crossed but unserved stage or an unsupported material-path gap.",
            "Credit an active BUY only when durable metadata identifies the exact account, sale date or sold slice, intended quantity, and recovery intent.",
            "PERCENTAGE_NOT_SET is fail-closed and never complete coverage.",
            "An 8 percent sold-marker drawdown is a mandatory review alarm, not a universal ladder stage.",
            "Do not chase a rebound to conceal a missed crossing.",
            "No-reentry decisions expire and require exact-sale revalidation against newer evidence.",
            "Every unresolved sale lot persists until exact recovery or a structured terminal decision.",
            "Pre-sale and unattributed BUY inventory remains separate from sale-attributed recovery.",
            "Duplicate or overallocated transaction and stop allocations are rejected.",
        ],
        "summary": {
            "exact_account_rows_with_prior_same_account_sales": 5,
            "modeled_recovery_cycle_rows": 5,
            "modeled_sale_lots": 5,
            "multi_sale_recovery_cycle_rows": 0,
            "unmodeled_prior_sale_identity_count": 0,
            "multi_sale_governance_complete": True,
            "repair_required_missed_path_rows": 2,
            "percentage_not_set_open_rows": 1,
            "partial_sale_attributed_active_rows": 1,
            "explicit_no_reentry_rows": 1,
            "open_material_rows": 4,
            "remaining_open_quantity_across_material_rows": 271,
            "all_path_active_buy_attribution_gaps_after_registry_correction": 0,
            "silent_active_buy_attribution_gaps_in_material_rows": 0,
            "broker_mutations": 0,
        },
        "source_universe": {
            "full_path_identity_count": 5,
            "modeled_outside_full_path_identity_count": 0,
            "combined_prior_sale_identity_count": 5,
        },
        "verification": {
            "personal": {
                "tenant_session_id": "personal",
                "account_id": "5227886",
                "session_authenticated": True,
                "live_authorization_off": True,
                "recovery_reachability_unresolved": 0,
                "position_repair_required_orderbook_ids": ["3340"],
                "sold_cycle_repair_orderbook_ids": ["3340"],
            },
            "darkcell": {
                "tenant_session_id": "darkcell",
                "account_id": "7616265",
                "session_authenticated": True,
                "live_authorization_off": True,
                "recovery_reachability_unresolved": 0,
                "position_repair_required_orderbook_ids": ["956885"],
                "sold_cycle_repair_orderbook_ids": ["956885"],
            },
        },
        "rows": remediation_rows,
    }
    return dynamic, remediation


def promote_soundhound_to_governed_dormant_ladder(dynamic, remediation):
    soundhound = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1393460")
    soundhound.update({
        "current_protection_classification": "CORE_HOLD_EXCEPTION",
        "low_exposure_decision": "BUILD_REVIEW",
        "buyback_coverage_state": "LADDER_DORMANT",
        "target_rebuild_quantity": 158,
        "stages_percent_below_sold_marker": [8.2, 15.3],
        "stage_quantities": [79, 79],
        "coverage_reason": "SoundHound AI: the remaining same-sale slice has a stock-specific dormant two-stage review ladder.",
    })
    dynamic["summary"].update({
        "buyback_coverage_state_counts": dict(Counter(row["buyback_coverage_state"] for row in dynamic["rows"])),
        "low_exposure_decision_counts": dict(Counter(row["low_exposure_decision"] for row in dynamic["rows"])),
        "percentage_ladders_with_supported_stages": 2,
        "percentage_not_set_rows": 3,
    })
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "1393460")
    recovery.update({
        "state": "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED",
        "recorded_stage_percentages_below_marker": [8.2, 15.3],
        "recorded_stage_quantities": [79, 79],
    })
    remediation["summary"]["percentage_not_set_open_rows"] = 0


def add_second_amd_sale_lot(dynamic, remediation):
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "529720")
    second_lot = {
        "sale_lot_id": "personal-5227886-529720-2026-08-01-lot-2",
        "sale_transaction_id": "raw-sell-personal-5227886-529720-2026-08-01-2",
        "sale_timestamp": "2026-08-01T00:00:00+02:00",
        "sold_quantity": 5,
        "qualifying_filled_quantity": 0,
        "active_recovery_quantity": 0,
        "closed_no_reentry_quantity": 0,
        "remaining_open_quantity": 5,
        "state": "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET",
    }
    recovery["sale_lots"].append(second_lot)
    recovery.update({
        "sale_date": "2026-08-01",
        "sold_quantity": 12,
        "remaining_open_quantity": 9,
        "raw_sale_transaction_count": 2,
        "raw_sale_quantity_total": 12,
    })
    remediation["summary"].update({
        "modeled_sale_lots": 6,
        "multi_sale_recovery_cycle_rows": 1,
        "remaining_open_quantity_across_material_rows": 276,
    })
    dynamic_row = next(row for row in dynamic["rows"] if row["orderbook_id"] == "529720")
    dynamic_row.update({
        "target_rebuild_quantity": 9,
        "latest_recent_sale_date": "2026-08-01",
        "sale_lot_ids": [lot["sale_lot_id"] for lot in recovery["sale_lots"]],
    })


def test_buyback_validator_defaults_to_current_refresh_artifacts():
    assert PLAN_PATH.name == "PORTFOLIO_BUYBACK_LADDER_LIVE_REFRESH_20260806.json"
    assert TABLE_PATH.name == "PORTFOLIO_BUYBACK_LADDER_TABLE_20260806.md"


def test_dynamic_buyback_validator_accepts_variable_size_live_universe():
    payload = dynamic_buyback_payload()

    assert payload["summary"]["exact_account_rows"] == 3
    assert validate_dynamic_live_coverage(payload) == []


def test_schema4_dynamic_buyback_keeps_current_hold_orthogonal_to_recovery_gap():
    payload = dynamic_buyback_payload()
    payload["schema_version"] = 4
    payload["rows"] = payload["rows"][:2]
    payload["summary"].update({
        "exact_account_rows": 2,
        "personal_rows": 2,
        "darkcell_rows": 0,
        "current_one_share_rows": 1,
        "below_20000_sek_rows": 2,
        "full_exit_rows": 0,
        "buyback_coverage_state_counts": {
            "LADDER_ACTIVE": 1,
            "LEDGER_ONLY": 1,
        },
        "low_exposure_decision_counts": {
            "BUILD_REVIEW": 1,
            "INTENTIONAL_MARKER_OR_CORE_HOLD": 1,
        },
        "percentage_ladders_with_supported_stages": 1,
        "percentage_not_set_rows": 1,
    })
    for row in payload["rows"]:
        row["economic_resolution"] = {
            "state": row["low_exposure_decision"],
            "source": "CURRENT_REVIEWED_POSITION_STRATEGY",
            "reason": "Exact current strategy evidence supports this state.",
            "next_review": row["exact_next_gate"],
        }

    assert validate_dynamic_live_coverage(payload) == []

    payload["rows"][1]["buyback_coverage_state"] = "LADDER_GAP"
    payload["summary"]["buyback_coverage_state_counts"] = {
        "LADDER_ACTIVE": 1,
        "LADDER_GAP": 1,
    }
    assert validate_dynamic_live_coverage(payload) == []

    payload["rows"][1]["current_protection_classification"] = "REPAIR_REQUIRED"
    errors = validate_dynamic_live_coverage(payload)

    assert any("intentional hold lacks a reviewed hold protection class" in error for error in errors)


def test_r17_economic_enrichment_resolves_only_completed_reviewed_holds():
    payload = dynamic_buyback_payload()
    registry = {
        "updated_at": "2026-08-26T17:20:00+02:00",
        "accounts": {
            "5227886": {
                "positions": {
                    "1002": {
                        "instrument": "Example Beta",
                        "strategy_class": "CORE_COMPOUNDER",
                        "thesis": (
                            "The current quality-core exposure remains economically intentional "
                            "while its operating thesis is intact."
                        ),
                        "audit_status": "VALID_CURRENT_PLAN",
                        "bucket": "CORE_HOLD",
                        "stance": (
                            "Retain the current quality core because exiting would discard intact "
                            "long-term participation without a thesis break."
                        ),
                        "protection_classification": "CORE_HOLD_EXCEPTION",
                        "protection_reason": (
                            "The reviewed core intentionally has no active SELL while its quality "
                            "thesis and long-term participation remain intact."
                        ),
                        "next_gate": (
                            "Revalidate at earnings or sooner after a thesis-changing event, and "
                            "rebuild only after risk, capacity, and friction gates pass."
                        ),
                        "updated_at": "2026-08-26T17:20:00+02:00",
                    }
                }
            }
        },
    }

    result = enrich_payload(
        payload,
        registry,
        generated_at="2026-08-26T17:30:00+02:00",
        source_path="output/input.json",
    )

    rows = {row["orderbook_id"]: row for row in result["rows"]}
    assert result["schema_version"] == 9
    assert rows["1002"]["low_exposure_decision"] == "INTENTIONAL_MARKER_OR_CORE_HOLD"
    assert rows["1002"]["economic_resolution"]["revalidation_status"] == "CURRENT"
    assert rows["1002"]["economic_resolution"]["revalidation_due_by"] == "2026-09-02"
    assert rows["1001"]["low_exposure_decision"] == "BUILD_REVIEW"
    assert rows["1003"]["low_exposure_decision"] == "REPAIR_REQUIRED"
    assert rows["1003"]["economic_resolution"]["reason"].startswith("Example Gamma")
    assert result["summary"]["economically_resolved_rows"] == 2
    assert result["summary"]["economically_unresolved_rows"] == 1


def test_r17_economic_intent_remains_resolved_during_independent_protection_repair():
    payload, registry, path = schema5_inputs()
    registry["updated_at"] = "2026-08-28T19:54:00+02:00"
    position = registry["accounts"]["5227886"]["positions"]["1002"]
    position.update({
        "strategy_class": "SPECULATIVE",
        "thesis": (
            "The bounded position preserves meaningful upside optionality while "
            "the current tactical slice receives separate profit protection."
        ),
        "audit_status": "PROFIT_PROTECTION_REPAIR_REQUIRED",
        "bucket": "SPECULATIVE_PROFIT_PROTECTION_REPAIR",
        "stance": (
            "Retain five shares as the reviewed speculative core while repairing "
            "protection only on the separate tactical slice."
        ),
        "protection_classification": "REPAIR_REQUIRED",
        "protection_reason": (
            "The profitable tactical slice lacks its calibrated stop, while the "
            "reviewed core remains economically intentional."
        ),
        "next_gate": (
            "Revalidate the exact tactical stop after a live support and spread "
            "check; do not rebuild or exit the reviewed core without a thesis break."
        ),
        "recommendation": (
            "Retain the reviewed core and repair only the exact tactical protection gap."
        ),
        "proposed_correction": (
            "Preserve the core while keeping the missing tactical stop explicitly open."
        ),
        "updated_at": "2026-08-28T19:54:00+02:00",
    })

    result = enrich_payload(
        payload,
        registry,
        generated_at="2026-08-28T20:00:00+02:00",
        source_path="output/input.json",
        path_evidence=path,
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_test.json",
    )
    row = next(row for row in result["rows"] if row["orderbook_id"] == "1002")

    assert row["current_protection_classification"] == "REPAIR_REQUIRED"
    assert row["low_exposure_decision"] == "INTENTIONAL_MARKER_OR_CORE_HOLD"
    assert row["economic_resolution"]["source"] == (
        "CURRENT_REVIEWED_CORE_WITH_INDEPENDENT_PROTECTION_REPAIR"
    )
    assert row["economic_resolution"]["revalidation_status"] == "CURRENT"
    assert validate_dynamic_live_coverage(result) == []

    unstructured = copy.deepcopy(registry)
    unstructured_position = unstructured["accounts"]["5227886"]["positions"]["1002"]
    unstructured_position["stance"] = "Protection remains unresolved without an economic holding decision."
    unstructured_position["recommendation"] = "Recalculate the missing protection."
    unstructured_position["proposed_correction"] = "Repair the stop."
    fail_closed = enrich_payload(
        payload,
        unstructured,
        generated_at="2026-08-28T20:00:00+02:00",
        source_path="output/input.json",
        path_evidence=path,
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_test.json",
    )
    fail_closed_row = next(
        row for row in fail_closed["rows"] if row["orderbook_id"] == "1002"
    )
    assert fail_closed_row["low_exposure_decision"] == "REPAIR_REQUIRED"
    assert fail_closed["summary"]["economically_unresolved_rows"] == (
        result["summary"]["economically_unresolved_rows"] + 1
    )


def test_r17_small_hold_revalidation_expires_and_fails_closed():
    payload, registry, path = schema5_inputs()
    position = registry["accounts"]["5227886"]["positions"]["1002"]
    position.update({
        "thesis": "Example Beta remains an intentional quality-core exposure.",
        "stance": "Retain the reviewed core while its thesis remains intact.",
        "protection_reason": (
            "Example Beta remains deliberately held because a generic exit would discard "
            "the intact quality-core exposure without a thesis break."
        ),
        "next_gate": (
            "Rebuild only after earnings and a support reclaim clear risk, capacity, "
            "duplicate, and full-friction gates."
        ),
        "updated_at": "2026-08-14T17:20:00+02:00",
    })

    result = enrich_payload(
        payload,
        registry,
        generated_at="2026-08-28T19:00:00+02:00",
        source_path="output/input.json",
        path_evidence=path,
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_test.json",
    )

    row = next(row for row in result["rows"] if row["orderbook_id"] == "1002")
    resolution = row["economic_resolution"]
    assert row["low_exposure_decision"] == "REPAIR_REQUIRED"
    assert resolution["revalidation_status"] == "EXPIRED"
    assert resolution["revalidation_due_by"] == "2026-08-21"
    assert result["summary"]["small_hold_revalidation_status_counts"] == {
        "CURRENT": 0,
        "EXPIRED": 1,
        "MISSING": 0,
        "INVALID": 0,
    }
    assert validate_dynamic_live_coverage(result) == []


def test_r17_economic_enrichment_uses_registry_protection_without_erasing_sold_cycle_repair():
    payload, registry, path = schema5_inputs()
    alpha = payload["rows"][0]
    alpha["current_protection_classification"] = "REPAIR_REQUIRED"
    registry["accounts"]["5227886"]["positions"]["1001"] = {
        "instrument": "Example Alpha",
        "strategy_class": "OBSERVATION_MARKER",
        "thesis": "The current one-share marker is intentional.",
        "audit_status": "VALID_CURRENT_PLAN",
        "bucket": "MARKER_HOLD",
        "stance": "Retain the marker while the sold cycle remains separately open.",
        "protection_classification": "MARKER_EXCEPTION",
        "protection_reason": "The current one-share marker is economically indivisible.",
        "next_gate": "Review the marker only after a thesis-changing event.",
        "updated_at": "2026-08-28T18:45:00+02:00",
    }

    result = enrich_payload(
        payload,
        registry,
        generated_at="2026-08-28T19:00:00+02:00",
        source_path="output/input.json",
        path_evidence=path,
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_20260828_1843.json",
    )

    alpha = next(row for row in result["rows"] if row["orderbook_id"] == "1001")
    assert alpha["current_protection_classification"] == "MARKER_EXCEPTION"
    assert alpha["current_protection_source"] == "CURRENT_POSITION_STRATEGY_REGISTRY"
    assert alpha["superseded_embedded_protection_classification"] == "REPAIR_REQUIRED"
    assert alpha["low_exposure_decision"] == "INTENTIONAL_MARKER_OR_CORE_HOLD"
    assert alpha["buyback_coverage_state"] == "REPAIR_REQUIRED"
    assert alpha["full_path_evidence"]["crossed_8pct_review_alarm"] is True
    assert result["summary"]["economically_unresolved_rows"] == 1
    assert result["summary"]["repair_required_missed_path_rows"] == 1
    assert validate_dynamic_live_coverage(result) == []


def test_schema5_enrichment_binds_complete_path_and_preserves_missed_crossing():
    payload, registry, path = schema5_inputs()
    specific_reason = payload["rows"][0]["coverage_reason"]
    specific_gate = payload["rows"][0]["exact_next_gate"]
    result = enrich_payload(
        payload,
        registry,
        generated_at="2026-08-26T18:00:00+02:00",
        source_path="output/input.json",
        path_evidence=path,
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_20260826_1754.json",
    )

    rows = {row["orderbook_id"]: row for row in result["rows"]}
    assert result["schema_version"] == 9
    assert rows["1001"]["buyback_coverage_state"] == "REPAIR_REQUIRED"
    assert rows["1001"]["low_exposure_decision"] == "REPAIR_REQUIRED"
    assert rows["1001"]["current_protection_classification"] == "MARKER_EXCEPTION"
    assert rows["1001"]["full_path_evidence"]["path_state"] == "MISSED_PATH_REPAIR_REQUIRED"
    assert rows["1001"]["coverage_reason"].startswith(specific_reason)
    assert rows["1001"]["exact_next_gate"] == specific_gate
    assert rows["1001"]["instrument_specific_path_context"]["instrument"] == "Example Alpha"
    assert rows["1001"]["economic_resolution"]["instrument_specific_path_context"] == rows["1001"][
        "instrument_specific_path_context"
    ]
    assert rows["1003"]["buyback_coverage_state"] == "LADDER_GAP"
    assert result["summary"]["repair_required_missed_path_rows"] == 1
    assert result["summary"]["rows_crossing_8pct_review_alarm"] == 1
    assert validate_dynamic_live_coverage(result) == []
    assert validate_r17_open_path_evidence(path) == []

    remediation = {
        "artifact": "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE",
        "schema_version": 3,
        "rows": [
            {
                "tenant_session_id": "personal",
                "account_id": "5227886",
                "orderbook_id": "1001",
                "remaining_open_quantity": 3,
            },
            {
                "tenant_session_id": "darkcell",
                "account_id": "7616265",
                "orderbook_id": "1003",
                "remaining_open_quantity": 4,
            },
        ],
        "summary": {},
        "verification": {},
    }
    enriched_remediation = enrich_remediation_payload(
        remediation,
        path,
        generated_at="2026-08-26T18:00:00+02:00",
        source_path="output/remediation-input.json",
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_20260826_1754.json",
    )
    assert enriched_remediation["schema_version"] == 4
    assert validate_r17_path_links(result, enriched_remediation, path) == []


def test_complete_path_enrichment_is_repeatable_and_suffix_is_canonical():
    payload, registry, path = schema5_inputs()
    kwargs = {
        "generated_at": "2026-08-26T18:00:00+02:00",
        "source_path": "output/input.json",
        "path_evidence": path,
        "path_source_path": "output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_test.json",
    }

    first = enrich_payload(payload, registry, **kwargs)
    second = enrich_payload(copy.deepcopy(first), registry, **kwargs)
    first_alpha = next(row for row in first["rows"] if row["orderbook_id"] == "1001")
    second_alpha = next(row for row in second["rows"] if row["orderbook_id"] == "1001")

    assert second == first
    assert second_alpha["coverage_reason"] == first_alpha["coverage_reason"]
    assert second_alpha["coverage_reason"].count(
        " Complete authenticated path evidence records a maximum "
    ) == 1
    assert validate_dynamic_live_coverage(second) == []


def test_non_crossed_dormant_ladder_survives_dynamic_and_remediation_enrichment():
    dynamic, remediation = sold_marker_reconciled_payloads()
    promote_soundhound_to_governed_dormant_ladder(dynamic, remediation)
    dynamic["rows"] = [row for row in dynamic["rows"] if row["orderbook_id"] == "1393460"]
    remediation["rows"] = [
        row for row in remediation["rows"] if row["orderbook_id"] == "1393460"
    ]
    path = r17_path_payload([
        r17_path_row(
            tenant_session_id="personal",
            account_id="5227886",
            orderbook_id="1393460",
            instrument="SoundHound AI",
            quantity=158,
            crossed=False,
        )
    ])

    enriched_remediation = enrich_remediation_payload(
        remediation,
        path,
        generated_at="2026-08-26T18:10:00+02:00",
        source_path="output/remediation-input.json",
        path_source_path="output/path-input.json",
    )
    enriched_dynamic = enrich_payload(
        dynamic,
        {"updated_at": "2026-08-26T18:09:00+02:00", "accounts": {}},
        generated_at="2026-08-26T18:10:00+02:00",
        source_path="output/dynamic-input.json",
        path_evidence=path,
        path_source_path="output/path-input.json",
        remediation_payload=enriched_remediation,
    )
    dynamic_row = enriched_dynamic["rows"][0]
    remediation_row = enriched_remediation["rows"][0]

    assert dynamic_row["buyback_coverage_state"] == "LADDER_DORMANT"
    assert dynamic_row["low_exposure_decision"] == "BUILD_REVIEW"
    assert dynamic_row["stages_percent_below_sold_marker"] == [8.2, 15.3]
    assert remediation_row["state"] == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
    assert enriched_remediation["summary"]["dormant_stock_specific_review_ladder_rows"] == 1
    assert governed_dormant_ladder_count(enriched_dynamic, enriched_remediation) == 1


def test_non_crossed_independent_dormant_ladder_replaces_stale_remediation_gate():
    dynamic, remediation = sold_marker_reconciled_payloads()
    promote_soundhound_to_governed_dormant_ladder(dynamic, remediation)
    remediation["rows"] = [
        row for row in remediation["rows"] if row["orderbook_id"] == "1393460"
    ]
    recovery = remediation["rows"][0]
    open_lot_ids = [
        lot["sale_lot_id"]
        for lot in recovery["sale_lots"]
        if lot["remaining_open_quantity"] > 0
    ]
    next_review = "Review after a regular-session higher low and support reclaim."
    recovery.update({
        "next_gate": "All shares remain PERCENTAGE_NOT_SET pending review.",
        "dormant_ladder_decision": {
            "authority": "LOCAL_REVIEW_ONLY",
            "broker_mutation": False,
            "trade_authority": False,
            "state": "LADDER_DORMANT",
            "economic_state": "BUILD_REVIEW",
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "1393460",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "personal-soun-r43-independent-dormant-build",
            "exact_open_sale_lot_ids": open_lot_ids,
            "target_rebuild_quantity": 158,
            "stages_percent_below_sold_marker": [8.2, 15.3],
            "stage_quantities": [79, 79],
            "calibration_evidence": "The stages are calibrated to the exact sold marker and volatility.",
            "promotion_evidence": "Require a regular-session higher low and support reclaim.",
            "rejection_evidence": "Reject on thesis break, failed support, or excessive friction.",
            "next_review": next_review,
            "expires_at": "2026-09-04T22:00:00+02:00",
        },
    })
    path = r17_path_payload([
        r17_path_row(
            tenant_session_id="personal",
            account_id="5227886",
            orderbook_id="1393460",
            instrument="SoundHound AI",
            quantity=158,
            crossed=False,
        )
    ])

    enriched = enrich_remediation_payload(
        remediation,
        path,
        generated_at="2026-08-28T20:20:00+02:00",
        source_path="output/remediation-input.json",
        path_source_path="output/path-input.json",
    )
    row = enriched["rows"][0]

    assert row["state"] == "DORMANT_STOCK_SPECIFIC_REVIEW_LADDER_DEFINED"
    assert row["next_gate"] == next_review
    assert "PERCENTAGE_NOT_SET" not in row["next_gate"]
    errors = validate_sold_marker_remediation(enriched)
    assert not any("dormant ladder next gate" in error for error in errors)

    stale = copy.deepcopy(enriched)
    stale["rows"][0]["next_gate"] = "All shares remain PERCENTAGE_NOT_SET pending review."
    stale_errors = validate_sold_marker_remediation(stale)
    assert any("dormant ladder next gate is stale" in error for error in stale_errors)
    assert any("contradicts quantified coverage" in error for error in stale_errors)


def test_crossed_path_keeps_independent_dormant_build_intent_and_historical_repair():
    dynamic, remediation = sold_marker_reconciled_payloads()
    promote_soundhound_to_governed_dormant_ladder(dynamic, remediation)
    dynamic["rows"] = [row for row in dynamic["rows"] if row["orderbook_id"] == "1393460"]
    remediation["rows"] = [
        row for row in remediation["rows"] if row["orderbook_id"] == "1393460"
    ]
    recovery = remediation["rows"][0]
    open_lot_ids = [
        lot["sale_lot_id"]
        for lot in recovery["sale_lots"]
        if lot["remaining_open_quantity"] > 0
    ]
    recovery["dormant_ladder_decision"] = {
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "state": "LADDER_DORMANT",
        "economic_state": "BUILD_REVIEW",
        "tenant_session_id": "personal",
        "account_id": "5227886",
        "orderbook_id": "1393460",
        "recovery_cycle_id": recovery["recovery_cycle_id"],
        "decision_id": "personal-soun-r33-independent-dormant-build",
        "exact_open_sale_lot_ids": open_lot_ids,
        "target_rebuild_quantity": 158,
        "stages_percent_below_sold_marker": [8.2, 15.3],
        "stage_quantities": [79, 79],
        "calibration_evidence": "The two stages are calibrated to the exact sold marker and current volatility.",
        "promotion_evidence": "Require a regular-session higher low and support reclaim before promotion.",
        "rejection_evidence": "Reject on thesis break, failed support, factor breach, or excessive friction.",
        "next_review": "Review at the next regular session or after material issuer evidence.",
        "expires_at": "2026-09-04T22:00:00+02:00",
    }
    source_row = dynamic["rows"][0]
    source_row.update({
        "low_exposure_decision": "INTENTIONAL_MARKER_OR_CORE_HOLD",
        "buyback_coverage_state": "LADDER_GAP",
        "stages_percent_below_sold_marker": "PERCENTAGE_NOT_SET",
        "stage_quantities": None,
    })
    registry = {
        "updated_at": "2026-08-28T20:19:00+02:00",
        "accounts": {
            "5227886": {
                "positions": {
                    "1393460": {
                        "instrument": "SoundHound AI",
                        "strategy_class": "HIGH_BETA_GROWTH",
                        "thesis": "The current marker preserves exposure while the sold slice awaits a governed rebuild.",
                        "audit_status": "WAITING_REVERSAL",
                        "bucket": "MARKER_HOLD",
                        "stance": "Retain the current marker until the exact rebuild gate is met.",
                        "protection_classification": "CORE_HOLD_EXCEPTION",
                        "protection_reason": "The small current holding remains intentional while a stock-specific rebuild is reviewed.",
                        "next_gate": "Review after a regular-session higher low and support reclaim with risk and friction clearance.",
                        "updated_at": "2026-08-28T20:19:00+02:00",
                    }
                }
            }
        },
    }
    path = r17_path_payload([
        r17_path_row(
            tenant_session_id="personal",
            account_id="5227886",
            orderbook_id="1393460",
            instrument="SoundHound AI",
            quantity=158,
            crossed=True,
        )
    ])

    enriched_remediation = enrich_remediation_payload(
        remediation,
        path,
        generated_at="2026-08-28T20:20:00+02:00",
        source_path="output/remediation-input.json",
        path_source_path="output/path-input.json",
    )
    enriched_dynamic = enrich_payload(
        dynamic,
        registry,
        generated_at="2026-08-28T20:20:00+02:00",
        source_path="output/dynamic-input.json",
        path_evidence=path,
        path_source_path="output/path-input.json",
        remediation_payload=enriched_remediation,
    )
    dynamic_row = enriched_dynamic["rows"][0]
    remediation_row = enriched_remediation["rows"][0]

    assert remediation_row["state"] == "REPAIR_REQUIRED_MISSED_PATH"
    assert remediation_row["recorded_stage_percentages_below_marker"] == [8.2, 15.3]
    assert dynamic_row["buyback_coverage_state"] == "REPAIR_REQUIRED"
    assert dynamic_row["low_exposure_decision"] == "BUILD_REVIEW"
    assert dynamic_row["stages_percent_below_sold_marker"] == [8.2, 15.3]
    assert dynamic_row["dormant_ladder_decision"] == remediation_row["dormant_ladder_decision"]
    assert dynamic_row["open_sale_lot_ids"] == open_lot_ids
    expected_reason = (
        "SoundHound AI: The two stages are calibrated to the exact sold marker and current volatility."
    )
    assert dynamic_row["instrument_specific_path_context"]["coverage_reason"] == expected_reason
    assert dynamic_row["coverage_reason"].startswith(expected_reason)
    assert dynamic_row["economic_resolution"]["reason"] == expected_reason
    assert dynamic_row["economic_resolution"]["next_review"] == recovery[
        "dormant_ladder_decision"
    ]["next_review"]
    assert "hold_revalidation_required" not in dynamic_row["economic_resolution"]
    assert "revalidation_status" not in dynamic_row["economic_resolution"]
    assert "why_exit_is_currently_inferior" not in dynamic_row["economic_resolution"]
    assert "no supported" not in dynamic_row["coverage_reason"].lower()
    remediation_errors = validate_sold_marker_remediation(enriched_remediation)
    dynamic_errors = validate_dynamic_live_coverage(enriched_dynamic)
    reconciliation_errors = validate_dynamic_against_sold_marker_recovery(
        enriched_dynamic,
        enriched_remediation,
    )
    assert not any("independent dormant ladder" in error for error in remediation_errors)
    assert not any(
        "BUILD_REVIEW lacks" in error or "independent dormant ladder" in error
        for error in dynamic_errors
    )
    assert not any(
        "dormant ladder decision mismatch" in error
        or "repair row does not preserve" in error
        or "repair row independent dormant ladder" in error
        for error in reconciliation_errors
    )


def test_schema5_enrichment_rejects_missing_or_quantity_mismatched_path_row():
    payload, registry, path = schema5_inputs()
    missing = copy.deepcopy(path)
    missing["rows"] = missing["rows"][:1]
    missing["summary"].update({
        "exact_account_rows": 1,
        "unique_orderbooks": 1,
        "exact_open_sale_lots": 1,
        "remaining_open_quantity": 3,
        "rows_crossing_8pct_review_alarm": 1,
        "lots_crossing_8pct_review_alarm": 1,
    })
    with pytest.raises(ValueError, match="do not match dynamic open rows"):
        enrich_payload(
            payload,
            registry,
            generated_at="2026-08-26T18:00:00+02:00",
            source_path="output/input.json",
            path_evidence=missing,
            path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_test.json",
        )

    mismatched = copy.deepcopy(path)
    mismatched["rows"][0]["remaining_open_quantity"] = 4
    mismatched["rows"][0]["exact_lots"][0]["remaining_open_quantity"] = 4
    mismatched["summary"]["remaining_open_quantity"] += 1
    with pytest.raises(ValueError, match="target quantity mismatch"):
        enrich_payload(
            payload,
            registry,
            generated_at="2026-08-26T18:00:00+02:00",
            source_path="output/input.json",
            path_evidence=mismatched,
            path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_test.json",
        )


def test_schema5_named_crossing_stays_named_and_requires_named_review():
    payload, registry, path = schema5_inputs(named_crossing=True)
    specific_reason = payload["rows"][2]["coverage_reason"]
    specific_gate = payload["rows"][2]["exact_next_gate"]
    result = enrich_payload(
        payload,
        registry,
        generated_at="2026-08-26T18:00:00+02:00",
        source_path="output/input.json",
        path_evidence=path,
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_20260826_1754.json",
    )

    gamma = next(row for row in result["rows"] if row["orderbook_id"] == "1003")
    assert gamma["buyback_coverage_state"] == "LADDER_GAP"
    assert gamma["low_exposure_decision"] == "NAMED_EXCEPTION"
    assert gamma["full_path_evidence"]["path_state"] == "NAMED_EXCEPTION_PATH_REVIEW_REQUIRED"
    assert gamma["coverage_reason"].startswith(specific_reason)
    assert gamma["exact_next_gate"] == specific_gate
    assert gamma["instrument_specific_path_context"]["instrument"] == "Example Gamma"
    assert result["summary"]["named_exception_path_review_rows"] == 1
    assert validate_dynamic_live_coverage(result) == []


def test_schema6_validator_rejects_flattened_complete_path_decision():
    payload, registry, path = schema5_inputs()
    result = enrich_payload(
        payload,
        registry,
        generated_at="2026-08-26T18:00:00+02:00",
        source_path="output/input.json",
        path_evidence=path,
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_20260826_1754.json",
    )
    alpha = next(row for row in result["rows"] if row["orderbook_id"] == "1001")
    alpha.pop("instrument_specific_path_context")
    alpha["economic_resolution"].pop("instrument_specific_path_context")
    alpha["coverage_reason"] = "The complete path crossed a generic review alarm."
    alpha["exact_next_gate"] = "Reconcile thesis, technicals, capacity and friction."
    alpha["economic_resolution"]["reason"] = alpha["coverage_reason"]
    alpha["economic_resolution"]["next_review"] = alpha["exact_next_gate"]

    errors = validate_dynamic_live_coverage(result)

    assert any("instrument-specific path context missing" in error for error in errors)


def test_terminal_decision_closes_only_multi_sale_residuals_after_prior_fills():
    dynamic, remediation = sold_marker_reconciled_payloads()
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "1393460")
    dynamic_row = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1393460")
    first_lot = recovery["sale_lots"][0]
    second_lot = {
        "sale_lot_id": "personal-5227886-1393460-2026-08-15-lot-2",
        "sale_transaction_id": "raw-sell-personal-5227886-1393460-2026-08-15-2",
        "sale_timestamp": "2026-08-15T00:00:00+02:00",
        "sold_quantity": 20,
        "qualifying_filled_quantity": 0,
        "active_recovery_quantity": 0,
        "closed_no_reentry_quantity": 0,
        "remaining_open_quantity": 20,
        "state": "MATERIAL_PATH_OPEN_PERCENTAGE_NOT_SET",
    }
    recovery["sale_lots"].append(second_lot)
    recovery.update({
        "sale_date": "2026-08-15",
        "sold_quantity": 336,
        "raw_sale_transaction_count": 2,
        "raw_sale_quantity_total": 336,
        "remaining_open_quantity": 178,
    })
    remediation["summary"].update({
        "modeled_sale_lots": 6,
        "multi_sale_recovery_cycle_rows": 1,
        "remaining_open_quantity_across_material_rows": 291,
    })
    remediation["schema_version"] = 3
    for row in remediation["rows"]:
        row["non_recovery_buy_inventory"] = []
        row["non_recovery_buy_quantity"] = 0
        row["normalized_sale_quantity_total"] = row["sold_quantity"]
        row["raw_sale_quantity_total"] = row["sold_quantity"]
        for allocation in row["qualifying_fill_allocations"]:
            allocation["raw_source_quantity"] = allocation["source_quantity"]
            allocation["quantity_normalization_factor"] = 1
        for lot in row["sale_lots"]:
            lot["raw_sold_quantity"] = lot["sold_quantity"]
            lot["quantity_normalization_factor"] = 1
    existing_terminal = next(
        row for row in remediation["rows"] if row["orderbook_id"] == "1211627"
    )["no_reentry_decision"]
    existing_terminal["last_revalidated_at"] = "2026-08-26T18:34:00+02:00"
    existing_terminal["expires_at"] = "2026-09-08T18:34:00+02:00"
    dynamic_row.update({
        "target_rebuild_quantity": 178,
        "latest_recent_sale_date": "2026-08-15",
        "sale_lot_ids": [first_lot["sale_lot_id"], second_lot["sale_lot_id"]],
    })

    decisions = {
        "artifact": "PORTFOLIO_R17_TERMINAL_DECISIONS",
        "schema_version": 1,
        "generated_at": "2026-08-26T18:34:00+02:00",
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "rows": [{
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "1393460",
            "instrument": "SoundHound AI",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "personal-5227886-1393460-20260826-no-reentry",
            "decision_at": "2026-08-26T18:30:00+02:00",
            "last_revalidated_at": "2026-08-26T18:34:00+02:00",
            "expires_at": "2026-09-08T18:34:00+02:00",
            "decision_basis": "Current reviewed evidence rejects rebuilding the exact residual slices.",
            "thesis_evidence": "The current thesis supports retaining only the live holding.",
            "event_evidence": "Current issuer evidence was reviewed and does not promote re-entry.",
            "technical_evidence": "Current structure has not passed the required reversal gate.",
            "path_evidence": "Every immutable sale lot, prior fill and complete path was reviewed.",
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "current_holding": dynamic_row["live_holding"],
            "sale_lot_closures": [
                {
                    "sale_lot_id": first_lot["sale_lot_id"],
                    "sale_transaction_id": first_lot["sale_transaction_id"],
                    "sale_timestamp": first_lot["sale_timestamp"],
                    "remaining_open_quantity_to_close": 158,
                },
                {
                    "sale_lot_id": second_lot["sale_lot_id"],
                    "sale_transaction_id": second_lot["sale_transaction_id"],
                    "sale_timestamp": second_lot["sale_timestamp"],
                    "remaining_open_quantity_to_close": 20,
                },
            ],
        }],
    }

    decided = apply_terminal_decisions_to_remediation(
        remediation,
        decisions,
        generated_at="2026-08-26T18:35:00+02:00",
        decision_source_path="output/PORTFOLIO_R17_TERMINAL_DECISIONS_test.json",
    )
    decided_row = next(row for row in decided["rows"] if row["orderbook_id"] == "1393460")
    decided_lots = {lot["sale_lot_id"]: lot for lot in decided_row["sale_lots"]}

    assert decided_row["remaining_open_quantity"] == 0
    assert decided_row["closed_no_reentry_quantity"] == 178
    assert decided_row["no_reentry_decision"]["sold_quantity"] == 178
    assert decided_lots[first_lot["sale_lot_id"]]["qualifying_filled_quantity"] == 158
    first_decision = decided_lots[first_lot["sale_lot_id"]]["no_reentry_decision"]
    assert first_decision["original_sold_quantity"] == 316
    assert first_decision["recovered_before_decision_quantity"] == 158
    assert first_decision["sold_quantity"] == 158
    assert first_decision["closed_quantity"] == 158
    assert decided_lots[second_lot["sale_lot_id"]]["no_reentry_decision"]["sold_quantity"] == 20

    apply_terminal_decisions_to_dynamic_rows(dynamic["rows"], decided)
    assert dynamic_row["target_rebuild_quantity"] is None
    assert dynamic_row["low_exposure_decision"] == "INTENTIONAL_MARKER_OR_CORE_HOLD"
    assert dynamic_row["no_reentry_decision"] == decided_row["no_reentry_decision"]

    path_rows = []
    for row in remediation["rows"]:
        quantity = int(row.get("remaining_open_quantity", 0) or 0)
        if quantity <= 0:
            continue
        crossed = row["orderbook_id"] in {"3340", "956885"}
        path_row = r17_path_row(
            tenant_session_id=row["tenant_session_id"],
            account_id=row["account_id"],
            orderbook_id=row["orderbook_id"],
            instrument=row["instrument"],
            quantity=quantity,
            crossed=crossed,
        )
        path_row["active_buy_quantity"] = row["sale_attributed_active_buy_quantity"]
        path_row["sale_attributed_active_buy_quantity"] = row[
            "sale_attributed_active_buy_quantity"
        ]
        path_rows.append(path_row)
    original_path = r17_path_payload(path_rows)
    filtered_path = filter_path_evidence_to_open_remediation(
        original_path,
        decided,
        generated_at="2026-08-26T18:35:00+02:00",
        source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_input.json",
        decision_source_path="output/PORTFOLIO_R17_TERMINAL_DECISIONS_test.json",
    )
    assert "1393460" not in {row["orderbook_id"] for row in filtered_path["rows"]}

    enriched = enrich_remediation_payload(
        decided,
        filtered_path,
        generated_at="2026-08-26T18:35:00+02:00",
        source_path="output/PORTFOLIO_SOLD_MARKER_REMEDIATION_input.json",
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_output.json",
    )
    assert validate_sold_marker_remediation(enriched) == []
    remaining = enriched["summary"]["remaining_open_quantity_across_material_rows"]
    assert f"{remaining:,} still-open shares" in enriched["conclusion"]

    stale = copy.deepcopy(enriched)
    stale["conclusion"] = stale["conclusion"].replace(
        f"{remaining:,} still-open shares",
        f"{remaining + 1:,} still-open shares",
    )
    assert "sold-marker remediation conclusion contradicts still-open shares" in validate_sold_marker_remediation(
        stale
    )


def test_terminal_sold_cycle_allows_unrelated_active_inventory_and_preserves_core_intent():
    dynamic, remediation = sold_marker_reconciled_payloads()
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "1393460")
    dynamic_row = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1393460")
    lot = recovery["sale_lots"][0]
    recovery["pre_sale_active_buy_quantity"] = 2
    recovery["pre_sale_active_buy_inventory"] = [
        {"stop_loss_id": "unrelated-core-buy", "quantity": 2, "evidence": "Predates the sale."}
    ]
    dynamic_row.update({
        "active_buy_volume": 2,
        "pre_sale_active_buy_quantity": 2,
        "unattributed_active_buy_quantity": 0,
        "sale_attributed_active_buy_quantity": 0,
    })
    decisions = {
        "artifact": "PORTFOLIO_R17_TERMINAL_DECISIONS",
        "schema_version": 1,
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "rows": [{
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "1393460",
            "instrument": "SoundHound AI",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "terminal-sold-cycle-unrelated-core",
            "decision_at": "2026-08-27T18:00:00+02:00",
            "last_revalidated_at": "2026-08-27T18:05:00+02:00",
            "expires_at": "2026-09-09T18:05:00+02:00",
            "decision_basis": "Close only the exact unresolved sold-cycle quantity.",
            "thesis_evidence": "The current core remains independently intentional.",
            "event_evidence": "Current issuer evidence was reviewed.",
            "technical_evidence": "The sold-cycle recovery gate remains rejected.",
            "path_evidence": "The complete authenticated sold-cycle path was reviewed.",
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "current_holding": dynamic_row["live_holding"],
            "sale_lot_closures": [{
                "sale_lot_id": lot["sale_lot_id"],
                "sale_transaction_id": lot["sale_transaction_id"],
                "sale_timestamp": lot["sale_timestamp"],
                "remaining_open_quantity_to_close": lot["remaining_open_quantity"],
            }],
        }],
    }

    decided = apply_terminal_decisions_to_remediation(
        remediation,
        decisions,
        generated_at="2026-08-27T18:06:00+02:00",
        decision_source_path="output/terminal-unrelated-core.json",
    )
    apply_terminal_decisions_to_dynamic_rows(dynamic["rows"], decided)

    assert dynamic_row["buyback_coverage_state"] == "LEDGER_ONLY"
    assert dynamic_row["low_exposure_decision"] == "INTENTIONAL_MARKER_OR_CORE_HOLD"
    assert dynamic_row["active_buy_volume"] == 2
    assert dynamic_row["pre_sale_active_buy_quantity"] == 2
    assert dynamic_row["sale_attributed_active_buy_quantity"] == 0

    contradicted = copy.deepcopy(decided)
    contradicted_row = next(
        row for row in contradicted["rows"] if row["orderbook_id"] == "1393460"
    )
    contradicted_row["sale_attributed_active_buy_quantity"] = 1
    contradicted_dynamic = copy.deepcopy(dynamic["rows"])
    next(
        row for row in contradicted_dynamic if row["orderbook_id"] == "1393460"
    )["active_buy_volume"] = 3
    with pytest.raises(ValueError, match="contradictory same-sale BUY inventory"):
        apply_terminal_decisions_to_dynamic_rows(contradicted_dynamic, contradicted)


def test_r19_partial_terminal_decision_reports_cumulative_closure_count():
    dynamic, remediation = sold_marker_reconciled_payloads()
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "1393460")
    dynamic_row = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1393460")
    open_lot = recovery["sale_lots"][0]
    prior_lot_id = "personal-5227886-1393460-2026-08-15-prior-closed"
    prior_transaction_id = "raw-sell-personal-5227886-1393460-2026-08-15-prior-closed"
    prior_decision = {
        "decision_id": "r19-prior-closure",
        "parent_decision_id": "r19-prior-parent",
        "tenant_session_id": "personal",
        "account_id": "5227886",
        "orderbook_id": "1393460",
        "sale_date": "2026-08-15",
        "sale_lot_id": prior_lot_id,
        "sale_transaction_id": prior_transaction_id,
        "sale_timestamp": "2026-08-15T00:00:00+02:00",
        "original_sold_quantity": 10,
        "recovered_before_decision_quantity": 0,
        "sold_quantity": 10,
        "closed_quantity": 10,
        "remaining_after_decision_quantity": 0,
        "decision_at": "2026-08-20T18:00:00+02:00",
        "last_revalidated_at": "2026-08-20T18:00:00+02:00",
        "expires_at": "2026-09-02T18:00:00+02:00",
        "decision_basis": "A prior exact lot was closed under a dated review.",
        "thesis_evidence": "The prior lot was independently reviewed.",
        "event_evidence": "Current issuer evidence was reviewed.",
        "technical_evidence": "The prior lot did not pass its re-entry gate.",
        "path_evidence": "The authenticated path for the prior lot was reviewed.",
        "newer_evidence_reviewed": True,
        "contradiction_status": "NONE",
    }
    recovery["sale_lots"].append({
        "sale_lot_id": prior_lot_id,
        "sale_transaction_id": prior_transaction_id,
        "sale_timestamp": "2026-08-15T00:00:00+02:00",
        "sold_quantity": 10,
        "qualifying_filled_quantity": 0,
        "active_recovery_quantity": 0,
        "closed_no_reentry_quantity": 10,
        "remaining_open_quantity": 0,
        "state": "EXPLICIT_NO_REENTRY_CURRENT_THESIS",
        "terminal_closure_decisions": [prior_decision],
    })
    recovery.update({
        "sale_date": "2026-08-15",
        "sold_quantity": 326,
        "raw_sale_transaction_count": 2,
        "raw_sale_quantity_total": 326,
        "closed_no_reentry_quantity": 10,
    })
    decisions = {
        "artifact": "PORTFOLIO_R17_TERMINAL_DECISIONS",
        "schema_version": 2,
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "rows": [{
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "1393460",
            "instrument": "SoundHound AI",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "r19-current-partial-closure",
            "decision_at": "2026-08-28T21:00:00+02:00",
            "last_revalidated_at": "2026-08-28T21:00:00+02:00",
            "expires_at": "2026-09-10T21:00:00+02:00",
            "decision_basis": "Close only half of the untouched open lot.",
            "thesis_evidence": "Current exposure remains independently governed.",
            "event_evidence": "Current issuer evidence was reviewed.",
            "technical_evidence": "The old recovery obligation is not promoted.",
            "path_evidence": "The complete authenticated path was reviewed.",
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "current_holding": dynamic_row["live_holding"],
            "qualifying_fill_reallocations": [],
            "active_recovery_allocations": [],
            "sale_lot_closures": [{
                "closure_id": "r19-current-partial-lot-closure",
                "sale_lot_id": open_lot["sale_lot_id"],
                "sale_transaction_id": open_lot["sale_transaction_id"],
                "sale_timestamp": open_lot["sale_timestamp"],
                "remaining_open_quantity_to_close": 79,
            }],
        }],
    }

    decided = apply_terminal_decisions_to_remediation(
        remediation,
        decisions,
        generated_at="2026-08-28T21:01:00+02:00",
        decision_source_path="output/r19-current-partial-closure.json",
    )
    decided_row = next(row for row in decided["rows"] if row["orderbook_id"] == "1393460")
    cumulative_count = sum(
        len(lot.get("terminal_closure_decisions", []))
        for lot in decided_row["sale_lots"]
    )

    assert decided_row["remaining_open_quantity"] == 79
    assert cumulative_count == 2
    assert decided_row["mixed_lot_resolution"]["terminal_closure_count"] == cumulative_count

    apply_terminal_decisions_to_dynamic_rows(dynamic["rows"], decided)
    assert dynamic_row["mixed_lot_resolution"] == decided_row["mixed_lot_resolution"]

    follow_up = copy.deepcopy(decisions)
    follow_up_row = follow_up["rows"][0]
    follow_up_row.update({
        "decision_id": "r19-follow-up-same-lot-closure",
        "decision_at": "2026-08-28T21:02:00+02:00",
        "last_revalidated_at": "2026-08-28T21:02:00+02:00",
        "decision_basis": "Close the exact remaining half after a fresh, separately identified review.",
    })
    follow_up_row["sale_lot_closures"] = [{
        "closure_id": "r19-follow-up-same-lot-closure-id",
        "sale_lot_id": open_lot["sale_lot_id"],
        "sale_transaction_id": open_lot["sale_transaction_id"],
        "sale_timestamp": open_lot["sale_timestamp"],
        "remaining_open_quantity_to_close": 79,
    }]

    fully_decided = apply_terminal_decisions_to_remediation(
        decided,
        follow_up,
        generated_at="2026-08-28T21:03:00+02:00",
        decision_source_path="output/r19-follow-up-same-lot-closure.json",
    )
    fully_decided_row = next(
        row for row in fully_decided["rows"] if row["orderbook_id"] == "1393460"
    )
    decided_open_lot = next(
        lot
        for lot in fully_decided_row["sale_lots"]
        if lot["sale_lot_id"] == open_lot["sale_lot_id"]
    )
    all_closure_ids = [
        closure["decision_id"]
        for lot in fully_decided_row["sale_lots"]
        for closure in lot.get("terminal_closure_decisions", [])
    ]

    assert fully_decided_row["remaining_open_quantity"] == 0
    assert fully_decided_row["state"] == "EXPLICIT_NO_REENTRY_CURRENT_THESIS"
    assert decided_open_lot["closed_no_reentry_quantity"] == 158
    assert len(decided_open_lot["terminal_closure_decisions"]) == 2
    assert fully_decided_row.get("partial_terminal_decisions", []) == []
    assert fully_decided_row["no_reentry_decision"]["sale_lot_decision_ids"] == all_closure_ids
    assert fully_decided_row["mixed_lot_resolution"]["terminal_closure_count"] == 3
    validation_errors = validate_sold_marker_remediation(fully_decided)
    assert not any("requires one exact terminal closure" in error for error in validation_errors)
    sequential_parity_errors = [
        error
        for error in validation_errors
        if any(
            marker in error
            for marker in (
                "recovered-before-decision quantity does not match prior allocations",
                "terminal decision remaining quantity does not reconcile",
                "terminal decision residual differs from the current sale lot",
            )
        )
    ]
    assert sequential_parity_errors == []

    invalid_sequence = copy.deepcopy(fully_decided)
    invalid_lot = next(
        lot
        for row in invalid_sequence["rows"]
        if row["orderbook_id"] == "1393460"
        for lot in row["sale_lots"]
        if lot["sale_lot_id"] == open_lot["sale_lot_id"]
    )
    invalid_lot["terminal_closure_decisions"][1][
        "recovered_before_decision_quantity"
    ] -= 79
    invalid_errors = validate_sold_marker_remediation(invalid_sequence)
    assert any(
        "recovered-before-decision quantity does not match prior allocations" in error
        for error in invalid_errors
    )

    repeated = apply_terminal_decisions_to_remediation(
        fully_decided,
        follow_up,
        generated_at="2026-08-28T21:03:00+02:00",
        decision_source_path="output/r19-follow-up-same-lot-closure.json",
    )
    assert repeated == fully_decided


def test_r19_reallocates_fill_attributes_full_stop_and_preserves_exact_residual():
    dynamic, remediation = sold_marker_reconciled_payloads()
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "1393460")
    dynamic_row = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1393460")
    first_lot = recovery["sale_lots"][0]
    second_lot = {
        "sale_lot_id": "personal-5227886-1393460-2026-08-15-lot-2",
        "sale_transaction_id": "raw-sell-personal-5227886-1393460-2026-08-15-2",
        "sale_timestamp": "2026-08-15T00:00:00+02:00",
        "sold_quantity": 20,
        "qualifying_filled_quantity": 0,
        "active_recovery_quantity": 0,
        "closed_no_reentry_quantity": 0,
        "remaining_open_quantity": 20,
        "state": "OPEN_UNRECOVERED",
    }
    recovery["sale_lots"].append(second_lot)
    recovery.update({
        "sale_date": "2026-08-15",
        "sold_quantity": 336,
        "raw_sale_transaction_count": 2,
        "raw_sale_quantity_total": 336,
        "remaining_open_quantity": 178,
        "unattributed_active_buy_quantity": 2,
        "unattributed_active_buy_inventory": [
            {"stop_loss_id": "stop-r19-soundhound", "quantity": 2, "evidence": "Reviewed exact stop."}
        ],
    })
    remediation["summary"].update({
        "modeled_sale_lots": 6,
        "multi_sale_recovery_cycle_rows": 1,
        "remaining_open_quantity_across_material_rows": 291,
    })
    for row in remediation["rows"]:
        row["non_recovery_buy_inventory"] = []
        row["non_recovery_buy_quantity"] = 0
        row["normalized_sale_quantity_total"] = row["sold_quantity"]
        row["raw_sale_quantity_total"] = row["sold_quantity"]
        for allocation in row["qualifying_fill_allocations"]:
            allocation["raw_source_quantity"] = allocation["source_quantity"]
            allocation["quantity_normalization_factor"] = 1
        for lot in row["sale_lots"]:
            lot["raw_sold_quantity"] = lot["sold_quantity"]
            lot["quantity_normalization_factor"] = 1
    existing_terminal = next(
        row for row in remediation["rows"] if row["orderbook_id"] == "1211627"
    )["no_reentry_decision"]
    existing_terminal["last_revalidated_at"] = "2026-08-27T18:00:00+02:00"
    existing_terminal["expires_at"] = "2026-09-09T18:00:00+02:00"
    dynamic_terminal = next(
        row for row in dynamic["rows"] if row["orderbook_id"] == "1211627"
    )["no_reentry_decision"]
    dynamic_terminal.update(copy.deepcopy(existing_terminal))
    dynamic_row.update({
        "active_buy_volume": 2,
        "target_rebuild_quantity": 178,
        "latest_recent_sale_date": "2026-08-15",
        "sale_lot_ids": [first_lot["sale_lot_id"], second_lot["sale_lot_id"]],
        "unattributed_active_buy_quantity": 2,
    })

    source_allocation = recovery["qualifying_fill_allocations"][0]
    source_allocation["buy_timestamp"] = "2026-08-16T12:00:00+02:00"
    decisions = {
        "artifact": "PORTFOLIO_R17_TERMINAL_DECISIONS",
        "schema_version": 2,
        "generated_at": "2026-08-27T18:05:00+02:00",
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "rows": [{
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "1393460",
            "instrument": "SoundHound AI",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "r19-personal-soundhound-mixed-lot",
            "decision_at": "2026-08-27T18:00:00+02:00",
            "last_revalidated_at": "2026-08-27T18:05:00+02:00",
            "expires_at": "2026-09-09T18:05:00+02:00",
            "decision_basis": "Exact chronology assigns current recovery to the latest sold slice.",
            "thesis_evidence": "The current holding remains intentional while only the latest residual is reviewable.",
            "event_evidence": "Current issuer evidence does not revive the stale earlier residual.",
            "technical_evidence": "The latest slice retains a stock-specific reclaim gate.",
            "path_evidence": "Every immutable lot and the complete authenticated path were reviewed.",
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "current_holding": dynamic_row["live_holding"],
            "qualifying_fill_reallocations": [{
                "reallocation_id": "r19-fill-soundhound-latest",
                "buy_transaction_id": source_allocation["buy_transaction_id"],
                "source_allocation_id": source_allocation["allocation_id"],
                "source_sale_lot_id": first_lot["sale_lot_id"],
                "target_sale_lot_id": second_lot["sale_lot_id"],
                "quantity": 2,
            }],
            "active_recovery_allocations": [{
                "allocation_id": "r19-active-soundhound-latest",
                "stop_loss_id": "stop-r19-soundhound",
                "source_quantity": 2,
                "sale_lot_id": second_lot["sale_lot_id"],
                "quantity": 2,
                "strategy_intent": "SOLD_SLICE_RECOVERY",
                "strategy_reason": "Attribute the exact current stop to the latest reviewed sold slice.",
            }],
            "sale_lot_closures": [{
                "closure_id": "r19-close-soundhound-stale-lot",
                "sale_lot_id": first_lot["sale_lot_id"],
                "sale_transaction_id": first_lot["sale_transaction_id"],
                "sale_timestamp": first_lot["sale_timestamp"],
                "remaining_open_quantity_to_close": 160,
            }],
        }],
    }

    decided = apply_terminal_decisions_to_remediation(
        remediation,
        decisions,
        generated_at="2026-08-27T18:06:00+02:00",
        decision_source_path="output/PORTFOLIO_R19_MIXED_LOT_DECISIONS_test.json",
    )
    decided_row = next(row for row in decided["rows"] if row["orderbook_id"] == "1393460")
    decided_lots = {lot["sale_lot_id"]: lot for lot in decided_row["sale_lots"]}
    assert decided_row["later_filled_quantity"] == 158
    assert decided_row["sale_attributed_active_buy_quantity"] == 2
    assert decided_row["unattributed_active_buy_quantity"] == 0
    assert decided_row["closed_no_reentry_quantity"] == 160
    assert decided_row["remaining_open_quantity"] == 16
    assert decided_lots[first_lot["sale_lot_id"]]["remaining_open_quantity"] == 0
    assert decided_lots[second_lot["sale_lot_id"]]["remaining_open_quantity"] == 16

    path_rows = []
    for row in remediation["rows"]:
        open_lots = [lot for lot in row["sale_lots"] if lot["remaining_open_quantity"] > 0]
        if not open_lots:
            continue
        exact_lots = []
        for index, lot in enumerate(open_lots):
            crossed = row["orderbook_id"] in {"3340", "956885"} or (
                row["orderbook_id"] == "1393460" and index == 0
            )
            exact_lots.append({
                "sale_lot_id": lot["sale_transaction_id"],
                "sale_transaction_id": lot["sale_transaction_id"],
                "sale_date": lot["sale_timestamp"][:10],
                "remaining_open_quantity": lot["remaining_open_quantity"],
                "comparable_sale_marker": 105.0,
                "maximum_drop_below_marker_percent": 12.5 if crossed else 5.0,
                "crossed_8pct_review_alarm": crossed,
            })
        quantity = sum(lot["remaining_open_quantity"] for lot in exact_lots)
        crossed_lots = sum(lot["crossed_8pct_review_alarm"] for lot in exact_lots)
        path_row = r17_path_row(
            tenant_session_id=row["tenant_session_id"],
            account_id=row["account_id"],
            orderbook_id=row["orderbook_id"],
            instrument=row["instrument"],
            quantity=quantity,
            crossed=crossed_lots > 0,
        )
        path_row.update({
            "remaining_open_lot_count": len(exact_lots),
            "maximum_open_lot_drop_percent": max(
                lot["maximum_drop_below_marker_percent"] for lot in exact_lots
            ),
            "open_lots_crossing_8pct_alarm": crossed_lots,
            "active_buy_quantity": sum(
                int(row.get(field, 0) or 0)
                for field in (
                    "sale_attributed_active_buy_quantity",
                    "pre_sale_active_buy_quantity",
                    "unattributed_active_buy_quantity",
                )
            ),
            "sale_attributed_active_buy_quantity": row["sale_attributed_active_buy_quantity"],
            "exact_lots": exact_lots,
        })
        path_rows.append(path_row)
    original_path = r17_path_payload(path_rows)
    original_path["generated_at"] = "2026-08-27T18:04:00+02:00"
    filtered_path = filter_path_evidence_to_open_remediation(
        original_path,
        decided,
        generated_at="2026-08-27T18:06:00+02:00",
        source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_input.json",
        decision_source_path="output/PORTFOLIO_R19_MIXED_LOT_DECISIONS_test.json",
    )
    filtered_soundhound = next(
        row for row in filtered_path["rows"] if row["orderbook_id"] == "1393460"
    )
    assert filtered_soundhound["remaining_open_quantity"] == 16
    assert filtered_soundhound["remaining_open_lot_count"] == 1
    assert filtered_soundhound["sale_attributed_active_buy_quantity"] == 2
    assert filtered_soundhound["crossed_8pct_review_alarm"] is False
    assert validate_r17_open_path_evidence(filtered_path) == []

    stale_context = {
        "instrument": "SoundHound AI",
        "coverage_reason": "SoundHound AI had an older crossed lot.",
        "exact_next_gate": "Repair the older crossed lot.",
        "remaining_open_quantity": 178,
        "remaining_open_lot_count": 2,
        "maximum_open_lot_drop_percent": 12.5,
        "current_drop_below_weighted_marker_percent": 10.0,
    }
    dynamic_row["instrument_specific_path_context"] = copy.deepcopy(stale_context)
    dynamic_row.setdefault("economic_resolution", {})[
        "instrument_specific_path_context"
    ] = copy.deepcopy(stale_context)
    apply_terminal_decisions_to_dynamic_rows(dynamic["rows"], decided)
    assert dynamic_row["target_rebuild_quantity"] == 16
    assert dynamic_row["sale_attributed_active_buy_quantity"] == 2
    assert dynamic_row["unattributed_active_buy_quantity"] == 0
    assert dynamic_row["low_exposure_decision"] != "EXIT_OR_NO_REENTRY_REVIEW"
    assert "instrument_specific_path_context" not in dynamic_row
    assert "instrument_specific_path_context" not in dynamic_row["economic_resolution"]

    isolated_dynamic = copy.deepcopy(dynamic)
    isolated_dynamic["rows"] = [
        row for row in isolated_dynamic["rows"] if row["orderbook_id"] == "1393460"
    ]
    isolated_decided = copy.deepcopy(decided)
    isolated_decided["rows"] = [
        row for row in isolated_decided["rows"] if row["orderbook_id"] == "1393460"
    ]
    isolated_path = r17_path_payload([copy.deepcopy(filtered_soundhound)])
    isolated_path["generated_at"] = filtered_path["generated_at"]
    isolated_soundhound = isolated_dynamic["rows"][0]
    isolated_soundhound["instrument_specific_path_context"] = copy.deepcopy(stale_context)
    isolated_soundhound.setdefault("economic_resolution", {})[
        "instrument_specific_path_context"
    ] = copy.deepcopy(stale_context)

    enriched = enrich_payload(
        isolated_dynamic,
        {"updated_at": "2026-08-27T18:06:00+02:00", "accounts": {}},
        generated_at="2026-08-27T18:07:00+02:00",
        source_path="output/PORTFOLIO_BUYBACK_LIVE_COVERAGE_input.json",
        path_evidence=isolated_path,
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_filtered.json",
        remediation_payload=isolated_decided,
    )
    enriched_soundhound = next(
        row for row in enriched["rows"] if row["orderbook_id"] == "1393460"
    )
    assert enriched["schema_version"] == 9
    assert enriched_soundhound["full_path_evidence"]["crossed_8pct_review_alarm"] is False
    assert "instrument_specific_path_context" not in enriched_soundhound
    assert "instrument_specific_path_context" not in enriched_soundhound["economic_resolution"]

    stale = copy.deepcopy(enriched)
    stale_soundhound = next(row for row in stale["rows"] if row["orderbook_id"] == "1393460")
    stale_soundhound["instrument_specific_path_context"] = copy.deepcopy(stale_context)
    stale_soundhound["economic_resolution"]["instrument_specific_path_context"] = copy.deepcopy(
        stale_context
    )
    errors = validate_dynamic_live_coverage(stale)
    assert any("noncrossed complete path retains stale" in error for error in errors)
    assert any("noncrossed economic resolution retains stale" in error for error in errors)


def test_r31_merges_into_existing_target_allocation_and_is_idempotent():
    _, remediation = sold_marker_reconciled_payloads()
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "1393460")
    first_lot = recovery["sale_lots"][0]
    second_lot = {
        "sale_lot_id": "personal-5227886-1393460-2026-08-15-lot-2",
        "sale_transaction_id": "raw-sell-personal-5227886-1393460-2026-08-15-2",
        "sale_timestamp": "2026-08-15T00:00:00+02:00",
        "sold_quantity": 20,
        "qualifying_filled_quantity": 3,
        "active_recovery_quantity": 0,
        "closed_no_reentry_quantity": 0,
        "remaining_open_quantity": 17,
        "state": "PARTIALLY_RECOVERED_OPEN",
    }
    recovery["sale_lots"].append(second_lot)
    source = recovery["qualifying_fill_allocations"][0]
    source.update({
        "source_quantity": 161,
        "buy_timestamp": "2026-08-16T12:00:00+02:00",
    })
    target = copy.deepcopy(source)
    target.update({
        "allocation_id": "existing-target-allocation",
        "sale_lot_id": second_lot["sale_lot_id"],
        "quantity": 3,
    })
    recovery["qualifying_fill_allocations"].append(target)
    recovery.update({
        "sale_date": "2026-08-15",
        "sold_quantity": 336,
        "raw_sale_transaction_count": 2,
        "raw_sale_quantity_total": 336,
        "later_filled_quantity": 161,
        "remaining_open_quantity": 175,
    })
    first_lot.update({
        "qualifying_filled_quantity": 158,
        "remaining_open_quantity": 158,
    })
    decisions = {
        "artifact": "PORTFOLIO_R17_TERMINAL_DECISIONS",
        "schema_version": 3,
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "rows": [{
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "1393460",
            "instrument": "SoundHound AI",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "r31-existing-target-merge",
            "decision_at": "2026-08-28T18:00:00+02:00",
            "last_revalidated_at": "2026-08-28T18:05:00+02:00",
            "expires_at": "2026-09-10T18:05:00+02:00",
            "decision_basis": "Merge exact fill quantity into its existing later-lot allocation.",
            "thesis_evidence": "The current exposure intent remains independent.",
            "event_evidence": "Current issuer evidence was reviewed.",
            "technical_evidence": "The exact lot chronology controls attribution.",
            "path_evidence": "The complete authenticated multi-sale path was reviewed.",
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "current_holding": 1,
            "qualifying_fill_reallocations": [{
                "reallocation_id": "r31-merge-six",
                "buy_transaction_id": source["buy_transaction_id"],
                "source_allocation_id": source["allocation_id"],
                "source_sale_lot_id": first_lot["sale_lot_id"],
                "target_sale_lot_id": second_lot["sale_lot_id"],
                "target_allocation_id": target["allocation_id"],
                "target_quantity_before": 3,
                "target_quantity_after": 9,
                "quantity": 6,
            }],
            "active_recovery_allocations": [],
            "sale_lot_closures": [],
        }],
    }

    decided = apply_terminal_decisions_to_remediation(
        remediation,
        decisions,
        generated_at="2026-08-28T18:06:00+02:00",
        decision_source_path="output/r31-existing-target.json",
    )
    decided_row = next(row for row in decided["rows"] if row["orderbook_id"] == "1393460")
    allocations = decided_row["qualifying_fill_allocations"]
    decided_source = next(row for row in allocations if row["allocation_id"] == source["allocation_id"])
    decided_target = next(row for row in allocations if row["allocation_id"] == target["allocation_id"])

    assert decided_source["quantity"] == 152
    assert decided_target["quantity"] == 9
    assert decided_target["reviewed_reallocation_ids"] == ["r31-merge-six"]
    assert decided_row["remaining_open_quantity"] == 175
    assert decided_row["mixed_lot_resolution"]["schema_version"] == 3
    assert len({
        (row["buy_transaction_id"], row["sale_lot_id"])
        for row in allocations
    }) == len(allocations)

    repeated = apply_terminal_decisions_to_remediation(
        decided,
        decisions,
        generated_at="2026-08-28T18:06:00+02:00",
        decision_source_path="output/r31-existing-target.json",
    )
    assert repeated == decided

    tampered = copy.deepcopy(decided)
    tampered_row = next(row for row in tampered["rows"] if row["orderbook_id"] == "1393460")
    next(
        row
        for row in tampered_row["qualifying_fill_allocations"]
        if row["allocation_id"] == target["allocation_id"]
    )["quantity"] = 8
    with pytest.raises(ValueError, match="only partially applied"):
        apply_terminal_decisions_to_remediation(
            tampered,
            decisions,
            generated_at="2026-08-28T18:06:00+02:00",
            decision_source_path="output/r31-existing-target.json",
        )


def test_r31_terminal_decision_renewal_is_exact_and_idempotent():
    dynamic, remediation = sold_marker_reconciled_payloads()
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "1211627")
    dynamic_row = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1211627")
    prior = recovery["no_reentry_decision"]
    lot_decision = recovery["sale_lots"][0]["no_reentry_decision"]
    decisions = {
        "artifact": "PORTFOLIO_R17_TERMINAL_DECISIONS",
        "schema_version": 3,
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "rows": [{
            "tenant_session_id": "darkcell",
            "account_id": "7616265",
            "orderbook_id": "1211627",
            "instrument": "Coinbase",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "darkcell-coinbase-r31-renewed-no-reentry",
            "decision_at": "2026-08-28T18:30:00+02:00",
            "last_revalidated_at": "2026-08-28T18:30:00+02:00",
            "expires_at": "2026-09-10T18:30:00+02:00",
            "decision_basis": "Fresh evidence revalidates the exact already-closed sold slice.",
            "thesis_evidence": "The current marker exposure remains intentional and independent.",
            "event_evidence": "No newer event evidence reverses the reviewed terminal decision.",
            "technical_evidence": "Current technical evidence does not promote a rebuild.",
            "path_evidence": "The complete authenticated path and every exact closed lot were rechecked.",
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "current_holding": dynamic_row["live_holding"],
            "qualifying_fill_reallocations": [],
            "active_recovery_allocations": [],
            "sale_lot_closures": [],
            "terminal_decision_renewal": {
                "renewal_id": "darkcell-coinbase-r31-renewal-20260828",
                "prior_decision_id": prior["decision_id"],
                "closed_quantity": recovery["closed_no_reentry_quantity"],
                "sale_lot_decision_ids": [lot_decision["decision_id"]],
            },
        }],
    }

    decided = apply_terminal_decisions_to_remediation(
        remediation,
        decisions,
        generated_at="2026-08-28T18:31:00+02:00",
        decision_source_path="output/r31-terminal-renewal.json",
    )
    decided_row = next(row for row in decided["rows"] if row["orderbook_id"] == "1211627")
    renewed_lot = decided_row["sale_lots"][0]["no_reentry_decision"]

    assert decided_row["remaining_open_quantity"] == 0
    assert decided_row["closed_no_reentry_quantity"] == 16
    assert decided_row["no_reentry_decision"]["decision_id"] == decisions["rows"][0]["decision_id"]
    assert renewed_lot["decision_id"] == lot_decision["decision_id"]
    assert renewed_lot["parent_decision_id"] == decisions["rows"][0]["decision_id"]
    assert renewed_lot["expires_at"] == "2026-09-10T18:30:00+02:00"

    apply_terminal_decisions_to_dynamic_rows(dynamic["rows"], decided)
    assert dynamic_row["no_reentry_decision"] == decided_row["no_reentry_decision"]
    assert dynamic_row["mixed_lot_resolution"] == decided_row["mixed_lot_resolution"]

    repeated = apply_terminal_decisions_to_remediation(
        decided,
        decisions,
        generated_at="2026-08-28T18:31:00+02:00",
        decision_source_path="output/r31-terminal-renewal.json",
    )
    assert repeated == decided

    tampered = copy.deepcopy(decided)
    tampered_row = next(row for row in tampered["rows"] if row["orderbook_id"] == "1211627")
    tampered_row["sale_lots"][0]["no_reentry_decision"]["expires_at"] = (
        "2026-09-09T18:30:00+02:00"
    )
    with pytest.raises(ValueError, match="only partially applied"):
        apply_terminal_decisions_to_remediation(
            tampered,
            decisions,
            generated_at="2026-08-28T18:31:00+02:00",
            decision_source_path="output/r31-terminal-renewal.json",
        )


def test_r19_active_only_attribution_canonicalizes_empty_partial_decisions():
    dynamic, remediation = sold_marker_reconciled_payloads()
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "529720")
    dynamic_row = next(row for row in dynamic["rows"] if row["orderbook_id"] == "529720")
    lot = recovery["sale_lots"][0]
    recovery["unattributed_active_buy_quantity"] = 1
    recovery["unattributed_active_buy_inventory"] = [
        {"stop_loss_id": "stop-r19-amd-extra", "quantity": 1, "evidence": "Reviewed exact stop."}
    ]
    dynamic_row.update({
        "active_buy_volume": 4,
        "sale_attributed_active_buy_quantity": 3,
        "unattributed_active_buy_quantity": 1,
    })
    decisions = {
        "artifact": "PORTFOLIO_R17_TERMINAL_DECISIONS",
        "schema_version": 2,
        "generated_at": "2026-08-27T18:05:00+02:00",
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "rows": [{
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "529720",
            "instrument": "Advanced Micro Devices",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "r19-personal-amd-active-attribution",
            "decision_at": "2026-08-27T18:00:00+02:00",
            "last_revalidated_at": "2026-08-27T18:05:00+02:00",
            "expires_at": "2026-09-09T18:05:00+02:00",
            "decision_basis": "Exact stop metadata identifies the same-account sold slice.",
            "thesis_evidence": "The active recovery remains review inventory, not position intent.",
            "event_evidence": "Current event evidence does not alter the source attribution.",
            "technical_evidence": "The exact stop remains at its reviewed technical gate.",
            "path_evidence": "The complete authenticated sale-lot chronology was reviewed.",
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "current_holding": dynamic_row["live_holding"],
            "qualifying_fill_reallocations": [],
            "active_recovery_allocations": [{
                "allocation_id": "r19-active-amd-extra",
                "stop_loss_id": "stop-r19-amd-extra",
                "source_quantity": 1,
                "sale_lot_id": lot["sale_lot_id"],
                "quantity": 1,
                "strategy_intent": "SOLD_SLICE_RECOVERY",
                "strategy_reason": "Attribute this exact stop to the reviewed AMD sale lot.",
            }],
            "sale_lot_closures": [],
        }],
    }

    decided = apply_terminal_decisions_to_remediation(
        remediation,
        decisions,
        generated_at="2026-08-27T18:06:00+02:00",
        decision_source_path="output/PORTFOLIO_R19_MIXED_LOT_DECISIONS_test.json",
    )
    decided_row = next(row for row in decided["rows"] if row["orderbook_id"] == "529720")
    assert decided_row["state"] == "PARTIAL_SOLD_SLICE_RECOVERY_ATTRIBUTED"
    assert decided_row["remaining_open_quantity"] == 3
    assert decided_row["partial_terminal_decisions"] == []

    apply_terminal_decisions_to_dynamic_rows(dynamic["rows"], decided)
    assert dynamic_row["partial_terminal_decisions"] == []
    assert dynamic_row["target_rebuild_quantity"] == 3
    assert dynamic_row["low_exposure_decision"] == "INTENTIONAL_MARKER_OR_CORE_HOLD"


def test_r19_full_active_recovery_coverage_does_not_overwrite_current_exposure_intent():
    dynamic, remediation = sold_marker_reconciled_payloads()
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "1393460")
    dynamic_row = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1393460")
    lot = recovery["sale_lots"][0]
    remaining = recovery["remaining_open_quantity"]
    recovery["unattributed_active_buy_quantity"] = remaining
    recovery["unattributed_active_buy_inventory"] = [{
        "stop_loss_id": "stop-r19-soundhound-full",
        "quantity": remaining,
        "evidence": "Reviewed exact full-remainder stop.",
    }]
    dynamic_row.update({
        "active_buy_volume": remaining,
        "sale_attributed_active_buy_quantity": 0,
        "unattributed_active_buy_quantity": remaining,
        "economic_resolution": {
            "next_review": "Revalidate the current position after the active recovery row fills."
        },
    })
    decisions = {
        "artifact": "PORTFOLIO_R17_TERMINAL_DECISIONS",
        "schema_version": 2,
        "generated_at": "2026-08-27T18:05:00+02:00",
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "rows": [{
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "1393460",
            "instrument": "SoundHound AI",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "r19-personal-soundhound-full-active",
            "decision_at": "2026-08-27T18:00:00+02:00",
            "last_revalidated_at": "2026-08-27T18:05:00+02:00",
            "expires_at": "2026-09-09T18:05:00+02:00",
            "decision_basis": "Exact stop metadata covers the full unresolved sold remainder.",
            "thesis_evidence": "Recovery coverage does not independently decide current position intent.",
            "event_evidence": "Current event evidence was reviewed without changing attribution.",
            "technical_evidence": "The exact active row retains its reviewed technical gate.",
            "path_evidence": "The complete authenticated sale-lot chronology was reviewed.",
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "current_holding": dynamic_row["live_holding"],
            "qualifying_fill_reallocations": [],
            "active_recovery_allocations": [{
                "allocation_id": "r19-active-soundhound-full",
                "stop_loss_id": "stop-r19-soundhound-full",
                "source_quantity": remaining,
                "sale_lot_id": lot["sale_lot_id"],
                "quantity": remaining,
                "strategy_intent": "SOLD_SLICE_RECOVERY",
                "strategy_reason": "Attribute the exact full-remainder stop to the reviewed sale lot.",
            }],
            "sale_lot_closures": [],
        }],
    }

    decided = apply_terminal_decisions_to_remediation(
        remediation,
        decisions,
        generated_at="2026-08-27T18:06:00+02:00",
        decision_source_path="output/PORTFOLIO_R19_MIXED_LOT_DECISIONS_test.json",
    )
    decided_row = next(row for row in decided["rows"] if row["orderbook_id"] == "1393460")
    assert decided_row["state"] == "FULL_SOLD_SLICE_RECOVERY_COVERED"
    assert decided_row["remaining_open_quantity"] == 0
    assert decided_row["partial_terminal_decisions"] == []

    apply_terminal_decisions_to_dynamic_rows(dynamic["rows"], decided)
    assert dynamic_row["buyback_coverage_state"] == "LEDGER_ONLY"
    assert dynamic_row["low_exposure_decision"] == "INTENTIONAL_MARKER_OR_CORE_HOLD"
    assert dynamic_row["target_rebuild_quantity"] is None
    assert dynamic_row["partial_terminal_decisions"] == []
    assert "no sold-slice remainder is open" in dynamic_row["coverage_reason"]
    assert "unresolved" not in dynamic_row["coverage_reason"]
    assert dynamic_row["exact_next_gate"] == dynamic_row["economic_resolution"]["next_review"]

    reconciliation = sold_marker_dynamic_reconciliation_rows(dynamic, decided)
    exact = next(row for row in reconciliation if row["orderbook_id"] == "1393460")
    assert exact["dynamic_partial_terminal_decisions"] == exact["recovery_partial_terminal_decisions"]
    gaps = sold_marker_governance_gap_rows(reconciliation)
    assert "1393460" not in {row.get("orderbook_id") for row in gaps}


def test_r19_rejects_partial_stop_attribution_and_terminal_overclosure():
    _, remediation = sold_marker_reconciled_payloads()
    recovery = next(row for row in remediation["rows"] if row["orderbook_id"] == "1393460")
    lot = recovery["sale_lots"][0]
    recovery["unattributed_active_buy_quantity"] = 2
    recovery["unattributed_active_buy_inventory"] = [
        {"stop_loss_id": "stop-r19", "quantity": 2, "evidence": "Exact current source."}
    ]
    decision = {
        "artifact": "PORTFOLIO_R17_TERMINAL_DECISIONS",
        "schema_version": 2,
        "authority": "LOCAL_REVIEW_ONLY",
        "broker_mutation": False,
        "trade_authority": False,
        "rows": [{
            "tenant_session_id": "personal",
            "account_id": "5227886",
            "orderbook_id": "1393460",
            "instrument": "SoundHound AI",
            "recovery_cycle_id": recovery["recovery_cycle_id"],
            "decision_id": "r19-invalid",
            "decision_at": "2026-08-27T18:00:00+02:00",
            "last_revalidated_at": "2026-08-27T18:01:00+02:00",
            "expires_at": "2026-09-09T18:01:00+02:00",
            "decision_basis": "Reviewed exact source.",
            "thesis_evidence": "Reviewed thesis.",
            "event_evidence": "Reviewed event evidence.",
            "technical_evidence": "Reviewed technical evidence.",
            "path_evidence": "Reviewed complete path.",
            "newer_evidence_reviewed": True,
            "contradiction_status": "NONE",
            "current_holding": 1,
            "qualifying_fill_reallocations": [],
            "active_recovery_allocations": [{
                "allocation_id": "r19-partial-stop",
                "stop_loss_id": "stop-r19",
                "source_quantity": 2,
                "sale_lot_id": lot["sale_lot_id"],
                "quantity": 1,
                "strategy_intent": "SOLD_SLICE_RECOVERY",
                "strategy_reason": "Invalid partial attribution used for regression coverage.",
            }],
            "sale_lot_closures": [{
                "closure_id": "r19-overclose",
                "sale_lot_id": lot["sale_lot_id"],
                "sale_transaction_id": lot["sale_transaction_id"],
                "sale_timestamp": lot["sale_timestamp"],
                "remaining_open_quantity_to_close": lot["remaining_open_quantity"] + 1,
            }],
        }],
    }

    with pytest.raises(ValueError, match="not fully attributed"):
        apply_terminal_decisions_to_remediation(
            remediation,
            decision,
            generated_at="2026-08-27T18:02:00+02:00",
            decision_source_path="output/invalid.json",
        )

    overclose = copy.deepcopy(decision)
    overclose["rows"][0]["active_recovery_allocations"][0]["quantity"] = 2
    with pytest.raises(ValueError, match="overcloses"):
        apply_terminal_decisions_to_remediation(
            remediation,
            overclose,
            generated_at="2026-08-27T18:02:00+02:00",
            decision_source_path="output/invalid.json",
        )


def test_schema5_validator_rejects_erased_crossing_and_summary_drift():
    payload, registry, path = schema5_inputs()
    result = enrich_payload(
        payload,
        registry,
        generated_at="2026-08-26T18:00:00+02:00",
        source_path="output/input.json",
        path_evidence=path,
        path_source_path="output/PORTFOLIO_R17_OPEN_SALE_PATH_EVIDENCE_20260826_1754.json",
    )
    alpha = next(row for row in result["rows"] if row["orderbook_id"] == "1001")
    alpha["buyback_coverage_state"] = "LADDER_GAP"
    result["summary"]["buyback_coverage_state_counts"].update({
        "REPAIR_REQUIRED": 0,
        "LADDER_GAP": 2,
    })
    result["summary"]["repair_required_missed_path_rows"] = 0

    errors = validate_dynamic_live_coverage(result)

    assert any("crossed ordinary sold cycle is not REPAIR_REQUIRED" in error for error in errors)
    assert "dynamic missed-path repair count mismatch" in errors


def test_dynamic_buyback_validator_rejects_count_drift():
    payload = dynamic_buyback_payload()
    payload["summary"]["personal_rows"] = 18

    errors = validate_dynamic_live_coverage(payload)

    assert "dynamic summary Personal count mismatch" in errors


def test_dynamic_buyback_validator_rejects_market_band_that_contradicts_live_value():
    payload = dynamic_buyback_payload()
    payload["rows"][0]["live_market_value_sek"] = 25000.0

    errors = validate_dynamic_live_coverage(payload)

    assert any("market-value band contradicts live SEK value" in error for error in errors)


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


def test_sold_marker_recovery_accepts_exact_full_path_reconciliation():
    dynamic, remediation = sold_marker_reconciled_payloads()

    assert validate_sold_marker_remediation(remediation) == []
    assert validate_dynamic_against_sold_marker_recovery(dynamic, remediation) == []


def test_sold_marker_universe_keeps_full_path_and_modeled_outside_rows_distinct():
    _, remediation = sold_marker_reconciled_payloads()
    full_path_rows = [
        {
            "tenant_session_id": row["tenant_session_id"],
            "account_id": row["account_id"],
            "orderbook_id": row["orderbook_id"],
        }
        for row in remediation["rows"]
    ]
    full_path_rows.extend([
        {"tenant_session_id": "personal", "account_id": "5227886", "orderbook_id": "extra-personal"},
        {"tenant_session_id": "darkcell", "account_id": "7616265", "orderbook_id": "extra-darkcell"},
    ])
    full_path = {
        "summary": {"exact_account_rows": len(full_path_rows)},
        "rows": full_path_rows,
    }
    remediation["summary"]["exact_account_rows_with_prior_same_account_sales"] = 7
    remediation["summary"]["unmodeled_prior_sale_identity_count"] = 2
    remediation["summary"]["multi_sale_governance_complete"] = False
    remediation["source_universe"] = {
        "full_path_identity_count": 7,
        "modeled_outside_full_path_identity_count": 0,
        "combined_prior_sale_identity_count": 7,
    }

    assert validate_sold_marker_universe_against_full_path(remediation, full_path) == []

    remediation["summary"]["exact_account_rows_with_prior_same_account_sales"] = 5
    remediation["summary"]["unmodeled_prior_sale_identity_count"] = 0
    remediation["source_universe"]["combined_prior_sale_identity_count"] = 5
    errors = validate_sold_marker_universe_against_full_path(remediation, full_path)

    assert any("omits full-path" in error for error in errors)
    assert any("unmodeled count" in error for error in errors)


def test_sold_marker_remediation_preserves_every_worklist_sale_and_buy_source():
    _, remediation = sold_marker_reconciled_payloads()
    rows = []
    for recovery in remediation["rows"]:
        candidate_sources = []
        allocated_ids = set()
        for allocation in recovery["qualifying_fill_allocations"]:
            source_id = allocation["buy_transaction_id"]
            if source_id in allocated_ids:
                continue
            allocated_ids.add(source_id)
            candidate_sources.append({
                "buy_transaction_id": source_id,
                "buy_timestamp": allocation["buy_timestamp"],
                "bought_quantity": allocation["source_quantity"],
            })
        candidate_sources.extend({
            "buy_transaction_id": item["buy_transaction_id"],
            "buy_timestamp": item.get("buy_timestamp"),
            "bought_quantity": item["quantity"],
        } for item in recovery["unattributed_later_buy_inventory"])
        rows.append({
            "tenant_session_id": recovery["tenant_session_id"],
            "account_id": recovery["account_id"],
            "orderbook_id": recovery["orderbook_id"],
            "sale_lots": [{
                "sale_transaction_id": lot["sale_transaction_id"],
                "sale_timestamp": lot["sale_timestamp"],
                "sold_quantity": lot["sold_quantity"],
            } for lot in recovery["sale_lots"]],
            "candidate_later_buy_sources": candidate_sources,
        })
    worklist = {
        "artifact": "PORTFOLIO_R17_MULTI_SALE_MIGRATION_WORKLIST",
        "summary": {
            "combined_prior_sale_identity_count": len(rows),
            "selected_sale_lot_count": sum(len(row["sale_lots"]) for row in rows),
            "candidate_later_buy_source_count": sum(
                len(row["candidate_later_buy_sources"]) for row in rows
            ),
        },
        "rows": rows,
    }

    assert validate_sold_marker_remediation_against_worklist(remediation, worklist) == []

    worklist["rows"][0]["sale_lots"].pop()
    worklist["summary"]["selected_sale_lot_count"] -= 1
    errors = validate_sold_marker_remediation_against_worklist(remediation, worklist)

    assert any("changes or reorders raw sale lots" in error for error in errors)


def test_sold_marker_schema3_preserves_raw_and_split_normalized_quantities():
    _, remediation = sold_marker_reconciled_payloads()
    remediation["schema_version"] = 3
    worklist_rows = []
    for recovery in remediation["rows"]:
        recovery["non_recovery_buy_inventory"] = []
        recovery["non_recovery_buy_quantity"] = 0
        recovery["normalized_sale_quantity_total"] = recovery["sold_quantity"]
        recovery["raw_sale_quantity_total"] = recovery["sold_quantity"]
        for lot in recovery["sale_lots"]:
            lot["raw_sold_quantity"] = lot["sold_quantity"]
            lot["quantity_normalization_factor"] = 1
        buy_sources = []
        seen_sources = set()
        for allocation in recovery["qualifying_fill_allocations"]:
            allocation["raw_source_quantity"] = allocation["source_quantity"]
            allocation["quantity_normalization_factor"] = 1
            source_id = allocation["buy_transaction_id"]
            if source_id in seen_sources:
                continue
            seen_sources.add(source_id)
            buy_sources.append({
                "buy_transaction_id": source_id,
                "bought_quantity": allocation["source_quantity"],
                "recovery_allocated_quantity": sum(
                    item["quantity"]
                    for item in recovery["qualifying_fill_allocations"]
                    if item["buy_transaction_id"] == source_id
                ),
                "non_recovery_quantity": 0,
            })
        worklist_rows.append({
            "tenant_session_id": recovery["tenant_session_id"],
            "account_id": recovery["account_id"],
            "orderbook_id": recovery["orderbook_id"],
            "sale_lots": [{
                key: lot[key]
                for key in (
                    "sale_transaction_id",
                    "sale_timestamp",
                    "raw_sold_quantity",
                    "sold_quantity",
                    "quantity_normalization_factor",
                )
            } for lot in recovery["sale_lots"]],
            "buy_sources": buy_sources,
        })
    worklist = {
        "artifact": "PORTFOLIO_R17_MULTI_SALE_MIGRATION_WORKLIST",
        "schema_version": 3,
        "summary": {
            "combined_prior_sale_identity_count": len(worklist_rows),
            "selected_sale_lot_count": sum(len(row["sale_lots"]) for row in worklist_rows),
            "candidate_later_buy_source_count": sum(len(row["buy_sources"]) for row in worklist_rows),
            "replay_exact_identity_count": len(worklist_rows),
            "unmodeled_boundary_or_allocation_identity_count": 0,
        },
        "rows": worklist_rows,
    }

    assert validate_sold_marker_remediation(remediation) == []
    assert validate_sold_marker_remediation_against_worklist(remediation, worklist) == []

    recovery = remediation["rows"][0]
    recovery["sale_lots"][0]["quantity_normalization_factor"] = 2
    errors = validate_sold_marker_remediation(remediation)

    assert any("normalized quantity is inconsistent" in error for error in errors)


def test_position_protection_repairs_are_independent_of_sold_cycle_repairs():
    _, remediation = sold_marker_reconciled_payloads()
    remediation["verification"]["personal"]["position_repair_required_orderbook_ids"].append("3674")

    assert validate_sold_marker_remediation(remediation) == []


def test_sold_marker_recovery_accepts_two_exact_sale_lots_in_one_cycle():
    dynamic, remediation = sold_marker_reconciled_payloads()
    add_second_amd_sale_lot(dynamic, remediation)

    assert validate_sold_marker_remediation(remediation) == []
    assert validate_dynamic_against_sold_marker_recovery(dynamic, remediation) == []
    rows = sold_marker_dynamic_reconciliation_rows(dynamic, remediation)
    amd = next(row for row in rows if row["orderbook_id"] == "529720")
    dynamic_amd = next(row for row in dynamic["rows"] if row["orderbook_id"] == "529720")
    assert amd["dynamic_active_buy_volume"] == amd["sale_attributed_active_buy_quantity"]
    assert amd["dynamic_broker_active_buy_volume"] == dynamic_amd["active_buy_volume"]


def test_sold_marker_recovery_accepts_unproven_envelope_only_as_fail_closed_repair():
    _, remediation = sold_marker_reconciled_payloads()
    marvell = next(row for row in remediation["rows"] if row["orderbook_id"] == "3340")
    marvell["cycle_boundary_evidence"].update({
        "boundary_status": "UNPROVEN_CONSERVATIVE_ENVELOPE",
        "truncation_risk": True,
        "all_sale_transactions_in_cycle_included": False,
        "all_selected_sale_transactions_in_envelope_included": True,
    })

    assert validate_sold_marker_remediation(remediation) == []

    marvell["later_filled_quantity"] = 1
    marvell["remaining_open_quantity"] -= 1
    marvell["sale_lots"][0]["qualifying_filled_quantity"] = 1
    marvell["sale_lots"][0]["remaining_open_quantity"] -= 1
    marvell["qualifying_fill_allocations"] = [{
        "allocation_id": "unproven-fill-credit",
        "buy_transaction_id": "unproven-buy",
        "buy_timestamp": "2026-07-03T00:00:00+02:00",
        "source_quantity": 1,
        "sale_lot_id": marvell["sale_lots"][0]["sale_lot_id"],
        "quantity": 1,
    }]
    remediation["summary"]["remaining_open_quantity_across_material_rows"] -= 1

    errors = validate_sold_marker_remediation(remediation)

    assert any("cannot receive recovery or terminal credit" in error for error in errors)


def test_sold_marker_recovery_rejects_dynamic_dropping_older_sale_lot():
    dynamic, remediation = sold_marker_reconciled_payloads()
    add_second_amd_sale_lot(dynamic, remediation)
    amd = next(row for row in dynamic["rows"] if row["orderbook_id"] == "529720")
    amd["sale_lot_ids"] = amd["sale_lot_ids"][-1:]

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)

    assert any("sale-lot set mismatch" in error for error in errors)


def test_sold_marker_recovery_rejects_duplicate_raw_sale_transaction():
    dynamic, remediation = sold_marker_reconciled_payloads()
    add_second_amd_sale_lot(dynamic, remediation)
    amd = next(row for row in remediation["rows"] if row["orderbook_id"] == "529720")
    amd["sale_lots"][1]["sale_transaction_id"] = amd["sale_lots"][0]["sale_transaction_id"]

    errors = validate_sold_marker_remediation(remediation)

    assert any("sale transaction id is duplicated" in error for error in errors)


def test_sold_marker_recovery_rejects_overallocated_buy_fill():
    _, remediation = sold_marker_reconciled_payloads()
    soundhound = next(row for row in remediation["rows"] if row["orderbook_id"] == "1393460")
    soundhound["qualifying_fill_allocations"][0]["source_quantity"] = 100

    errors = validate_sold_marker_remediation(remediation)

    assert any("is overallocated" in error for error in errors)


def test_sold_marker_recovery_rejects_pre_sale_stop_credited_to_sale():
    dynamic, remediation = sold_marker_reconciled_payloads()
    amd = next(row for row in remediation["rows"] if row["orderbook_id"] == "529720")
    stop_id = amd["active_recovery_allocations"][0]["stop_loss_id"]
    amd["pre_sale_active_buy_inventory"] = [{"stop_loss_id": stop_id, "quantity": 3}]
    amd["pre_sale_active_buy_quantity"] = 3
    dynamic_amd = next(row for row in dynamic["rows"] if row["orderbook_id"] == "529720")
    dynamic_amd["pre_sale_active_buy_quantity"] = 3

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)

    assert any("both sale-attributed and unattributed" in error for error in errors)


def test_sold_marker_recovery_rejects_missing_dynamic_no_reentry_evidence():
    dynamic, remediation = sold_marker_reconciled_payloads()
    coinbase = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1211627")
    coinbase.pop("no_reentry_decision")

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)

    assert any("dynamic no-reentry decision is invalid" in error for error in errors)
    assert any("differs between remediation and dynamic coverage" in error for error in errors)


def test_sold_marker_recovery_rejects_expired_no_reentry_evidence():
    dynamic, remediation = sold_marker_reconciled_payloads()
    for payload in (dynamic, remediation):
        coinbase = next(row for row in payload["rows"] if row["orderbook_id"] == "1211627")
        coinbase["no_reentry_decision"]["expires_at"] = "2026-08-19T00:09:00+02:00"

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)
    reconciliation = sold_marker_dynamic_reconciliation_rows(dynamic, remediation)
    gaps = sold_marker_governance_gap_rows(reconciliation)

    assert any("no-reentry decision is expired" in error for error in errors)
    assert any(row.get("orderbook_id") == "1211627" for row in gaps)


def test_sold_marker_recovery_rejects_newer_no_reentry_contradiction():
    dynamic, remediation = sold_marker_reconciled_payloads()
    for payload in (dynamic, remediation):
        coinbase = next(row for row in payload["rows"] if row["orderbook_id"] == "1211627")
        coinbase["no_reentry_decision"]["contradiction_status"] = "NEWER_EVIDENCE_CONTRADICTS"

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)

    assert any("newer evidence contradicts" in error for error in errors)


def test_sold_marker_recovery_rejects_wrong_no_reentry_closed_quantity():
    dynamic, remediation = sold_marker_reconciled_payloads()
    for payload in (dynamic, remediation):
        coinbase = next(row for row in payload["rows"] if row["orderbook_id"] == "1211627")
        coinbase["no_reentry_decision"]["closed_quantity"] = 15

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)

    assert any("closed quantity does not equal the exact sold quantity" in error for error in errors)


def test_dynamic_buyback_rejects_noble_style_no_reentry_without_sale_identity():
    dynamic, _ = sold_marker_reconciled_payloads()
    coinbase = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1211627")
    coinbase.pop("no_reentry_decision")
    coinbase["latest_recent_sale_date"] = None
    coinbase["coverage_reason"] = "Reviewed risk-off marker with no active recovery row."

    errors = validate_dynamic_live_coverage(dynamic)

    assert any("structured no-reentry decision is missing" in error for error in errors)


def test_dynamic_buyback_rejects_shopify_style_active_recovery_as_no_reentry():
    dynamic, _ = sold_marker_reconciled_payloads()
    coinbase = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1211627")
    coinbase["active_buy_volume"] = 3
    coinbase["sale_attributed_active_buy_quantity"] = 3
    coinbase["coverage_reason"] = "Sale-attributed active BUY inventory remains open."

    errors = validate_dynamic_live_coverage(dynamic)

    assert any("exit/no-reentry row is contradicted by active same-sale BUY inventory" in error for error in errors)


def test_governed_dormant_ladder_is_not_an_open_completion_gap():
    dynamic, remediation = sold_marker_reconciled_payloads()
    promote_soundhound_to_governed_dormant_ladder(dynamic, remediation)

    assert validate_dynamic_against_sold_marker_recovery(dynamic, remediation) == []
    reconciliation = sold_marker_dynamic_reconciliation_rows(dynamic, remediation)
    gaps = sold_marker_governance_gap_rows(reconciliation)

    assert all(row.get("orderbook_id") != "1393460" for row in gaps)


def test_underquantified_dormant_ladder_remains_a_completion_gap():
    dynamic, remediation = sold_marker_reconciled_payloads()
    promote_soundhound_to_governed_dormant_ladder(dynamic, remediation)
    soundhound = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1393460")
    soundhound["stage_quantities"] = [79, 78]

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)
    reconciliation = sold_marker_dynamic_reconciliation_rows(dynamic, remediation)
    gaps = sold_marker_governance_gap_rows(reconciliation)

    assert any("stage quantities do not exactly cover" in error for error in errors)
    assert any(row.get("orderbook_id") == "1393460" for row in gaps)


def test_sold_marker_recovery_rejects_rebound_erasing_missed_marvell_path():
    dynamic, remediation = sold_marker_reconciled_payloads()
    marvell = next(row for row in dynamic["rows"] if row["orderbook_id"] == "3340")
    marvell.update({
        "current_protection_classification": "MARKER_EXCEPTION",
        "low_exposure_decision": "INTENTIONAL_MARKER_OR_CORE_HOLD",
        "buyback_coverage_state": "LEDGER_ONLY",
    })
    dynamic["summary"]["buyback_coverage_state_counts"] = dict(
        Counter(row["buyback_coverage_state"] for row in dynamic["rows"])
    )
    dynamic["summary"]["low_exposure_decision_counts"] = dict(
        Counter(row["low_exposure_decision"] for row in dynamic["rows"])
    )

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)

    assert any("missed sold-marker path is not REPAIR_REQUIRED" in error for error in errors)


def test_sold_marker_recovery_rejects_unsupported_material_path_as_ordinary_ledger():
    dynamic, remediation = sold_marker_reconciled_payloads()
    soundhound = next(row for row in dynamic["rows"] if row["orderbook_id"] == "1393460")
    soundhound["buyback_coverage_state"] = "LEDGER_ONLY"
    dynamic["summary"]["buyback_coverage_state_counts"] = dict(
        Counter(row["buyback_coverage_state"] for row in dynamic["rows"])
    )

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)

    assert any("unsupported material sold-marker path is not LADDER_GAP" in error for error in errors)


def test_sold_marker_recovery_rejects_dynamic_snapshot_older_than_path_overlay():
    dynamic, remediation = sold_marker_reconciled_payloads()
    dynamic["generated_at"] = "2026-08-19T00:09:59+02:00"

    errors = validate_dynamic_against_sold_marker_recovery(dynamic, remediation)

    assert "dynamic buyback coverage predates the authoritative sold-marker remediation" in errors


def test_sold_marker_selector_is_dated_and_current():
    path = latest_sold_marker_remediation_path()

    assert SOLD_MARKER_REMEDIATION_GLOB == "PORTFOLIO_SOLD_MARKER_REMEDIATION_LIVE_[0-9]*.json"
    assert path is not None
    assert "REMEDIATION_LIVE_" in path.name


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


def _rehash_full_history(payload):
    boundary = payload["raw_boundary"]
    mappings = payload["source_identity_to_lineage_map"]
    lineages = payload["effective_lineages"]
    lots = payload["immutable_sale_lots"]
    sale_transaction_ids = [row["sale_transaction_id"] for row in lots]
    lot_ids = [row["sale_lot_id"] for row in lots]
    source_ids = [row["source_identity_id"] for row in mappings]
    lineage_ids = [row["effective_lineage_id"] for row in lineages]
    boundary.update(
        {
            "source_identity_count": len(source_ids),
            "effective_lineage_count": len(lineage_ids),
            "immutable_sale_lot_count": len(lot_ids),
            "unique_sale_transaction_id_count": len(set(sale_transaction_ids)),
            "duplicate_sale_transaction_id_count": len(sale_transaction_ids)
            - len(set(sale_transaction_ids)),
            "missing_sale_transaction_id_count": sum(
                not value for value in sale_transaction_ids
            ),
            "source_identity_set_sha256": _canonical_json_sha256(
                sorted(source_ids)
            ),
            "source_identity_to_lineage_map_sha256": _canonical_json_sha256(
                mappings
            ),
            "effective_lineage_set_sha256": _canonical_json_sha256(
                sorted(lineage_ids)
            ),
            "effective_lineage_content_sha256": _canonical_json_sha256(lineages),
            "sale_transaction_id_set_sha256": _canonical_json_sha256(
                sorted(sale_transaction_ids)
            ),
            "sale_lot_id_set_sha256": _canonical_json_sha256(sorted(lot_ids)),
            "immutable_sale_lot_content_sha256": _canonical_json_sha256(lots),
            "allocation_content_sha256": _canonical_json_sha256(
                payload["qualifying_fill_allocations"]
            ),
            "active_recovery_source_content_sha256": _canonical_json_sha256(
                payload["active_recovery_sources"]
            ),
            "active_recovery_allocation_content_sha256": _canonical_json_sha256(
                payload["active_recovery_allocations"]
            ),
            "terminal_closure_content_sha256": _canonical_json_sha256(
                payload["terminal_closures"]
            ),
        }
    )


def full_history_payload():
    payload = {
        "artifact": "PORTFOLIO_FULL_HISTORY_CANONICAL",
        "schema_version": 2,
        "generated_at": "2026-09-03T18:00:00+02:00",
        "authority": {
            "trade_authority": False,
            "broker_mutation": False,
            "paper_mutation": False,
        },
        "raw_boundary": {
            "truncation_risk": False,
            "live_raw_vs_canonical_sale_transaction_id_set_parity": True,
        },
        "source_identity_to_lineage_map": [
            {
                "source_identity_id": "personal/5227886/orderbook-1001",
                "effective_lineage_id": "lineage-1",
            }
        ],
        "effective_lineages": [
            {
                "effective_lineage_id": "lineage-1",
                "source_identity_ids": ["personal/5227886/orderbook-1001"],
                "sale_lot_ids": ["lot-1"],
            }
        ],
        "immutable_sale_lots": [
            {
                "sale_lot_id": "lot-1",
                "sale_transaction_id": "sell-1",
                "effective_lineage_id": "lineage-1",
                "source_identity_id": "personal/5227886/orderbook-1001",
                "sale_timestamp": "2026-08-01T14:00:00+02:00",
                "raw_sold_quantity_exact": "10",
                "quantity_normalization_factor_exact": "1",
                "normalized_sold_quantity_exact": "10",
                "qualifying_filled_quantity_exact": "4",
                "active_recovery_quantity_exact": "0",
                "terminal_closure_quantity_exact": "0",
                "remaining_open_quantity_exact": "6",
                "terminal_closure_ids": [],
                "parity_delta_exact": "0",
            }
        ],
        "buy_sources": [
            {
                "buy_transaction_id": "buy-1",
                "effective_lineage_id": "lineage-1",
                "source_quantity_exact": "4",
                "allocated_recovery_quantity_exact": "4",
                "non_recovery_quantity_exact": "0",
                "unattributed_quantity_exact": "0",
            }
        ],
        "qualifying_fill_allocations": [
            {
                "allocation_id": "fill-1",
                "buy_transaction_id": "buy-1",
                "effective_lineage_id": "lineage-1",
                "sale_lot_id": "lot-1",
                "quantity_exact": "4",
            }
        ],
        "active_recovery_sources": [],
        "active_recovery_allocations": [],
        "terminal_closures": [],
        "dynamic_mirror_projection": [],
    }
    _rehash_full_history(payload)
    return payload


def full_history_raw_boundary(payload):
    boundary = payload["raw_boundary"]
    return {
        "artifact": "PORTFOLIO_R386_FULL_RAW_BOUNDARY_AFTER_ETH_SETTLEMENT",
        "current_boundary": {
            "source_identity_count": boundary["source_identity_count"],
            "effective_lineage_count_from_unchanged_corporate_action_map": boundary[
                "effective_lineage_count"
            ],
            "immutable_sale_lot_count": boundary["immutable_sale_lot_count"],
            "unique_sale_transaction_id_count": boundary[
                "unique_sale_transaction_id_count"
            ],
            "duplicate_sale_transaction_id_count": boundary[
                "duplicate_sale_transaction_id_count"
            ],
            "missing_sale_transaction_id_count": boundary[
                "missing_sale_transaction_id_count"
            ],
            "truncation_risk": False,
        },
        "parity": {
            "new_sale_transaction_ids": ["sell-1"],
            "prior_sale_transaction_id_omissions": [],
        },
    }


def full_dynamic_mirror(canonical, official=None):
    official = official or {"rows": []}
    lot = canonical["immutable_sale_lots"][0]
    active_ids = [
        row["active_recovery_source_id"]
        for row in canonical["active_recovery_sources"]
    ]
    closure_ids = [row["closure_id"] for row in canonical["terminal_closures"]]
    row = {
        "r390_dynamic_row_id": "lineage::lineage-1",
        "tenant_session_id": "personal",
        "account_id": "5227886",
        "orderbook_id": "1001",
        "r137_canonical_lineage_ids": ["lineage-1"],
        "buyback_coverage_state": "LADDER_GAP",
        "r390_full_history": {
            "effective_lineage_ids": ["lineage-1"],
            "source_identity_ids": ["personal/5227886/orderbook-1001"],
            "sale_lot_ids": ["lot-1"],
            "open_sale_lot_ids": (
                ["lot-1"]
                if float(lot["remaining_open_quantity_exact"]) > 0
                else []
            ),
            "active_recovery_source_ids": active_ids,
            "terminal_closure_ids": closure_ids,
            "normalized_sold_quantity_exact": lot[
                "normalized_sold_quantity_exact"
            ],
            "qualifying_filled_quantity_exact": lot[
                "qualifying_filled_quantity_exact"
            ],
            "active_recovery_quantity_exact": lot[
                "active_recovery_quantity_exact"
            ],
            "terminal_closure_quantity_exact": lot[
                "terminal_closure_quantity_exact"
            ],
            "remaining_open_quantity_exact": lot["remaining_open_quantity_exact"],
        },
    }
    official_rows = official.get("rows", [])
    if official_rows:
        row["r390_official_close_reachability"] = copy.deepcopy(official_rows[0])
    return {
        "artifact": "PORTFOLIO_FULL_DYNAMIC_GOVERNANCE_MIRROR",
        "schema_version": 2,
        "authority": {
            "authoritative_dynamic_ledger": True,
            "trade_authority": False,
            "broker_mutation": False,
        },
        "objective_complete": False,
        "canonical_contract": {
            "payload_sha256": _canonical_json_sha256(canonical),
        },
        "official_close_contract": {
            "payload_sha256": _canonical_json_sha256(official),
            "later_rebound_erases_crossing": False,
            "row_identity_set_sha256": _canonical_json_sha256(
                sorted(
                    [
                        [
                            str(value.get("tenant_session_id") or ""),
                            str(value.get("account_id") or ""),
                            str(value.get("orderbook_id") or ""),
                        ]
                        for value in official_rows
                    ]
                )
            ),
        },
        "rows": [row],
        "summary": {
            "dynamic_row_count": 1,
            "mirrored_effective_lineage_count": 1,
            "mirrored_source_identity_count": 1,
            "mirrored_immutable_sale_lot_count": 1,
            "official_close_row_count": len(official_rows),
        },
    }


def _add_current_terminal_closure(payload):
    lot = payload["immutable_sale_lots"][0]
    lot.update(
        {
            "terminal_closure_quantity_exact": "1",
            "remaining_open_quantity_exact": "5",
            "terminal_closure_ids": ["closure-1"],
        }
    )
    payload["terminal_closures"] = [
        {
            "closure_id": "closure-1",
            "sale_lot_id": "lot-1",
            "sale_transaction_id": "sell-1",
            "canonical_terminal_closure_antal_exact": "1",
            "decision_time": "2026-09-01T10:00:00+02:00",
            "expires_at": "2026-09-10T10:00:00+02:00",
            "contradiction_status": "NONE",
        }
    ]
    _rehash_full_history(payload)


def test_full_history_and_dynamic_mirror_positive_contract():
    canonical = full_history_payload()
    official = {"rows": []}
    mirror = full_dynamic_mirror(canonical, official)

    assert validate_full_history_canonical(
        canonical, full_history_raw_boundary(canonical)
    ) == []
    assert validate_full_dynamic_governance_mirror(
        mirror, canonical, official
    ) == []


def test_full_history_rejects_source_identity_omission():
    payload = full_history_payload()
    payload["source_identity_to_lineage_map"] = []

    errors = validate_full_history_canonical(payload)

    assert any("lot source identity is unknown" in error for error in errors)


def test_full_history_rejects_duplicate_sale_transaction_id():
    payload = full_history_payload()
    duplicate = copy.deepcopy(payload["immutable_sale_lots"][0])
    duplicate["sale_lot_id"] = "lot-2"
    payload["immutable_sale_lots"].append(duplicate)
    payload["effective_lineages"][0]["sale_lot_ids"].append("lot-2")

    errors = validate_full_history_canonical(payload)

    assert "full-history sale transaction id is duplicated" in errors


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "raw_sold_quantity_exact",
            "11",
            "full-history lot normalization parity failed",
        ),
        ("sale_timestamp", "not-a-time", "full-history sale timestamp is invalid"),
    ],
)
def test_full_history_rejects_lot_quantity_or_timestamp_mutation(
    field, value, message
):
    payload = full_history_payload()
    payload["immutable_sale_lots"][0][field] = value

    errors = validate_full_history_canonical(payload)

    assert any(message in error for error in errors)


def test_full_history_rejects_source_identity_mapped_twice():
    payload = full_history_payload()
    payload["source_identity_to_lineage_map"].append(
        {
            "source_identity_id": "personal/5227886/orderbook-1001",
            "effective_lineage_id": "lineage-2",
        }
    )

    errors = validate_full_history_canonical(payload)

    assert "full-history source identity id is duplicated" in errors


def test_full_history_rejects_buy_source_overallocation():
    payload = full_history_payload()
    payload["qualifying_fill_allocations"][0]["quantity_exact"] = "5"

    errors = validate_full_history_canonical(payload)

    assert any("buy source allocation parity failed" in error for error in errors)


def test_full_history_rejects_source_as_both_filled_and_active():
    payload = full_history_payload()
    payload["active_recovery_sources"] = [
        {
            "active_recovery_source_id": "buy-1",
            "effective_lineage_id": "lineage-1",
            "source_quantity_exact": "1",
            "allocated_recovery_quantity_exact": "0",
            "unattributed_quantity_exact": "1",
        }
    ]

    errors = validate_full_history_canonical(payload)

    assert "full-history one source is classified as both filled and active" in errors


def test_full_history_rejects_expired_terminal_decision():
    payload = full_history_payload()
    _add_current_terminal_closure(payload)
    payload["terminal_closures"][0]["expires_at"] = "2026-09-02T10:00:00+02:00"
    _rehash_full_history(payload)

    errors = validate_full_history_canonical(payload)

    assert any("terminal closure is expired" in error for error in errors)


def test_full_history_rejects_current_raw_boundary_drift():
    payload = full_history_payload()
    boundary = full_history_raw_boundary(payload)
    boundary["current_boundary"]["immutable_sale_lot_count"] = 2

    errors = validate_full_history_canonical(payload, boundary)

    assert "full-history canonical does not match current raw boundary" in errors


def test_full_dynamic_mirror_rejects_omitted_lineage():
    canonical = full_history_payload()
    official = {"rows": []}
    mirror = full_dynamic_mirror(canonical, official)
    mirror["rows"][0]["r390_full_history"]["effective_lineage_ids"] = []

    errors = validate_full_dynamic_governance_mirror(mirror, canonical, official)

    assert "full dynamic lineage parity mismatch" in errors


def test_full_dynamic_mirror_rejects_sale_lot_membership_change():
    canonical = full_history_payload()
    official = {"rows": []}
    mirror = full_dynamic_mirror(canonical, official)
    mirror["rows"][0]["r390_full_history"]["sale_lot_ids"] = []

    errors = validate_full_dynamic_governance_mirror(mirror, canonical, official)

    assert any("sale-lot order or membership mismatch" in error for error in errors)


def test_full_dynamic_mirror_rejects_rebound_clearing_repair():
    canonical = full_history_payload()
    official = {
        "rows": [
            {
                "tenant_session_id": "personal",
                "account_id": "5227886",
                "orderbook_id": "1001",
                "state": "HISTORICAL_STAGE_REACHED_REBOUNDED_REPAIR",
            }
        ]
    }
    mirror = full_dynamic_mirror(canonical, official)

    errors = validate_full_dynamic_governance_mirror(mirror, canonical, official)

    assert any("missed crossing was cleared" in error for error in errors)


def test_full_dynamic_mirror_rejects_unmirrored_terminal_decision():
    canonical = full_history_payload()
    _add_current_terminal_closure(canonical)
    official = {"rows": []}
    mirror = full_dynamic_mirror(canonical, official)
    mirror["rows"][0]["r390_full_history"]["terminal_closure_ids"] = []

    errors = validate_full_dynamic_governance_mirror(mirror, canonical, official)

    assert any("terminal-closure membership mismatch" in error for error in errors)
