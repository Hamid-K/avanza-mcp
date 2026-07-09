"""SEC, FRED, FMP, and Polygon data-feed snapshots."""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from avanza_mcp import utils
from avanza_mcp.config import (
    FMP_ANALYST_RECOMMENDATIONS_URL_TEMPLATE,
    FRED_OBSERVATIONS_URL,
    POLYGON_ANALYST_INSIGHTS_URL,
    SEC_SUBMISSIONS_URL_TEMPLATE,
    SEC_TICKERS_URL,
)
from avanza_mcp.external import http as ext_http

def sec_cik_text(value: Any) -> str:
    raw = re.sub(r"[^0-9]", "", str(value or ""))
    if not raw:
        raise ValueError("CIK must contain digits.")
    return raw.zfill(10)


def sec_ticker_index() -> list[dict[str, Any]]:
    payload = ext_http.external_fetch_json(SEC_TICKERS_URL, headers={"Accept": "application/json"})
    rows = payload.get("data", [])
    fields = payload.get("fields", [])
    if not isinstance(rows, list) or not isinstance(fields, list):
        raise RuntimeError("Unexpected SEC ticker payload.")
    return [
        {str(fields[idx]): row[idx] if idx < len(row) else None for idx in range(len(fields))}
        for row in rows
        if isinstance(row, list)
    ]


def sec_lookup_cik(ticker: str | None = None, cik: str | None = None) -> tuple[str, dict[str, Any] | None]:
    if cik:
        return sec_cik_text(cik), None
    ticker_text = str(ticker or "").strip().upper()
    if not ticker_text:
        raise ValueError("ticker or cik is required.")
    for row in sec_ticker_index():
        if str(row.get("ticker", "")).strip().upper() == ticker_text:
            return sec_cik_text(row.get("cik")), row
    raise ValueError(f"Unknown SEC ticker: {ticker_text}")


def sec_recent_filings_snapshot(ticker: str | None, cik: str | None, limit: int = 20) -> dict[str, Any]:
    cik_text, company = sec_lookup_cik(ticker=ticker, cik=cik)
    payload = ext_http.external_fetch_json(
        SEC_SUBMISSIONS_URL_TEMPLATE.format(cik=cik_text),
        headers={"Accept": "application/json"},
    )
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", []) if isinstance(recent, dict) else []
    accessions = recent.get("accessionNumber", []) if isinstance(recent, dict) else []
    filing_dates = recent.get("filingDate", []) if isinstance(recent, dict) else []
    report_dates = recent.get("reportDate", []) if isinstance(recent, dict) else []
    primary_docs = recent.get("primaryDocument", []) if isinstance(recent, dict) else []
    rows = []
    max_len = min(len(forms), max(1, min(limit, 200)))
    for index in range(max_len):
        accession = str(accessions[index]) if index < len(accessions) else ""
        accession_url = ""
        if accession:
            accession_compact = accession.replace("-", "")
            accession_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik_text)}/{accession_compact}/"
                f"{primary_docs[index] if index < len(primary_docs) else ''}"
            )
        rows.append(
            {
                "form": forms[index] if index < len(forms) else "",
                "filing_date": filing_dates[index] if index < len(filing_dates) else "",
                "report_date": report_dates[index] if index < len(report_dates) else "",
                "accession_number": accession,
                "document": primary_docs[index] if index < len(primary_docs) else "",
                "url": accession_url,
            }
        )
    return {
        "ticker": str(ticker or company.get("ticker") if company else "").upper() or None,
        "cik": cik_text,
        "company_name": payload.get("name") or (company.get("name") if company else ""),
        "exchange": company.get("exchange") if company else "",
        "filings": rows,
        "source": "sec-edgar",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unsafe_for_execution": False,
    }


