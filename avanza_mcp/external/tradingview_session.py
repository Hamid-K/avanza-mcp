"""TradingView session management: cookies, keychain, persisted session, auto-login."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from avanza_mcp import config
from avanza_mcp.config import (
    EXTERNAL_HTTP_USER_AGENT,
    TRADINGVIEW_BROWSER_PROFILE_DIR,
    TRADINGVIEW_KEYCHAIN_SERVICE,
    TRADINGVIEW_LOGIN_URL,
)
from avanza_mcp.external.http import mask_secret, parse_cookie_value

def tradingview_cookie_from_browser_cookies(cookies: list[dict[str, Any]]) -> str:
    sessionid = ""
    sessionid_sign = ""
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        value = str(item.get("value", "")).strip()
        domain = str(item.get("domain", "")).lower()
        if "tradingview.com" not in domain and domain:
            continue
        if name == "sessionid" and value:
            sessionid = value
        elif name == "sessionid_sign" and value:
            sessionid_sign = value
    if sessionid and sessionid_sign:
        return f"sessionid={sessionid}; sessionid_sign={sessionid_sign}"
    if sessionid:
        return f"sessionid={sessionid}"
    return ""


def tradingview_session_backend() -> str:
    value = str(os.getenv("AVANZA_TV_SESSION_BACKEND", "auto") or "auto").strip().lower()
    if value in {"keychain", "file", "auto"}:
        return value
    return "auto"


def tradingview_keychain_supported() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None


def tradingview_keychain_account(path: Path) -> str:
    scope = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"tradingview_session::{scope}"


def tradingview_keychain_get_cookie(path: Path) -> str:
    if not tradingview_keychain_supported():
        return ""
    account = tradingview_keychain_account(path)
    result = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", TRADINGVIEW_KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


def tradingview_keychain_set_cookie(path: Path, cookie: str) -> tuple[bool, str]:
    if not tradingview_keychain_supported():
        return False, "keychain not supported"
    account = tradingview_keychain_account(path)
    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-a",
            account,
            "-s",
            TRADINGVIEW_KEYCHAIN_SERVICE,
            "-w",
            cookie,
            "-U",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    error = str(result.stderr or result.stdout or "").strip()
    return False, error or f"security exited with {result.returncode}"


def tradingview_keychain_delete_cookie(path: Path) -> tuple[bool, str]:
    if not tradingview_keychain_supported():
        return False, "keychain not supported"
    account = tradingview_keychain_account(path)
    result = subprocess.run(
        ["security", "delete-generic-password", "-a", account, "-s", TRADINGVIEW_KEYCHAIN_SERVICE],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    error = str(result.stderr or result.stdout or "").strip().lower()
    if "could not be found" in error or "item not found" in error:
        return False, ""
    return False, error or f"security exited with {result.returncode}"


def load_tradingview_session_metadata(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def save_tradingview_session_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass


def load_tradingview_session(path: Path | None = None) -> dict[str, Any]:
    session_path = path or config.TRADINGVIEW_SESSION_FILE
    payload = load_tradingview_session_metadata(session_path)
    cookie = str(payload.get("cookie", "") or "").strip()
    storage = str(payload.get("storage", "") or "").strip().lower()
    if not cookie and storage == "keychain":
        cookie = tradingview_keychain_get_cookie(session_path)
    if not cookie and not payload:
        cookie = tradingview_keychain_get_cookie(session_path)
        if cookie:
            storage = "keychain"
    if not storage:
        storage = "file" if cookie else "none"
    return {
        "cookie": cookie,
        "created_at": str(payload.get("created_at", "") or ""),
        "updated_at": str(payload.get("updated_at", "") or ""),
        "source": str(payload.get("source", "") or ""),
        "storage": storage,
        "path": str(session_path),
    }


def save_tradingview_session(cookie: str, *, source: str = "manual", path: Path | None = None) -> dict[str, Any]:
    clean_cookie = str(cookie or "").strip()
    if not clean_cookie:
        raise ValueError("TradingView cookie cannot be empty.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = load_tradingview_session(path)
    session_path = path or config.TRADINGVIEW_SESSION_FILE
    backend = tradingview_session_backend()
    storage = "file"
    keychain_error = ""
    if backend in {"auto", "keychain"}:
        keychain_saved, keychain_error = tradingview_keychain_set_cookie(session_path, clean_cookie)
        if keychain_saved:
            storage = "keychain"
        elif backend == "keychain":
            raise RuntimeError(f"Could not save TradingView session in keychain: {keychain_error}")

    payload: dict[str, Any] = {
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "source": source,
        "storage": storage,
    }
    if storage == "file":
        payload["cookie"] = clean_cookie
    save_tradingview_session_metadata(session_path, payload)
    return {
        "saved": True,
        "path": str(session_path),
        "updated_at": now,
        "source": source,
        "storage": storage,
        "backend": backend,
        "keychain_error": keychain_error,
        "has_sessionid": bool(parse_cookie_value(clean_cookie, "sessionid")),
        "has_sessionid_sign": bool(parse_cookie_value(clean_cookie, "sessionid_sign")),
        "cookie_preview": mask_secret(clean_cookie, keep=8),
    }


def clear_tradingview_session(path: Path | None = None) -> bool:
    session_path = path or config.TRADINGVIEW_SESSION_FILE
    deleted_file = False
    if session_path.exists():
        session_path.unlink(missing_ok=True)
        deleted_file = True
    deleted_keychain, _ = tradingview_keychain_delete_cookie(session_path)
    return deleted_file or deleted_keychain


def tradingview_cookie_from_inputs(arguments: dict[str, Any], stored_session: dict[str, Any] | None = None) -> str:
    explicit_cookie = str(arguments.get("cookie", "") or "").strip()
    if explicit_cookie:
        return explicit_cookie
    sessionid = str(arguments.get("sessionid", "") or os.getenv("TRADINGVIEW_SESSIONID", "")).strip()
    sessionid_sign = str(arguments.get("sessionid_sign", "") or os.getenv("TRADINGVIEW_SESSIONID_SIGN", "")).strip()
    if sessionid and sessionid_sign:
        return f"sessionid={sessionid}; sessionid_sign={sessionid_sign}"
    if sessionid:
        return f"sessionid={sessionid}"
    saved = stored_session if stored_session is not None else load_tradingview_session()
    if isinstance(saved, dict):
        saved_cookie = str(saved.get("cookie", "") or "").strip()
        if saved_cookie:
            return saved_cookie
    return ""


def tradingview_session_status(path: Path | None = None) -> dict[str, Any]:
    session_path = path or config.TRADINGVIEW_SESSION_FILE
    session = load_tradingview_session(session_path)
    cookie = str(session.get("cookie", "") or "")
    storage = str(session.get("storage", "") or "none")
    if not cookie:
        return {
            "configured": False,
            "path": str(session_path),
            "storage": storage,
            "backend": tradingview_session_backend(),
            "message": "No saved TradingView session cookie.",
        }
    return {
        "configured": True,
        "path": str(session_path),
        "storage": storage,
        "backend": tradingview_session_backend(),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "source": session.get("source"),
        "has_sessionid": bool(parse_cookie_value(cookie, "sessionid")),
        "has_sessionid_sign": bool(parse_cookie_value(cookie, "sessionid_sign")),
        "cookie_preview": mask_secret(cookie, keep=8),
    }


def tradingview_auto_login_and_capture_session(
    *,
    timeout_seconds: int = 300,
    profile_dir: Path | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "Playwright is required for browser-instrumented TradingView login. "
            "Install with: uv add --dev playwright && uv run playwright install chromium"
        ) from exc

    target_profile_dir = profile_dir or TRADINGVIEW_BROWSER_PROFILE_DIR
    target_profile_dir.mkdir(parents=True, exist_ok=True)
    timeout = max(30, min(int(timeout_seconds), 1800))
    deadline = time.time() + timeout
    last_error = ""

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(target_profile_dir),
            headless=False,
            viewport={"width": 1440, "height": 900},
            user_agent=EXTERNAL_HTTP_USER_AGENT,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(TRADINGVIEW_LOGIN_URL, wait_until="domcontentloaded")
            while time.time() < deadline:
                try:
                    cookies = context.cookies("https://www.tradingview.com")
                    cookie_header = tradingview_cookie_from_browser_cookies(cookies)
                    if cookie_header:
                        saved = save_tradingview_session(cookie_header, source="playwright-auto")
                        return {
                            "captured": True,
                            "timeout_seconds": timeout,
                            "login_url": TRADINGVIEW_LOGIN_URL,
                            "session_file": str(config.TRADINGVIEW_SESSION_FILE),
                            "details": saved,
                            "status": tradingview_session_status(),
                        }
                except Exception as exc:
                    last_error = str(exc)
                time.sleep(1.0)
        finally:
            context.close()

    raise RuntimeError(
        "Timed out waiting for TradingView login session cookie capture. "
        "Complete login in the opened browser and retry."
        + (f" Last error: {last_error}" if last_error else "")
    )
