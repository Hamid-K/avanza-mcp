"""MCP stdio proxy bridging MCP clients to the TUI's HTTP bridge."""

import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from avanza_mcp import config
from avanza_mcp.config import (
    APP_VERSION,
    MCP_LEGACY_PROTOCOL_VERSIONS,
    MCP_PROTOCOL_VERSION,
    MCP_STATELESS_PROTOCOL_VERSION,
    MCP_SUPPORTED_PROTOCOL_VERSIONS,
)
from avanza_mcp.mcp import server as mcp_server


MCP_PROTOCOL_META_KEY = "io.modelcontextprotocol/protocolVersion"
MCP_SERVER_INSTRUCTIONS = (
    "Avanza-MCP exposes the same account, market-data, paper-trading, and guarded "
    "broker tools to every MCP client. Begin with avanza_status or "
    "avanza_capabilities. Use explicit tenant_session_id and account_id when "
    "account identity matters. Read-only and paper modes are the defaults; live "
    "broker mutation requires the server-side read/write gate, explicit live "
    "authorization for the active session, confirm=true, and post-mutation "
    "readback. Client trust or auto-approval never grants trading authority."
)


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


def mcp_tool_response(
    payload: dict[str, Any],
    *,
    protocol_version: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            }
        ],
        "isError": not bool(payload.get("ok", True)),
    }
    if protocol_version in MCP_SUPPORTED_PROTOCOL_VERSIONS[:-1]:
        result["structuredContent"] = payload
    if protocol_version == MCP_STATELESS_PROTOCOL_VERSION:
        result["resultType"] = "complete"
    return result


MCP_STDIO_NEWLINE = "newline"
MCP_STDIO_CONTENT_LENGTH = "content-length"