def fred_observations_snapshot(
    series_id: str,
    *,
    api_key: str | None = None,
    limit: int = 100,
    sort_order: str = "desc",
) -> dict[str, Any]:
    key = str(api_key or os.getenv("FRED_API_KEY", "")).strip()
    if not key:
        raise ValueError("FRED API key missing. Set FRED_API_KEY or pass api_key.")
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "sort_order": "asc" if str(sort_order).lower() == "asc" else "desc",
        "limit": str(max(1, min(limit, 1000))),
    }
    url = f"{FRED_OBSERVATIONS_URL}?{urlencode(params)}"
    payload = ext_http.external_fetch_json(url, headers={"Accept": "application/json"})
    observations = payload.get("observations", [])
    rows = []
    for item in observations if isinstance(observations, list) else []:
        if not isinstance(item, dict):
            continue
        value_raw = str(item.get("value", "")).strip()
        value = None if value_raw in {"", "."} else utils.scalar_number(value_raw)
        rows.append({"date": item.get("date"), "value": value, "value_raw": value_raw})
    return {
        "series_id": series_id,
        "title": payload.get("title"),
        "units": payload.get("units"),
        "frequency": payload.get("frequency"),
        "observations": rows,
        "source": "fred",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unsafe_for_execution": False,
    }



def fmp_analyst_recommendations_snapshot(
    symbol: str,
    *,
    api_key: str | None = None,
    limit: int = 52,
) -> dict[str, Any]:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        raise ValueError("symbol is required.")
    resolved_key = str(api_key or os.getenv("FMP_API_KEY", "")).strip()
    if not resolved_key:
        raise ValueError("FMP API key missing. Pass api_key or set FMP_API_KEY.")
    url = f"{FMP_ANALYST_RECOMMENDATIONS_URL_TEMPLATE.format(symbol=ticker)}?apikey={resolved_key}"
    payload = json.loads(ext_http.external_fetch_text(url, headers={"Accept": "application/json"}))
    rows_raw: list[Any]
    if isinstance(payload, list):
        rows_raw = payload
    elif isinstance(payload, dict):
        nested = payload.get("data")
        rows_raw = nested if isinstance(nested, list) else []
    else:
        rows_raw = []

    rows: list[dict[str, Any]] = []
    for item in rows_raw[: max(1, min(limit, 5000))]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "date": str(item.get("date", "") or ""),
                "symbol": str(item.get("symbol", ticker) or ticker),
                "strong_buy": int(item.get("strongBuy", 0) or 0),
                "buy": int(item.get("buy", 0) or 0),
                "hold": int(item.get("hold", 0) or 0),
                "sell": int(item.get("sell", 0) or 0),
                "strong_sell": int(item.get("strongSell", 0) or 0),
            }
        )

    latest = rows[0] if rows else None
    return {
        "symbol": ticker,
        "rows": rows,
        "latest": latest,
        "source": "fmp-analyst-stock-recommendations",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unsafe_for_execution": False,
    }


def polygon_analyst_insights_snapshot(
    symbol: str,
    *,
    api_key: str | None = None,
    limit: int = 50,
    date_value: str | None = None,
) -> dict[str, Any]:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        raise ValueError("symbol is required.")
    resolved_key = str(api_key or os.getenv("POLYGON_API_KEY", "")).strip()
    if not resolved_key:
        raise ValueError("Polygon API key missing. Pass api_key or set POLYGON_API_KEY.")
    params: dict[str, str] = {
        "ticker": ticker,
        "limit": str(max(1, min(limit, 5000))),
        "apiKey": resolved_key,
        "sort": "-date",
    }
    if str(date_value or "").strip():
        params["date"] = str(date_value).strip()
    url = f"{POLYGON_ANALYST_INSIGHTS_URL}?{urlencode(params)}"
    payload = ext_http.external_fetch_json(url, headers={"Accept": "application/json"})

    results_raw = payload.get("results", []) if isinstance(payload, dict) else []
    if not isinstance(results_raw, list):
        results_raw = []
    rows: list[dict[str, Any]] = []
    for item in results_raw:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "date": str(item.get("date", "") or ""),
                "ticker": str(item.get("ticker", ticker) or ticker),
                "firm": str(item.get("firm", "") or ""),
                "rating": str(item.get("rating", "") or ""),
                "rating_action": str(item.get("rating_action", "") or ""),
                "price_target": utils.scalar_number(item.get("price_target")),
                "insight": str(item.get("insight", "") or ""),
                "company_name": str(item.get("company_name", "") or ""),
                "last_updated": str(item.get("last_updated", "") or ""),
            }
        )
    return {
        "symbol": ticker,
        "status": str(payload.get("status", "") or ""),
        "count": len(rows),
        "rows": rows,
        "next_url": str(payload.get("next_url", "") or ""),
        "request_id": str(payload.get("request_id", "") or ""),
        "source": "polygon-benzinga-analyst-insights",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unsafe_for_execution": False,
    }
