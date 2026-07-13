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
    symbol = str(_first_value(row, "display_symbol", "symbol", "ticker", "name") or "").strip().upper()
    if ":" in symbol:
        symbol = symbol.split(":", 1)[1]
    return symbol


def _exchange_from_row(row: dict[str, Any]) -> str:
    return str(_first_value(row, "exchange", "market", "market_place", "marketPlace") or "").strip().upper()


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("rows", "items", "symbols", "data", "constituents"):
        raw = payload.get(key)
        if isinstance(raw, list):
            rows = [row for row in raw if isinstance(row, dict)]
            if rows:
                return rows
    for key in ("result", "payload"):
        nested = payload.get(key)
        rows = _payload_rows(nested)
        if rows:
            return rows
    return []


def _avanza_market_mover_rows(kernel: Any, limit: int, source_errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    try:
        movers = kernel.execute_mcp_tool(
            "avanza_market_movers",
            {
                "countryCodes": ["SE"],
                "min_price": 5,
                "min_total_value_traded": 5_000_000,
                "limit": max(1, min(limit, MAX_CANDIDATES)),
            },
        )
    except Exception as exc:
        source_errors.append({"source": "Avanza market movers", "symbol": "", "error": str(exc)})
        return []

    rows: list[dict[str, Any]] = []
    if isinstance(movers, dict):
        for bucket in ("gainers", "losers"):
            raw_rows = movers.get(bucket)
            if not isinstance(raw_rows, list):
                continue
            for raw in raw_rows:
                if not isinstance(raw, dict):
                    continue
                row = dict(raw)
                row["_source"] = "Avanza market movers"
                row.setdefault("symbol", _first_value(row, "display_symbol", "ticker", "name"))
                row.setdefault("description", _first_value(row, "name", "stock", "Stock"))
                row.setdefault("last", _first_value(row, "last_price", "last", "price"))
                row.setdefault("change_percent", _first_value(row, "one_day_change_percent", "change_percent", "change"))
                row.setdefault("Value.Traded", _first_value(row, "total_value_traded", "turnover"))
                rows.append(row)
    return rows[: max(1, min(limit, MAX_CANDIDATES))]


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


def _zacks_missing_detail(snapshot: dict[str, Any]) -> str:
    if snapshot.get("blocked"):
        blocked_sources = snapshot.get("blocked_sources") if isinstance(snapshot.get("blocked_sources"), list) else []
        suffix = f" ({', '.join(str(item) for item in blocked_sources if item)})" if blocked_sources else ""
        return f"Zacks returned no visible rank/summary; source appears blocked{suffix}."
    error = str(snapshot.get("analysis_error") or "").strip()
    if error:
        return f"Zacks returned no visible rank/summary; analysis fetch failed: {_short_text(error, 140)}"
    return "Zacks returned no visible rank/summary for this symbol."


def _format_fmp(snapshot: dict[str, Any]) -> str:
    latest = snapshot.get("latest") if isinstance(snapshot.get("latest"), dict) else {}
    if not latest:
        return "n/a"
    return (
        f"SB {latest.get('strong_buy', 0)} / B {latest.get('buy', 0)} / "
        f"H {latest.get('hold', 0)} / S {latest.get('sell', 0)}"
    )


def _make_candidate(row: dict[str, Any], index: int, source: str = "TradingView heatmap") -> dict[str, Any]:
    source = str(row.get("_source") or source or "TradingView heatmap")
    symbol = _symbol_from_row(row)
    exchange = _exchange_from_row(row)
    close = _number(_first_value(row, "close", "last", "last_price", "premarket_close", "postmarket_close"))
    change_percent = _number(_first_value(row, "change_percent", "one_day_change_percent", "change"))
    relative_volume = _number(_first_value(row, "relative_volume_10d_calc", "relative_volume"))
    volume = _number(_first_value(row, "volume", "day_volume"))
    value_traded = _number(_first_value(row, "Value.Traded", "total_value_traded", "turnover"))
    market_cap = _number(_first_value(row, "market_cap_basic", "market_cap"))

    score = 0.0
    if change_percent is not None:
        score += max(-12.0, min(18.0, change_percent * 1.2))
    if relative_volume is not None:
        score += max(0.0, min(12.0, relative_volume * 3.0))
    if value_traded is not None and value_traded > 0:
        score += min(8.0, value_traded / 50_000_000.0)

    notes: list[str] = []
    if change_percent is not None:
        notes.append(f"{source} {change_percent:+.2f}% day")
    if relative_volume is not None:
        notes.append(f"rel vol {relative_volume:.2f}x")
    if value_traded is not None:
        notes.append(f"value traded {value_traded:,.0f}")
    elif volume is not None:
        notes.append(f"volume {volume:,.0f}")
    sector = str(row.get("sector") or "")
    if sector:
        notes.append(sector)
    if not notes:
        notes.append(source)

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
        "sector": sector,
        "industry": str(row.get("industry") or ""),
        "currency": str(_first_value(row, "currency", "fundamental_currency_code") or ""),
        "tv_rating": "n/a",
        "tv_score": None,
        "zacks_rank": "n/a",
        "zacks_note": "",
        "zacks_error": "",
        "fmp_recommendation": "n/a",
        "score": score,
        "sources": [source],
        "source_count": 1,
        "notes": notes,
        "errors": [],
        "_enrich_symbol_sources": source == "TradingView heatmap",
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
    candidate.pop("_enrich_symbol_sources", None)
    candidate["sources"] = _unique_sources(candidate.get("sources", []))
    candidate["source_count"] = len(candidate["sources"])
    notes = [str(item) for item in candidate.get("notes", []) if str(item or "").strip()]
    if not notes:
        notes = [str(item) for item in candidate["sources"] if str(item or "").strip()]
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
        source_health: dict[str, dict[str, int]] = {}

        def mark_source(source: str, outcome: str) -> None:
            health = source_health.setdefault(source, {"attempted": 0, "succeeded": 0, "failed": 0})
            if outcome == "attempted":
                health["attempted"] += 1
            elif outcome in {"succeeded", "failed"}:
                health[outcome] += 1

        heatmap = kernel.execute_mcp_tool(
            "tv_scrape_heatmap",
            {
                "limit": max(bounded_limit, bounded_enrich),
                "sort_by": "change",
                "include_premarket": True,
                "exclude_otc": True,
            },
        )
        rows = _payload_rows(heatmap)
        fallback_used = False
        if not rows:
            rows_before_filter = heatmap.get("rows_before_filter") if isinstance(heatmap, dict) else None
            detail = f" ({rows_before_filter} raw rows before filters)" if rows_before_filter is not None else ""
            warnings.append(f"TradingView heatmap returned no filtered candidates{detail}; trying Avanza market movers fallback.")
            rows = _avanza_market_mover_rows(kernel, bounded_limit, source_errors)
            fallback_used = bool(rows)
            if not rows:
                warnings.append("No research candidates returned by TradingView heatmap or Avanza market movers.")
        candidates = [_make_candidate(row, index) for index, row in enumerate(rows[:bounded_limit])]

        for candidate in candidates[:bounded_enrich]:
            if not candidate.get("_enrich_symbol_sources", True):
                continue
            symbol = str(candidate.get("symbol") or "").strip()
            exchange = str(candidate.get("exchange") or "").strip()
            if not symbol:
                continue

            mark_source("TradingView technicals", "attempted")
            try:
                tv_snapshot = kernel.execute_mcp_tool(
                    "tv_scrape_symbol_analytics",
                    {"symbol": symbol, "exchange": exchange or "NASDAQ"},
                )
                if not isinstance(tv_snapshot, dict):
                    raise RuntimeError("TradingView technicals returned an invalid payload.")
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
                mark_source("TradingView technicals", "succeeded")
            except Exception as exc:
                detail = str(exc)
                mark_source("TradingView technicals", "failed")
                candidate["errors"].append({"source": "TradingView technicals", "error": detail})
                source_errors.append({"source": "TradingView technicals", "symbol": symbol, "error": detail})

            mark_source("Zacks", "attempted")
            try:
                zacks = kernel.execute_mcp_tool("zacks_scrape_symbol", {"symbol": symbol})
                if isinstance(zacks, dict):
                    zacks_text, zacks_note = _format_zacks(zacks)
                    candidate["zacks_rank"] = zacks_text
                    candidate["zacks_note"] = zacks_note
                    if zacks_text != "n/a" or zacks_note:
                        candidate["score"] += _zacks_score_points(zacks)
                        candidate["sources"].append("Zacks")
                        if zacks_text != "n/a":
                            candidate["notes"].append(f"Zacks {zacks_text}")
                        if zacks_note:
                            candidate["notes"].append(zacks_note)
                        mark_source("Zacks", "succeeded")
                    else:
                        detail = _zacks_missing_detail(zacks)
                        mark_source("Zacks", "failed")
                        candidate["zacks_error"] = detail
                        candidate["errors"].append({"source": "Zacks", "error": detail})
                        source_errors.append({"source": "Zacks", "symbol": symbol, "error": detail})
                else:
                    detail = "Zacks returned an invalid payload."
                    mark_source("Zacks", "failed")
                    candidate["zacks_error"] = detail
                    candidate["errors"].append({"source": "Zacks", "error": detail})
                    source_errors.append({"source": "Zacks", "symbol": symbol, "error": detail})
            except Exception as exc:
                detail = str(exc)
                mark_source("Zacks", "failed")
                candidate["zacks_error"] = detail
                candidate["errors"].append({"source": "Zacks", "error": detail})
                source_errors.append({"source": "Zacks", "symbol": symbol, "error": detail})

            if include_fmp:
                if not os.getenv("FMP_API_KEY", "").strip():
                    if "FMP skipped: FMP_API_KEY is not configured." not in warnings:
                        warnings.append("FMP skipped: FMP_API_KEY is not configured.")
                else:
                    mark_source("FMP", "attempted")
                    try:
                        fmp = kernel.execute_mcp_tool("fmp_analyst_recommendations", {"symbol": symbol, "limit": 1})
                        if isinstance(fmp, dict):
                            candidate["fmp_recommendation"] = _format_fmp(fmp)
                            candidate["score"] += _fmp_score_points(fmp)
                            candidate["sources"].append("FMP")
                            if candidate["fmp_recommendation"] != "n/a":
                                candidate["notes"].append(f"FMP {candidate['fmp_recommendation']}")
                            mark_source("FMP", "succeeded")
                        else:
                            raise RuntimeError("FMP returned an invalid payload.")
                    except Exception as exc:
                        detail = str(exc)
                        mark_source("FMP", "failed")
                        candidate["errors"].append({"source": "FMP", "error": detail})
                        source_errors.append({"source": "FMP", "symbol": symbol, "error": detail})

        finished = [_finish_candidate(candidate) for candidate in candidates]
        finished.sort(key=lambda row: (float(row.get("score") or 0.0), float(row.get("change_percent") or 0.0)), reverse=True)
        for index, candidate in enumerate(finished, start=1):
            candidate["rank"] = index

        health_rows = [
            {"source": source, **counts}
            for source, counts in source_health.items()
        ]
        result = {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "research_candidates",
            "universe": "Avanza Swedish market movers fallback" if fallback_used else "TradingView public U.S. movers",
            "limit": bounded_limit,
            "enrich_limit": bounded_enrich,
            "sources": _unique_sources(
                [
                    source
                    for candidate in finished
                    for source in candidate.get("sources", [])
                ]
                or ["TradingView heatmap"]
            ),
            "warnings": warnings,
            "source_errors": source_errors[:50],
            "source_health": health_rows,
            "rows": finished,
            "disclaimer": "Research candidates only. This is not investment advice and does not authorize order placement.",
        }
        kernel.record_event(
            "app",
            "research_candidates_loaded",
            {
                "rows": len(finished),
                "source_health": health_rows,
                "source_errors": source_errors[:50],
                "warnings": warnings,
            },
        )
        return result

    try:
        return await asyncio.to_thread(work)
    except PermissionError as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    except Exception as exc:
        return JSONResponse({"error": "research_failed", "detail": str(exc)}, status_code=502)
