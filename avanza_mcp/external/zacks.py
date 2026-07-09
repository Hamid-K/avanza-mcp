"""Zacks HTML parsing and symbol snapshot."""

import html
import re
from datetime import datetime, timezone
from typing import Any

from avanza_mcp.external import http as ext_http
from avanza_mcp.external.http import (
    append_cookie_header,
    bounded_text,
    html_document_text,
    html_meta_content,
    html_title_text,
)

def zacks_blocked_html(html_text: str) -> bool:
    lowered = html_text.lower()
    return any(
        marker in lowered
        for marker in (
            "pardon our interruption",
            "access denied",
            "request blocked",
            "verify you are a human",
            "enable javascript and cookies",
        )
    )


ZACKS_ANALYSIS_HEADINGS = (
    "Summary",
    "Company Summary",
    "Investment Thesis",
    "Valuation",
    "Industry Analysis",
    "Earnings Estimate Revisions",
    "Zacks Rank",
    "Style Scores",
    "Growth Score",
    "Value Score",
    "Momentum Score",
)
ZACKS_RANK_LABELS = {
    1: "Strong Buy",
    2: "Buy",
    3: "Hold",
    4: "Sell",
    5: "Strong Sell",
}


def _safe_int(value: Any) -> int | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        number = int(float(text))
    except (TypeError, ValueError):
        return None
    return number


def zacks_quote_feed_snapshot(symbol: str) -> dict[str, Any]:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        raise ValueError("symbol is required.")
    url = f"https://quote-feed.zacks.com/index?t={ticker}"
    data = ext_http.external_fetch_json(url)
    row = data.get(ticker)
    if not isinstance(row, dict):
        row = next((item for item in data.values() if isinstance(item, dict)), {})
    if not isinstance(row, dict):
        row = {}
    rank_value = _safe_int(row.get("zacks_rank"))
    rank_label = str(row.get("zacks_rank_text") or "").strip()
    if rank_value and not rank_label:
        rank_label = ZACKS_RANK_LABELS.get(rank_value, "")
    return {
        "source": "zacks-quote-feed",
        "url": url,
        "symbol": str(row.get("ticker") or ticker).upper(),
        "name": str(row.get("name") or row.get("company_short_name") or "").strip(),
        "rank": {"value": rank_value, "label": rank_label or None},
        "updated": str(row.get("updated") or "").strip() or None,
        "last": row.get("last"),
        "previous_close": row.get("previous_close"),
        "percent_net_change": row.get("percent_net_change"),
        "raw": row,
    }


def zacks_section_excerpt(document_text: str, heading: str, max_chars: int = 900) -> str:
    text = str(document_text or "")
    if not text:
        return ""
    match = re.search(rf"(?im)^\s*{re.escape(heading)}\s*$", text)
    if not match:
        return ""
    start = match.end()
    stop = len(text)
    for candidate in ZACKS_ANALYSIS_HEADINGS:
        if candidate.lower() == heading.lower():
            continue
        candidate_match = re.search(rf"(?im)^\s*{re.escape(candidate)}\s*$", text[start:])
        if candidate_match:
            stop = min(stop, start + candidate_match.start())
    return bounded_text(text[start:stop], max_chars)


def zacks_analysis_summary_from_html(
    html_text: str,
    *,
    source_url: str,
    max_chars: int = 2500,
) -> dict[str, Any]:
    document_text = html_document_text(html_text)
    title = html_title_text(html_text)
    description = html_meta_content(html_text, "description") or html_meta_content(html_text, "og:description")
    sections = []
    seen_headings: set[str] = set()
    for heading in ZACKS_ANALYSIS_HEADINGS:
        excerpt = zacks_section_excerpt(document_text, heading)
        if excerpt and heading.lower() not in seen_headings:
            sections.append({"heading": heading, "text": excerpt})
            seen_headings.add(heading.lower())
    summary_parts = []
    if description:
        summary_parts.append(description)
    summary_parts.extend(f"{section['heading']}: {section['text']}" for section in sections)
    summary = bounded_text(" ".join(summary_parts), max_chars)
    return {
        "available": bool(summary),
        "source_url": source_url,
        "title": title,
        "meta_description": description,
        "summary": summary or None,
        "sections": sections,
    }


