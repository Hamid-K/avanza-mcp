"""Read-only research candidate aggregation endpoints."""

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from avanza_mcp import utils

router = APIRouter()

MAX_CANDIDATES = 50
MAX_ENRICHED_CANDIDATES = 12


def _kernel(request: Request):
    return request.app.state.runtime.kernel


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float | None:
    return utils.scalar_number(value)


def _short_text(value: Any, max_chars: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _symbol_from_row(row: dict[str, Any]) -> str:
    symbol = str(_first_value(row, "symbol", "name") or "").strip().upper()
    if ":" in symbol:
        symbol = symbol.split(":", 1)[1]
    return symbol


def _exchange_from_row(row: dict[str, Any]) -> str:
    return str(_first_value(row, "exchange", "market") or "").strip().upper()


def _technical_score_points(label: str, score: Any) -> float:
    normalized = str(label or "").strip().lower().replace("_", " ")
    if normalized == "strong buy":
        return 25.0
    if normalized == "buy":
        return 15.0
    if normalized == "neutral":
        return 0.0
    if normalized == "sell":
        return -12.0
    if normalized == "strong sell":
        return -22.0
    numeric = _number(score)
    if numeric is not None:
        return max(-22.0, min(25.0, numeric * 20.0))
    return 0.0


def _zacks_score_points(snapshot: dict[str, Any]) -> float:
    rank = snapshot.get("rank") if isinstance(snapshot.get("rank"), dict) else {}
    value = _number(rank.get("value"))
    points = 0.0
    if value == 1:
        points += 30.0
    elif value == 2:
        points += 20.0
    elif value == 3:
        points += 5.0
    elif value == 4:
        points -= 15.0
    elif value == 5:
        points -= 30.0

    esp = str(snapshot.get("earnings_esp") or "").replace("%", "")
    esp_value = _number(esp)
    if esp_value is not None:
        points += max(-5.0, min(5.0, esp_value / 2.0))
    return points


def _fmp_score_points(snapshot: dict[str, Any]) -> float:
    latest = snapshot.get("latest") if isinstance(snapshot.get("latest"), dict) else {}
    strong_buy = int(latest.get("strong_buy", 0) or 0)
    buy = int(latest.get("buy", 0) or 0)
    hold = int(latest.get("hold", 0) or 0)
    sell = int(latest.get("sell", 0) or 0)
    strong_sell = int(latest.get("strong_sell", 0) or 0)
    total = strong_buy + buy + hold + sell + strong_sell
    if total <= 0:
        return 0.0
    bullish = strong_buy * 2 + buy
    bearish = strong_sell * 2 + sell
    return max(-15.0, min(15.0, ((bullish - bearish) / total) * 12.0))


def _format_zacks(snapshot: dict[str, Any]) -> tuple[str, str]:
    rank = snapshot.get("rank") if isinstance(snapshot.get("rank"), dict) else {}
    value = rank.get("value")
    label = str(rank.get("label") or "").strip()
    if value:
        text = f"#{value}"
        if label:
            text += f" {label}"
    else:
        text = "n/a"
    summary = snapshot.get("analysis_summary") if isinstance(snapshot.get("analysis_summary"), dict) else {}
    note = _short_text(summary.get("summary"), 220)
    return text, note


def _format_fmp(snapshot: dict[str, Any]) -> str:
    latest = snapshot.get("latest") if isinstance(snapshot.get("latest"), dict) else {}
    if not latest:
        return "n/a"
    return (
        f"SB {latest.get('strong_buy', 0)} / B {latest.get('buy', 0)} / "
        f"H {latest.get('hold', 0)} / S {latest.get('sell', 0)}"
    )


def _make_candidate(row: dict[str, Any], index: int) -> dict[str, Any]:
    symbol = _symbol_from_row(row)
    exchange = _exchange_from_row(row)
    close = _number(_first_value(row, "close", "last", "premarket_close", "postmarket_close"))
    change_percent = _number(_first_value(row, "change_percent", "change"))
    relative_volume = _number(_first_value(row, "relative_volume_10d_calc", "relative_volume"))
    volume = _number(row.get("volume"))
    value_traded = _number(_first_value(row, "Value.Traded", "total_value_traded", "turnover"))
    market_cap = _number(_first_value(row, "market_cap_basic", "market_cap"))

    score = 0.0
    if change_percent is not None:
        score += max(-12.0, min(18.0, change_percent * 1.2))
    if relative_volume is not None:
        score += max(0.0, min(12.0, relative_volume * 3.0))
    if value_traded is not None and value_traded > 0:
        score += min(8.0, value_traded / 50_000_000.0)

    return {
        "rank": index + 1,
        "symbol": symbol,
        "symbol_full": f"{exchange}:{symbol}" if exchange and symbol else symbol,
        "name": str(_first_value(row, "description", "name", "symbol") or symbol),
        "exchange": exchange,
        "last": close,
        "change_percent": change_percent,
        "change_abs": _number(_first_value(row, "change_abs", "change_absolute")),
        "volume": volume,
        "relative_volume": relative_volume,
        "value_traded": value_traded,
        "market_cap": market_cap,
        "sector": str(row.get("sector") or ""),
        "industry": str(row.get("industry") or ""),
        "currency": str(_first_value(row, "currency", "fundamental_currency_code") or ""),
        "tv_rating": "n/a",
        "tv_score": None,
        "zacks_rank": "n/a",
        "zacks_note": "",
        "fmp_recommendation": "n/a",
        "score": score,
        "sources": ["TradingView heatmap"],
        "source_count": 1,
        "notes": [],
        "errors": [],
    }


def _unique_sources(sources: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for source in sources:
        if source and source not in seen:
            result.append(source)
            seen.add(source)
    return result


def _finish_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    candidate["sources"] = _unique_sources(candidate.get("sources", []))
    candidate["source_count"] = len(candidate["sources"])
    notes = [str(item) for item in candidate.get("notes", []) if str(item or "").strip()]
    candidate["reason"] = _short_text(" · ".join(notes), 260)
    candidate["score"] = round(float(candidate.get("score") or 0.0), 2)
    return candidate


@router.get("/api/recommendations/stocks")
async def stock_recommendations(
    request: Request,
    limit: int = 25,
    enrich_limit: int = 8,
    include_fmp: bool = False,
):
    """Return source-ranked research candidates from read-only market data."""
    kernel = _kernel(request)

    def work() -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), MAX_CANDIDATES))
        bounded_enrich = max(0, min(int(enrich_limit), MAX_ENRICHED_CANDIDATES, bounded_limit))
        warnings: list[str] = []
        source_errors: list[dict[str, str]] = []

        heatmap = kernel.execute_mcp_tool(
            "tv_scrape_heatmap",
            {
                "limit": max(bounded_limit, bounded_enrich),
                "sort_by": "change",
                "include_premarket": True,
                "exclude_otc": True,
            },
        )
        rows_raw = heatmap.get("rows") if isinstance(heatmap, dict) else []
        rows = [row for row in rows_raw if isinstance(row, dict)]
        candidates = [_make_candidate(row, index) for index, row in enumerate(rows[:bounded_limit])]

        for candidate in candidates[:bounded_enrich]:
            symbol = str(candidate.get("symbol") or "").strip()
            exchange = str(candidate.get("exchange") or "").strip()
            if not symbol:
                continue

            try:
                tv_snapshot = kernel.execute_mcp_tool(
                    "tv_scrape_symbol_analytics",
                    {"symbol": symbol, "exchange": exchange or "NASDAQ"},
                )
                technicals = tv_snapshot.get("technicals") if isinstance(tv_snapshot, dict) else {}
                if isinstance(technicals, dict):
                    label = str(technicals.get("overall_label") or "Unknown")
                    score = technicals.get("overall_score")
                    if label and label != "Unknown":
                        candidate["tv_rating"] = label
                    candidate["tv_score"] = _number(score)
                    candidate["score"] += _technical_score_points(label, score)
                    candidate["sources"].append("TradingView technicals")
                    candidate["notes"].append(f"TradingView {candidate['tv_rating']}")
                analytics = tv_snapshot.get("analytics") if isinstance(tv_snapshot, dict) else {}
                if isinstance(analytics, dict):
                    candidate["sector"] = candidate["sector"] or str(analytics.get("sector") or "")
                    candidate["industry"] = candidate["industry"] or str(analytics.get("industry") or "")
                    candidate["market_cap"] = candidate["market_cap"] if candidate["market_cap"] is not None else _number(analytics.get("market_cap_basic"))
            except Exception as exc:
                detail = str(exc)
                candidate["errors"].append({"source": "TradingView technicals", "error": detail})
                source_errors.append({"source": "TradingView technicals", "symbol": symbol, "error": detail})

            try:
                zacks = kernel.execute_mcp_tool("zacks_scrape_symbol", {"symbol": symbol})
                if isinstance(zacks, dict):
                    zacks_text, zacks_note = _format_zacks(zacks)
                    candidate["zacks_rank"] = zacks_text
                    candidate["zacks_note"] = zacks_note
                    candidate["score"] += _zacks_score_points(zacks)
                    candidate["sources"].append("Zacks")
                    if zacks_text != "n/a":
                        candidate["notes"].append(f"Zacks {zacks_text}")
                    if zacks_note:
                        candidate["notes"].append(zacks_note)
            except Exception as exc:
                detail = str(exc)
                candidate["errors"].append({"source": "Zacks", "error": detail})
                source_errors.append({"source": "Zacks", "symbol": symbol, "error": detail})

            if include_fmp:
                if not os.getenv("FMP_API_KEY", "").strip():
                    if "FMP skipped: FMP_API_KEY is not configured." not in warnings:
                        warnings.append("FMP skipped: FMP_API_KEY is not configured.")
                else:
                    try:
                        fmp = kernel.execute_mcp_tool("fmp_analyst_recommendations", {"symbol": symbol, "limit": 1})
                        if isinstance(fmp, dict):
                            candidate["fmp_recommendation"] = _format_fmp(fmp)
                            candidate["score"] += _fmp_score_points(fmp)
                            candidate["sources"].append("FMP")
                            if candidate["fmp_recommendation"] != "n/a":
                                candidate["notes"].append(f"FMP {candidate['fmp_recommendation']}")
                    except Exception as exc:
                        detail = str(exc)
                        candidate["errors"].append({"source": "FMP", "error": detail})
                        source_errors.append({"source": "FMP", "symbol": symbol, "error": detail})

        finished = [_finish_candidate(candidate) for candidate in candidates]
        finished.sort(key=lambda row: (float(row.get("score") or 0.0), float(row.get("change_percent") or 0.0)), reverse=True)
        for index, candidate in enumerate(finished, start=1):
            candidate["rank"] = index

        if source_errors:
            warnings.append("Some enrichment sources failed; affected rows include per-source errors.")

        return {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "research_candidates",
            "universe": "TradingView public U.S. movers",
            "limit": bounded_limit,
            "enrich_limit": bounded_enrich,
            "sources": _unique_sources(["TradingView heatmap", "TradingView technicals", "Zacks"] + (["FMP"] if include_fmp else [])),
            "warnings": warnings,
            "source_errors": source_errors[:50],
            "rows": finished,
            "disclaimer": "Research candidates only. This is not investment advice and does not authorize order placement.",
        }

    try:
        return await asyncio.to_thread(work)
    except PermissionError as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    except Exception as exc:
        return JSONResponse({"error": "research_failed", "detail": str(exc)}, status_code=502)
