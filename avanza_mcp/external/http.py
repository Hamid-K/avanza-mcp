"""Generic external HTTP fetch helpers and HTML/cookie utilities."""

import html
import json
import re
from typing import Any
from urllib.request import Request, urlopen

from avanza_mcp.config import EXTERNAL_HTTP_TIMEOUT_SECONDS, EXTERNAL_HTTP_USER_AGENT

def external_http_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"User-Agent": EXTERNAL_HTTP_USER_AGENT, "Accept": "application/json,text/plain,*/*"}
    if extra:
        headers.update({str(key): str(value) for key, value in extra.items() if value is not None})
    return headers


def external_fetch_text(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: Any | None = None,
    timeout_seconds: float = EXTERNAL_HTTP_TIMEOUT_SECONDS,
) -> str:
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method.upper(), headers=external_http_headers(headers))
    with urlopen(request, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def external_fetch_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: Any | None = None,
    timeout_seconds: float = EXTERNAL_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    text = external_fetch_text(
        url,
        method=method,
        headers=headers,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected object JSON from {url}.")
    return data


def append_cookie_header(headers: dict[str, str], cookie: str | None) -> dict[str, str]:
    if not cookie:
        return headers
    merged = dict(headers)
    merged["Cookie"] = cookie.strip()
    return merged


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def html_meta_content(html_text: str, key: str) -> str:
    key_pattern = re.escape(str(key or "").strip())
    if not key_pattern:
        return ""
    patterns = (
        rf"<meta[^>]+(?:name|property)=['\"]{key_pattern}['\"][^>]+content=['\"]([^'\"]+)['\"][^>]*>",
        rf"<meta[^>]+content=['\"]([^'\"]+)['\"][^>]+(?:name|property)=['\"]{key_pattern}['\"][^>]*>",
    )
    for pattern in patterns:
        match = re.search(pattern, html_text, re.IGNORECASE)
        if match:
            return normalize_text(html.unescape(match.group(1)))
    return ""


def html_title_text(html_text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    return normalize_text(html.unescape(match.group(1))) if match else ""


def html_document_text(html_text: str) -> str:
    text = re.sub(r"(?is)<(script|style|svg|canvas|iframe|noscript)\b.*?</\1>", " ", html_text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|section|article|li|tr|h[1-6])>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [normalize_text(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def bounded_text(value: str, max_chars: int) -> str:
    text = normalize_text(value)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "..."



def mask_secret(value: str, keep: int = 4) -> str:
    text = str(value or "")
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]


def parse_cookie_value(cookie: str, key: str) -> str:
    pattern = re.compile(rf"(?:^|;\s*){re.escape(key)}=([^;]+)")
    match = pattern.search(cookie)
    return match.group(1) if match else ""