def zacks_symbol_snapshot(symbol: str, cookie: str = "") -> dict[str, Any]:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        raise ValueError("symbol is required.")
    url = f"https://www.zacks.com/stock/quote/{ticker}"
    report_url = f"https://www.zacks.com/zer/report/{ticker}?rwid=Y"
    headers = append_cookie_header({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}, cookie)
    html_text = ext_http.external_fetch_text(url, headers=headers)
    quote_blocked = zacks_blocked_html(html_text)
    rank_match = re.search(r"Zacks\s*Rank\s*#\s*([1-5])\s*\(([^)]+)\)", html_text, re.IGNORECASE)
    industry_rank_match = re.search(r"Industry Rank\s*:?\s*#?\s*([0-9]+)", html_text, re.IGNORECASE)
    esp_match = re.search(r"Earnings\s+ESP\s*:?\s*([+-]?[0-9]+(?:\.[0-9]+)?%)", html_text, re.IGNORECASE)
    quote_analysis = zacks_analysis_summary_from_html(html_text, source_url=url)
    analysis_sources = [{"kind": "quote", "blocked": quote_blocked, "analysis": quote_analysis}]
    blocked_sources = ["quote"] if quote_blocked else []
    report_error = ""
    if cookie or not quote_blocked:
        try:
            report_html = ext_http.external_fetch_text(report_url, headers=headers)
            report_blocked = zacks_blocked_html(report_html)
            report_analysis = zacks_analysis_summary_from_html(report_html, source_url=report_url)
            analysis_sources.append({"kind": "equity_report", "blocked": report_blocked, "analysis": report_analysis})
            if report_blocked:
                blocked_sources.append("equity_report")
        except Exception as exc:
            report_error = str(exc)
            analysis_sources.append(
                {
                    "kind": "equity_report",
                    "blocked": False,
                    "error": report_error,
                    "analysis": {
                        "available": False,
                        "source_url": report_url,
                        "title": "",
                        "meta_description": "",
                        "summary": None,
                        "sections": [],
                    },
                }
            )
    selected_analysis = next(
        (source["analysis"] for source in reversed(analysis_sources) if source.get("analysis", {}).get("available")),
        {
            "available": False,
            "source_url": report_url if cookie else url,
            "title": "",
            "meta_description": "",
            "summary": None,
            "sections": [],
        },
    )
    quote_feed: dict[str, Any] = {}
    quote_feed_error = ""
    if not rank_match:
        try:
            quote_feed = zacks_quote_feed_snapshot(ticker)
        except Exception as exc:
            quote_feed_error = str(exc)
    quote_feed_rank = quote_feed.get("rank") if isinstance(quote_feed.get("rank"), dict) else {}
    rank_value = int(rank_match.group(1)) if rank_match else _safe_int(quote_feed_rank.get("value"))
    rank_label = (
        html.unescape(rank_match.group(2)).strip()
        if rank_match
        else str(quote_feed_rank.get("label") or "").strip() or (ZACKS_RANK_LABELS.get(rank_value or 0, "") if rank_value else "")
    )
    rank_source = "html" if rank_match else "quote-feed" if rank_value else None
    blocked = quote_blocked and not selected_analysis.get("available") and not rank_value
    return {
        "symbol": ticker,
        "url": url,
        "report_url": report_url,
        "quote_feed_url": f"https://quote-feed.zacks.com/index?t={ticker}",
        "blocked": blocked,
        "blocked_sources": blocked_sources,
        "rank": {
            "value": rank_value,
            "label": rank_label or None,
        },
        "rank_source": rank_source,
        "industry_rank": int(industry_rank_match.group(1)) if industry_rank_match else None,
        "earnings_esp": esp_match.group(1) if esp_match else None,
        "analysis_summary": selected_analysis,
        "analysis_sources": analysis_sources,
        "analysis_error": report_error or None,
        "quote_feed": quote_feed,
        "quote_feed_error": quote_feed_error or None,
        "source": "zacks-web",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unsafe_for_execution": blocked,
    }
