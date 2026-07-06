"""Credential prompting, 1Password integration, and Avanza connection."""

import argparse
import getpass
import json
import os
import subprocess
from typing import Any

from avanza import Avanza

def prompt_credentials(username: str | None) -> dict[str, str]:
    if not username:
        username = input("Avanza username: ").strip()

    password = getpass.getpass("Avanza password: ")
    totp_code = getpass.getpass("Avanza TOTP code: ").strip()

    if not username:
        raise ValueError("Username is required.")
    if not password:
        raise ValueError("Password is required.")
    if not totp_code:
        raise ValueError("TOTP code is required.")

    return {
        "username": username,
        "password": password,
        "totpToken": totp_code,
    }


def onepassword_command(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["op", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("AVANZA_OP_TIMEOUT_SECONDS", "120")),
        )
    except FileNotFoundError as exc:
        raise RuntimeError("1Password CLI 'op' is not installed or is not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("1Password CLI timed out waiting for authorization.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"1Password CLI failed: {message or exc}") from exc
    return result.stdout.strip()


def onepassword_item_json(item: str, vault: str | None = None) -> dict[str, Any]:
    if not item.strip():
        raise ValueError("1Password item name or ID is required.")
    args = ["item", "get", item, "--format", "json"]
    if vault:
        args.extend(["--vault", vault])
    raw = onepassword_command(args)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("1Password CLI returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("1Password CLI returned an unexpected item shape.")
    return data


def onepassword_field_value(item: dict[str, Any], labels: set[str], purposes: set[str]) -> str:
    for field in item.get("fields", []):
        if not isinstance(field, dict):
            continue
        label = str(field.get("label") or field.get("id") or "").strip().lower()
        purpose = str(field.get("purpose") or "").strip().lower()
        value = field.get("value")
        if value is None:
            continue
        if label in labels or purpose in purposes:
            return str(value)
    return ""


def onepassword_credentials(item: str, vault: str | None = None) -> dict[str, str]:
    item_data = onepassword_item_json(item, vault)
    username = onepassword_field_value(
        item_data,
        {"username", "user name", "email", "e-mail"},
        {"username"},
    )
    password = onepassword_field_value(
        item_data,
        {"password"},
        {"password"},
    )

    otp_args = ["item", "get", item, "--otp"]
    if vault:
        otp_args.extend(["--vault", vault])
    totp_code = onepassword_command(otp_args).strip()

    if not username:
        raise ValueError("Could not find a username field in the 1Password item.")
    if not password:
        raise ValueError("Could not find a password field in the 1Password item.")
    if not totp_code:
        raise ValueError("Could not get a TOTP code from the 1Password item.")

    return {
        "username": username,
        "password": password,
        "totpToken": totp_code,
    }


def connect(args: argparse.Namespace) -> Avanza:
    if getattr(args, "username", None) and getattr(args, "onepassword_item", None):
        raise ValueError("Use either --username or --onepassword-item, not both.")
    onepassword_item = getattr(args, "onepassword_item", None)
    onepassword_vault = getattr(args, "onepassword_vault", None)
    if onepassword_item:
        return Avanza(onepassword_credentials(onepassword_item, onepassword_vault))
    return Avanza(prompt_credentials(args.username))
