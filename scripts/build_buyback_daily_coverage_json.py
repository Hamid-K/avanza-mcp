#!/usr/bin/env python3
"""Build the machine-readable buyback coverage ledger from its rendered table."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "output" / "PORTFOLIO_BUYBACK_DAILY_COVERAGE_20260806.md"
OUTPUT = ROOT / "output" / "PORTFOLIO_BUYBACK_DAILY_COVERAGE_20260806.json"

STOCKS = {
    "AMD": ("AMD", "529720"),
    "Arista Networks": ("ANET", "482615"),
    "Astera Labs": ("ALAB", "1738607"),
    "Ciena": ("CIEN", "451419"),
    "D-Wave Quantum": ("QBTS", "1445340"),
    "F5": ("FFIV", "4069"),
    "GE Vernova": ("GEV", "1744076"),
    "Intel": ("INTC", "3658"),
    "IonQ": ("IONQ", "1284389"),
    "Marvell Technology": ("MRVL", "3340"),
    "Morgan Stanley": ("MS", "3468"),
    "Okta A": ("OKTA", "741275"),
    "POET Technologies": ("POET", "1581461"),
    "Palantir Technologies": ("PLTR", "1138439"),
    "Rigetti Computing": ("RGTI", "1356668"),
    "SpaceX": ("SPCX", "2509560"),
    "Teradyne": ("TER", "3674"),
    "Vertiv Holdings A": ("VRT", "1042976"),
    "Akamai Technologies": ("AKAM", "3535"),
    "American Battery Technology": ("ABAT", "1642727"),
    "Booz Allen Hamilton": ("BAH", "258824"),
    "Coinbase": ("COIN", "1211627"),
    "Gilat Satellite Networks": ("GILT", "179475"),
    "Globus Medical": ("GMED", "366637"),
    "Newmont": ("NEM", "3968"),
    "Noble A": ("NE", "3402"),
    "Palo Alto Networks": ("PANW", "365155"),
    "Shopify": ("SHOP", "564535"),
    "Wayfair A": ("W", "508521"),
    "Western Digital": ("WDC", "353285"),
    "Zoom Communications A": ("ZM", "952518"),
}

COVERAGE_STATES = (
    "LADDER_DORMANT",
    "LEDGER_ONLY",
    "LADDER_GAP",
    "REPAIR_REQUIRED",
    "NAMED_EXCEPTION",
)

# A ledger-only row is still a strategy state. These paired conditions keep
# daily review stock-specific without turning review evidence into authority.
EVIDENCE_CONTRACTS: dict[str, tuple[str, str]] = {
    "AMD": (
        "After the verified August 4 results/guidance review, no material guide cut, a regular-session higher low and VWAP/opening-range reclaim, then factor/capacity and full-friction checks.",
        "Reject or keep ledger-only on a material guide cut, failed reclaim, semiconductor/data-center capacity breach, or failed full-friction hurdle.",
    ),
    "ANET": (
        "After the verified August 4 event response, two regular-session closes holding post-event support, a higher low and VWAP reclaim, then one-share stage economics clear full friction.",
        "Reject staged participation on a new event low, failed support/reclaim, thesis or guidance deterioration, or uneconomic stage size.",
    ),
    "ALAB": (
        "After the verified August 4 event response, no material guidance cut, a post-event higher low and VWAP/opening-range reclaim, then spread, capacity and full-friction checks.",
        "Reject on a material guide cut, failed post-event base, failed reclaim, or factor/capacity/friction failure.",
    ),
    "CIEN": (
        "A regular-session higher low above current range/support followed by a reclaim, with the proposed quantity clearing the 3x full-friction hurdle.",
        "Keep ledger-only if support fails, the higher low is absent, spread widens materially, or the 3x full-friction hurdle fails.",
    ),
    "QBTS": (
        "A verified catalyst/roadmap check followed by a regular-session base and higher low; one deep row must clear the 3x full-friction hurdle before any redesign.",
        "Reject staged participation on a thesis break, failed base/reclaim, or fee-heavy quantity that fails full friction.",
    ),
    "FFIV": (
        "A stock-specific absolute child cap derived from the current range and core stop, with exact quantity, risk and full-friction checks completed.",
        "Reject any replacement and keep the existing row unchanged when no absolute cap/range model is verified; never replace a percentage child merely to make coverage look complete.",
    ),
    "GEV": (
        "A regular-session base, higher low and VWAP/opening-range reclaim, followed by capacity and full-friction checks for the one-share add.",
        "Reject on a failed rebound, unresolved range breakdown, factor-capacity breach, or failed full-friction hurdle.",
    ),
    "INTC": (
        "A verified thesis/catalyst check with no material guide cut, a regular-session higher low and a second-stage quantity that clears full friction.",
        "Keep ledger-only on a thesis/guidance break, failed support/reclaim, or uneconomic second stage.",
    ),
    "IONQ": (
        "A verified roadmap/catalyst, regular-session higher low and VWAP reclaim, followed by exact risk, capacity and full-friction review; DarkCell also requires the bridge contract and exact approval gate.",
        "Reject extra stages on a roadmap/thesis break, failed reclaim, risk or friction failure, or missing required bridge/approval evidence.",
    ),
    "MRVL": (
        "The dormant stock-specific design is promotable only after semiconductor/data-center capacity passes, a regular-session higher low/VWAP reclaim forms, and risk/full-friction checks pass.",
        "Reject promotion on a new drawdown low, failed reclaim, factor-capacity breach, or failed risk/full-friction hurdle.",
    ),
    "MS": (
        "A stock-specific range model produces an absolute child cap and exact quantity, followed by risk and full-friction checks.",
        "Reject any replacement and keep the existing follow-down row unchanged when the absolute cap/range model is missing; no percentage-only replacement is allowed.",
    ),
    "NEM": (
        "Gold/allocation, operating, cost, reserve and capital-return evidence must remain acceptable, followed by a meaningful stock-specific pullback, regular-session higher low/reclaim and full risk/capacity/friction checks.",
        "Reject re-entry on a gold/allocation or operating thesis break, failed pullback/reclaim, spread or capacity failure, or absent fresh exact approval after the manual exit.",
    ),
    "OKTA": (
        "A verified event/thesis check with no material guide cut, a regular-session higher low/VWAP reclaim, and stage economics clearing full friction.",
        "Reject on a thesis/guidance break, failed reclaim, or stage economics that do not clear full friction.",
    ),
    "POET": (
        "A regular-session higher low above support plus a single deep quantity that clears the 3x full-friction hurdle; no multi-stage ladder is required at this size.",
        "Keep marker-only or one-deep-row treatment on a failed base, unresolved thesis, or fragmented quantity that fails full friction.",
    ),
    "PLTR": (
        "The official event/10-Q review remains intact, price makes the account-specific pullback from the sale reference, then forms a regular-session higher low and VWAP/opening-range reclaim without a new event low.",
        "Reject or keep dormant on a new event low, material thesis/guidance deterioration, failed reclaim, factor/capacity breach, or failed full-friction gate.",
    ),
    "RGTI": (
        "A verified roadmap/catalyst check, regular-session technical base and higher low, with one small deep quantity clearing full friction before redesign.",
        "Reject on a thesis/roadmap break, failed base/reclaim, or uneconomic speculative quantity.",
    ),
    "SPCX": (
        "Only a fresh exact SpaceX approval and instruction specifying account, side, quantity, hard price limit and validity can promote any change; otherwise retain the named exception.",
        "Reject every generic ladder or automatic repair when fresh exact SpaceX approval is absent, regardless of the quoted drawdown.",
    ),
    "TER": (
        "A post-earnings breakout hold followed by regular-session reversal/higher-low evidence, stale sizing recalculation, and risk/full-friction checks; DarkCell also requires displacement review.",
        "Reject on failed breakout hold, failed reversal, stale sizing, capacity/displacement failure, or failed full-friction hurdle.",
    ),
    "VRT": (
        "A base, regular-session higher low and VWAP/opening-range reclaim must replace part of the existing row before any add or redesign.",
        "Reject participation after a failed opening rebound, support breakdown, factor-capacity breach, or failed full-friction hurdle.",
    ),
    "AKAM": (
        "The official August 6 results/guidance must show no material thesis break, followed by a regular-session higher low/reclaim and DarkCell capacity/risk/full-friction checks.",
        "Reject on a material guide cut, failed post-event support/reclaim, or DarkCell capacity/risk/friction failure.",
    ),
    "ABAT": (
        "Only a material verified thesis/event change plus a regular-session higher low and quantity clearing full friction can promote the marker to a plan.",
        "Keep marker-only when no verified catalyst or technical base exists, or when the notional cannot clear full friction.",
    ),
    "BAH": (
        "A verified earnings/catalyst review with no material guide cut, a regular-session higher low/reclaim and capacity/full-friction clearance.",
        "Reject on a guide/thesis break, failed reclaim, or capacity/full-friction failure.",
    ),
    "COIN": (
        "A verified crypto thesis/catalyst, regular-session higher low/reclaim, crypto-linked exposure below the mandatory review threshold, and full-friction clearance.",
        "Reject while crypto-linked exposure remains above the mandatory review threshold, the thesis breaks, or technical/friction gates fail.",
    ),
    "GILT": (
        "A verified event/reaction review followed by a regular-session higher low, support reclaim, acceptable liquidity/spread and full-friction clearance.",
        "Reject on a material event deterioration, failed support/reclaim, poor liquidity/spread, or failed full-friction hurdle.",
    ),
    "GMED": (
        "The official August 6 results/guidance must remain intact, followed by a regular-session higher low/reclaim and DarkCell capacity, risk and full-friction checks.",
        "Reject on a material guide cut, failed post-event technical confirmation, or DarkCell capacity/risk/friction failure.",
    ),
    "NE": (
        "Only a fresh explicit thesis-reset approval, regular-session higher low/reclaim, and capacity/risk/full-friction clearance can promote re-entry.",
        "Keep risk-off marker-only when no thesis reset and exact approval exist; historical loss is not a re-entry signal.",
    ),
    "PANW": (
        "Core-rebuild evidence must show an intact thesis, regular-session higher low/reclaim, DarkCell capacity clearance and full-friction economics.",
        "Reject on thesis/guidance deterioration, failed reclaim, DarkCell capacity breach, or failed full-friction hurdle.",
    ),
    "SHOP": (
        "The official call review must remain intact, followed by multi-session post-gap stabilization, a higher low/reclaim, and full event, capacity, risk, churn, spread and friction checks.",
        "Reject a new stage on a new gap low, failed stabilization/reclaim, thesis break, or unattributed pre-existing BUY row; do not relabel it as sold-slice recovery.",
    ),
    "W": (
        "The three-stage design is promotable only after a stable regular-session higher low/reclaim without a new event low, materially tighter spread, and full thesis/capacity/risk/churn/friction checks.",
        "Reject on a new event low, failed reclaim, persistently wide spread, thesis break, or failed full-friction hurdle.",
    ),
    "WDC": (
        "The verified report reaction must hold support, form a regular-session higher low/reclaim, and clear spread, capacity and full-friction checks.",
        "Reject on a report-driven thesis/guidance break, failed support/reclaim, spread deterioration, or capacity/friction failure.",
    ),
    "ZM": (
        "Only an official result or material strategy change followed by a regular-session higher low/reclaim and full-friction clearance can justify a ladder.",
        "Reject a ladder and keep tracker-only when no official catalyst or material strategy change exists; do not create a ladder from price weakness alone.",
    ),
}


def evidence_contract(row: dict[str, object]) -> tuple[str, str]:
    ticker = str(row["ticker"])
    if ticker not in EVIDENCE_CONTRACTS:
        raise ValueError(f"missing buyback evidence contract for {ticker}")
    promotion, rejection = EVIDENCE_CONTRACTS[ticker]
    if ticker == "TER" and str(row["account_id"]) == "7616265":
        promotion += " Exact DarkCell displacement must be named before any proposal."
    return promotion, rejection


def parse_value(value: str) -> float:
    return float(value.replace(",", "").split()[0])


def parse_rows(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        if not (line.startswith("| Personal | ") or line.startswith("| DarkCell | ")):
            continue
        fields = [field.strip() for field in line.strip().strip("|").split("|")]
        if len(fields) != 7:
            raise ValueError(f"unexpected candidate table row: {line}")
        account_label, stock, holding, value, existing_buy, state, next_check = fields
        stock_name = stock.replace(" (`PLTR`)", "").replace(" (`W`)", "")
        stock_name = re.sub(r" \(`[^`]+`\)$", "", stock_name)
        ticker, orderbook_id = STOCKS.get(stock_name, (None, None))
        if not ticker:
            raise ValueError(f"missing stock mapping for {stock_name!r}")
        rows.append(
            {
                "account_id": "5227886" if account_label == "Personal" else "7616265",
                "tenant_session_id": "personal" if account_label == "Personal" else "darkcell",
                "account_label": account_label,
                "instrument": stock_name,
                "ticker": ticker,
                "orderbook_id": orderbook_id,
                "holding": int(holding.replace(",", "")),
                "value_sek": parse_value(value),
                "existing_buy": existing_buy,
                "coverage_state": state,
                "next_daily_evidence": next_check,
            }
        )
        promotion, rejection = evidence_contract(rows[-1])
        rows[-1]["promotion_evidence"] = promotion
        rows[-1]["rejection_evidence"] = rejection
    return rows


def candidate_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    account_counts = Counter(str(row["account_id"]) for row in rows)
    return {
        "count": len(rows),
        "account_rows": {
            "personal_5227886": account_counts.get("5227886", 0),
            "darkcell_7616265": account_counts.get("7616265", 0),
        },
        "one_share_rows": sum(int(row["holding"]) == 1 for row in rows),
        "low_sek_rows": sum(float(row["value_sek"]) <= 5000 for row in rows),
        "without_active_buy_rows": sum(
            str(row["existing_buy"]).strip().lower() in {"", "none"} for row in rows
        ),
    }


def freshness_metadata(as_of: str, *, current_live: bool = False) -> dict[str, object]:
    """Mark rendered-table evidence as stamped until a scoped live refresh proves otherwise."""

    if current_live:
        return {
            "status": "CURRENT_LIVE_REFRESH",
            "source_as_of": as_of,
            "live_state_current": True,
            "live_refresh_verified": True,
            "requires_new_scoped_live_refresh_before_action": False,
            "statement": (
                "This ledger is derived from an exact Personal and DarkCell refresh. "
                "It remains review evidence only and never authorizes a mutation."
            ),
        }
    return {
        "status": "STAMPED_REVIEW_SNAPSHOT",
        "source_as_of": as_of,
        "live_state_current": False,
        "live_refresh_verified": False,
        "requires_new_scoped_live_refresh_before_action": True,
        "statement": (
            "This ledger is derived from a rendered snapshot. It is review evidence only; "
            "both exact tenant/account snapshots must succeed before any action proposal "
            "can be treated as current."
        ),
    }


def extract_source_as_of(table: str) -> str:
    """Preserve the stamped source timestamp across label migrations."""

    for label in ("Stamped source snapshot", "Fresh exact live snapshot", "Live refresh"):
        match = re.search(rf"^{re.escape(label)}: (.+)$", table, flags=re.MULTILINE)
        if match:
            return match.group(1).strip()
    return "UNSPECIFIED"


def extract_latest_refresh_attempt(table: str) -> dict[str, str]:
    """Preserve the latest operator-controlled refresh boundary in the ledger."""

    match = re.search(
        r"^Latest refresh attempt: `([^`]+)`; (.+)$",
        table,
        flags=re.MULTILINE,
    )
    if not match:
        return {}
    detail = match.group(2).strip()
    status = "SESSION_UNAVAILABLE" if "failed" in detail.lower() or "unavailable" in detail.lower() else "RECORDED"
    return {
        "last_refresh_attempt_as_of": match.group(1).strip(),
        "last_refresh_attempt_status": status,
        "last_refresh_error": detail if status == "SESSION_UNAVAILABLE" else "",
    }


def main() -> None:
    table = TABLE.read_text(encoding="utf-8")
    rows = parse_rows(table)
    states = Counter(row["coverage_state"] for row in rows)
    metrics = candidate_metrics(rows)
    as_of = extract_source_as_of(table)
    current_live = "Fresh exact live snapshot:" in table
    freshness = freshness_metadata(as_of, current_live=current_live)
    if current_live:
        freshness.update(
            {
                "last_refresh_attempt_as_of": as_of,
                "last_refresh_attempt_status": "LIVE_REFRESH_VERIFIED",
            }
        )
    else:
        freshness.update(extract_latest_refresh_attempt(table))
    artifact = {
        "artifact": "PORTFOLIO_BUYBACK_DAILY_COVERAGE",
        "as_of": as_of,
        "source_table": "output/PORTFOLIO_BUYBACK_DAILY_COVERAGE_20260806.md",
        "freshness": freshness,
        "authority": {
            "trade_authority": False,
            "broker_mutation": False,
            "paper_mutation": False,
            "statement": "Derived review ledger only. It never authorizes an order, cancellation, repricing, edit, or stop mutation.",
        },
        "candidate_universe": {
            "criteria": [
                "one-share account positions",
                "holdings at or below 5,000 SEK coverage screen",
                "manual exit-1 or sold-slice rows",
                "named exceptions",
            ],
            **metrics,
        },
        "coverage_states": {state: states.get(state, 0) for state in COVERAGE_STATES},
        "daily_fields": [
            "exact account and tenant",
            "orderbook and instrument",
            "holding and current SEK value",
            "active BUY inventory without ladder promotion",
            "coverage state",
            "next evidence gate",
            "promotion evidence",
            "rejection/hold evidence",
        ],
        "rows": rows,
        "live_controls": {
            "regular_open_orders": {"personal": 0, "darkcell": 0},
            "raw_failed_orders": {"personal": 0, "darkcell": 0},
            "protection_gaps": {"personal": 0, "darkcell": 0},
            "live_authorization": {"personal": False, "darkcell": False},
        },
    }
    OUTPUT.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"[buyback] wrote {OUTPUT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