def read_mcp_message_frame(
    stream: Any,
) -> tuple[dict[str, Any] | None, str]:
    """Read standard newline-delimited MCP, with legacy framing fallback."""

    first_line = stream.readline()
    while first_line in (b"\r\n", b"\n"):
        first_line = stream.readline()
    if first_line == b"":
        return None, MCP_STDIO_NEWLINE

    stripped = first_line.strip()
    if stripped.startswith((b"{", b"[")):
        payload = json.loads(stripped.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected an MCP JSON-RPC object.")
        return payload, MCP_STDIO_NEWLINE

    headers: dict[str, str] = {}
    line = first_line
    while True:
        if line in (b"\r\n", b"\n"):
            break
        key, separator, value = line.decode("utf-8").partition(":")
        if not separator:
            raise ValueError("Invalid MCP stdio frame.")
        headers[key.lower()] = value.strip()
        line = stream.readline()
        if line == b"":
            return None, MCP_STDIO_CONTENT_LENGTH

    length = int(headers.get("content-length", "0"))
    if length <= 0:
        raise ValueError("Invalid MCP Content-Length frame.")
    payload = json.loads(stream.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected an MCP JSON-RPC object.")
    return payload, MCP_STDIO_CONTENT_LENGTH


def read_mcp_message(stream: Any) -> dict[str, Any] | None:
    payload, _framing = read_mcp_message_frame(stream)
    return payload


def write_mcp_message(
    stream: Any,
    payload: dict[str, Any],
    *,
    framing: str = MCP_STDIO_NEWLINE,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if framing == MCP_STDIO_CONTENT_LENGTH:
        stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    elif framing == MCP_STDIO_NEWLINE:
        stream.write(body + b"\n")
    else:
        raise ValueError(f"Unsupported MCP stdio framing: {framing}")
    stream.flush()


def mcp_success(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def mcp_error(
    message_id: Any,
    code: int,
    message: str,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def mcp_server_capabilities() -> dict[str, Any]:
    return {"tools": {}}


def negotiate_initialize_protocol(params: dict[str, Any]) -> str:
    requested = str(params.get("protocolVersion", "") or "").strip()
    if requested in MCP_LEGACY_PROTOCOL_VERSIONS:
        return requested
    return MCP_PROTOCOL_VERSION


def mcp_request_protocol_version(
    params: dict[str, Any],
    active_protocol_version: str | None,
) -> str | None:
    meta = params.get("_meta")
    if isinstance(meta, dict):
        requested = str(meta.get(MCP_PROTOCOL_META_KEY, "") or "").strip()
        if requested:
            return requested
    return active_protocol_version


def mcp_discovery_result() -> dict[str, Any]:
    return {
        "resultType": "complete",
        "supportedVersions": list(MCP_SUPPORTED_PROTOCOL_VERSIONS),
        "capabilities": mcp_server_capabilities(),
        "serverInfo": {"name": "avanza_cli", "version": APP_VERSION},
        "instructions": MCP_SERVER_INSTRUCTIONS,
    }


def mcp_tools_list_result(protocol_version: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"tools": mcp_server.mcp_tools_catalog()}
    if protocol_version == MCP_STATELESS_PROTOCOL_VERSION:
        result.update(
            {
                "resultType": "complete",
                "ttlMs": 300_000,
                "cacheScope": "public",
            }
        )
    return result


def run_mcp_stdio_proxy(session_file: Path | None = None) -> None:
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    active_protocol_version: str | None = None

    while True:
        message, framing = read_mcp_message_frame(input_stream)
        if message is None:
            return
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        if message_id is None and str(method).startswith("notifications/"):
            continue

        try:
            if method == "initialize":
                active_protocol_version = negotiate_initialize_protocol(params)
                write_mcp_message(
                    output_stream,
                    mcp_success(
                        message_id,
                        {
                            "protocolVersion": active_protocol_version,
                            "capabilities": mcp_server_capabilities(),
                            "serverInfo": {"name": "avanza_cli", "version": APP_VERSION},
                            "instructions": MCP_SERVER_INSTRUCTIONS,
                        },
                    ),
                    framing=framing,
                )
            elif method == "server/discover":
                write_mcp_message(
                    output_stream,
                    mcp_success(message_id, mcp_discovery_result()),
                    framing=framing,
                )
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                write_mcp_message(
                    output_stream,
                    mcp_success(message_id, {}),
                    framing=framing,
                )
            elif method == "tools/list":
                protocol_version = mcp_request_protocol_version(params, active_protocol_version)
                if protocol_version not in {None, *MCP_SUPPORTED_PROTOCOL_VERSIONS}:
                    write_mcp_message(
                        output_stream,
                        mcp_error(
                            message_id,
                            -32022,
                            f"Unsupported MCP protocol version: {protocol_version}",
                            data={"supportedVersions": list(MCP_SUPPORTED_PROTOCOL_VERSIONS)},
                        ),
                        framing=framing,
                    )
                    continue
                write_mcp_message(
                    output_stream,
                    mcp_success(
                        message_id,
                        mcp_tools_list_result(protocol_version),
                    ),
                    framing=framing,
                )
            elif method == "tools/call":
                protocol_version = mcp_request_protocol_version(params, active_protocol_version)
                if protocol_version not in {None, *MCP_SUPPORTED_PROTOCOL_VERSIONS}:
                    write_mcp_message(
                        output_stream,
                        mcp_error(
                            message_id,
                            -32022,
                            f"Unsupported MCP protocol version: {protocol_version}",
                            data={"supportedVersions": list(MCP_SUPPORTED_PROTOCOL_VERSIONS)},
                        ),
                        framing=framing,
                    )
                    continue
                tool_name = str(params.get("name", ""))
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object.")
                session = load_mcp_session(session_file)
                payload = call_mcp_bridge(session, tool_name, arguments)
                write_mcp_message(
                    output_stream,
                    mcp_success(
                        message_id,
                        mcp_tool_response(payload, protocol_version=protocol_version),
                    ),
                    framing=framing,
                )
            else:
                write_mcp_message(
                    output_stream,
                    mcp_error(message_id, -32601, f"Unknown method: {method}"),
                    framing=framing,
                )
        except Exception as exc:
            write_mcp_message(
                output_stream,
                mcp_error(message_id, -32000, str(exc)),
                framing=framing,
            )
