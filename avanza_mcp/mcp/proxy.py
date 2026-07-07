"""MCP stdio proxy bridging MCP clients to the TUI's HTTP bridge."""

import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from avanza_mcp import config
from avanza_mcp.config import APP_VERSION, MCP_PROTOCOL_VERSION
from avanza_mcp.mcp import server as mcp_server

def load_mcp_session(path: Path | None = None) -> dict[str, Any]:
    path = path or config.MCP_SESSION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"MCP session file not found: {path}. Enable MCP mode in the TUI first.") from exc
    if not isinstance(data, dict) or not data.get("url"):
        raise RuntimeError(f"Invalid MCP session file: {path}")
    token = str(data.get("token", "") or "").strip()
    if not token:
        storage = str(data.get("storage", "") or "").strip().lower()
        if storage == "keychain":
            token = mcp_server.mcp_keychain_get_token(path)
    if not token:
        raise RuntimeError(f"Invalid MCP session file: {path}")
    data = dict(data)
    data["token"] = token
    return data


def call_mcp_bridge(session: dict[str, Any], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    url = str(session["url"]).rstrip("/") + "/call"
    body = json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {session['token']}",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            payload = {"error": body or f"HTTP {exc.code}"}
        payload.setdefault("ok", False)
        payload.setdefault("error", f"HTTP {exc.code}")
    except URLError as exc:
        raise RuntimeError(f"Could not reach TUI MCP bridge at {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("MCP bridge returned a non-object response.")
    return payload


def mcp_tool_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            }
        ],
        "isError": not bool(payload.get("ok", True)),
    }


def read_mcp_message(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("utf-8").partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(stream.read(length).decode("utf-8"))


def write_mcp_message(stream: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stream.flush()


def mcp_success(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def mcp_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def run_mcp_stdio_proxy(session_file: Path | None = None) -> None:
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer

    while True:
        message = read_mcp_message(input_stream)
        if message is None:
            return
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        if message_id is None and str(method).startswith("notifications/"):
            continue

        try:
            if method == "initialize":
                write_mcp_message(
                    output_stream,
                    mcp_success(
                        message_id,
                        {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "avanza_cli", "version": APP_VERSION},
                        },
                    ),
                )
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                write_mcp_message(output_stream, mcp_success(message_id, {}))
            elif method == "tools/list":
                write_mcp_message(output_stream, mcp_success(message_id, {"tools": mcp_server.mcp_tools_catalog()}))
            elif method == "tools/call":
                tool_name = str(params.get("name", ""))
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object.")
                session = load_mcp_session(session_file)
                payload = call_mcp_bridge(session, tool_name, arguments)
                write_mcp_message(output_stream, mcp_success(message_id, mcp_tool_response(payload)))
            else:
                write_mcp_message(output_stream, mcp_error(message_id, -32601, f"Unknown method: {method}"))
        except Exception as exc:
            write_mcp_message(output_stream, mcp_error(message_id, -32000, str(exc)))
