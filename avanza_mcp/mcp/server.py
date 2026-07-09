"""MCP HTTP bridge server, session payload, and keychain/session-file storage."""

import copy
import hashlib
import json
import os
import subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from avanza_mcp import config
from avanza_mcp.external import tradingview_session as tv_session
from avanza_mcp.config import MCP_KEYCHAIN_SERVICE
from avanza_mcp.mcp.catalog import (
    MCP_TOOLS,
    PAPER_SESSION_ID_TOOLS,
    TENANT_SESSION_CONTROL_TOOLS,
    TENANT_SESSION_SCOPED_TOOLS,
)

if TYPE_CHECKING:
    from avanza_mcp.tui.app import AvanzaTradingTui

def mcp_tools_catalog() -> list[dict[str, Any]]:
    """Return MCP tool schemas with normalized multi-tenant scope fields."""
    tools: list[dict[str, Any]] = copy.deepcopy(MCP_TOOLS)
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name", "")).strip()
        if not name.startswith("avanza_"):
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            continue
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
            schema["properties"] = properties

        if name in TENANT_SESSION_SCOPED_TOOLS and name not in TENANT_SESSION_CONTROL_TOOLS:
            properties.setdefault(
                "tenant_session_id",
                {
                    "type": "string",
                    "description": "Optional tenant session scope id for multi-session TUI/MCP routing.",
                },
            )

        if (
            name in TENANT_SESSION_SCOPED_TOOLS
            and name not in PAPER_SESSION_ID_TOOLS
            and name != "avanza_select_session"
        ):
            properties.setdefault(
                "session_id",
                {
                    "type": "string",
                    "description": "Legacy alias for tenant_session_id (non-paper tools only).",
                },
            )
    return tools


class AvanzaMcpHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], app: "AvanzaTradingTui", token: str) -> None:
        super().__init__(server_address, handler_class)
        self.app = app
        self.token = token


class AvanzaMcpRequestHandler(BaseHTTPRequestHandler):
    server: AvanzaMcpHttpServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        expected = self.server.token
        auth = self.headers.get("Authorization", "")
        header_token = self.headers.get("X-Avanza-MCP-Token", "")
        return auth == f"Bearer {expected}" or header_token == expected

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected JSON object.")
        return data

    def do_GET(self) -> None:
        if self.path != "/status":
            self.send_json(404, {"error": "not found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            payload = self.server.app.call_from_thread(self.server.app.mcp_status_payload)
            self.send_json(200, payload)
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        if self.path != "/call":
            self.send_json(404, {"error": "not found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            request = self.read_json_body()
            tool = str(request.get("tool", ""))
            arguments = request.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object.")
            payload = self.server.app.call_from_thread(self.server.app.handle_mcp_tool_call, tool, arguments)
            self.send_json(200, payload)
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})


def mcp_session_payload(host: str, port: int, token: str, read_write: bool) -> dict[str, Any]:
    return {
        "url": f"http://{host}:{port}",
        "token": token,
        "read_write": read_write,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "proxy_command": f"python {config.SHIM_SCRIPT_NAME} mcp",
    }


def mcp_session_backend() -> str:
    value = str(os.getenv("AVANZA_MCP_SESSION_BACKEND", "auto") or "auto").strip().lower()
    if value in {"keychain", "file", "auto"}:
        return value
    return "auto"


def mcp_keychain_account(path: Path) -> str:
    scope = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"mcp_session::{scope}"


def mcp_keychain_get_token(path: Path) -> str:
    if not tv_session.tradingview_keychain_supported():
        return ""
    account = mcp_keychain_account(path)
    result = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", MCP_KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


def mcp_keychain_set_token(path: Path, token: str) -> tuple[bool, str]:
    if not tv_session.tradingview_keychain_supported():
        return False, "keychain not supported"
    account = mcp_keychain_account(path)
    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-a",
            account,
            "-s",
            MCP_KEYCHAIN_SERVICE,
            "-w",
            token,
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


def mcp_keychain_delete_token(path: Path) -> tuple[bool, str]:
    if not tv_session.tradingview_keychain_supported():
        return False, "keychain not supported"
    account = mcp_keychain_account(path)
    result = subprocess.run(
        ["security", "delete-generic-password", "-a", account, "-s", MCP_KEYCHAIN_SERVICE],
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


def write_mcp_session_file(path: Path, payload: dict[str, Any]) -> None:
    write_payload = dict(payload)
    token = str(write_payload.get("token", "") or "").strip()
    backend = mcp_session_backend()
    storage = "file"
    keychain_error = ""
    if token and backend in {"auto", "keychain"}:
        saved, keychain_error = mcp_keychain_set_token(path, token)
        if saved:
            storage = "keychain"
            write_payload.pop("token", None)
        elif backend == "keychain":
            raise RuntimeError(f"Could not save MCP session token in keychain: {keychain_error}")
    write_payload["storage"] = storage
    write_payload["backend"] = backend
    if keychain_error:
        write_payload["keychain_error"] = keychain_error
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(write_payload, indent=2), encoding="utf-8")
    os.replace(temp_path, path)
    try:
        path.chmod(0o600)
    except Exception:
        pass


def remove_mcp_session_file(path: Path | None = None) -> None:
    path = path or config.MCP_SESSION_FILE
    mcp_keychain_delete_token(path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
