"""GitHub release/tag version checking."""

import json
import os
import re
from urllib.error import HTTPError

from avanza_mcp.config import GITHUB_RELEASE_REPO, UPDATE_CHECK_TIMEOUT_SECONDS
from avanza_mcp.external import http as ext_http

def update_check_enabled() -> bool:
    return str(os.getenv("AVANZA_UPDATE_CHECK_ENABLED", "1")).strip().lower() not in {"0", "false", "no"}


def normalize_version_text(value: str) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    return text


def version_tuple(value: str) -> tuple[int, ...] | None:
    normalized = normalize_version_text(value)
    if not normalized:
        return None
    parts = normalized.split(".")
    numbers: list[int] = []
    for part in parts:
        match = re.match(r"^(\d+)", part)
        if not match:
            break
        numbers.append(int(match.group(1)))
    return tuple(numbers) if numbers else None


def is_version_outdated(current: str, latest: str) -> bool:
    current_tuple = version_tuple(current)
    latest_tuple = version_tuple(latest)
    if not current_tuple or not latest_tuple:
        return False
    width = max(len(current_tuple), len(latest_tuple))
    current_padded = current_tuple + (0,) * (width - len(current_tuple))
    latest_padded = latest_tuple + (0,) * (width - len(latest_tuple))
    return current_padded < latest_padded


def github_latest_version_info(repo: str = GITHUB_RELEASE_REPO) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    release_url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        text = ext_http.external_fetch_text(
            release_url,
            headers=headers,
            timeout_seconds=UPDATE_CHECK_TIMEOUT_SECONDS,
        )
        payload = json.loads(text)
        if isinstance(payload, dict):
            tag = str(payload.get("tag_name", "") or payload.get("name", "")).strip()
            if tag:
                return {
                    "version": normalize_version_text(tag),
                    "tag": tag,
                    "url": str(payload.get("html_url", "") or ""),
                    "source": "release",
                }
    except HTTPError as exc:
        if int(getattr(exc, "code", 0)) != 404:
            raise

    tags_url = f"https://api.github.com/repos/{repo}/tags?per_page=1"
    tags_text = ext_http.external_fetch_text(
        tags_url,
        headers=headers,
        timeout_seconds=UPDATE_CHECK_TIMEOUT_SECONDS,
    )
    tags_payload = json.loads(tags_text)
    if isinstance(tags_payload, list) and tags_payload:
        first = tags_payload[0]
        if isinstance(first, dict):
            tag = str(first.get("name", "")).strip()
            if tag:
                return {
                    "version": normalize_version_text(tag),
                    "tag": tag,
                    "url": str(first.get("zipball_url", "") or ""),
                    "source": "tag",
                }
    raise RuntimeError(f"No GitHub release/tag data found for {repo}.")
