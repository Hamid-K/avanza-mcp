import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from avanza_mcp.config import (
    MCP_LEGACY_PROTOCOL_VERSIONS,
    MCP_PROTOCOL_VERSION,
    MCP_STATELESS_PROTOCOL_VERSION,
    MCP_SUPPORTED_PROTOCOL_VERSIONS,
)
from avanza_mcp.mcp.proxy import (
    MCP_PROTOCOL_META_KEY,
    MCP_STDIO_CONTENT_LENGTH,
    MCP_STDIO_NEWLINE,
    MCP_SERVER_INSTRUCTIONS,
    read_mcp_message_frame,
    write_mcp_message,
)


ROOT = Path(__file__).resolve().parents[1]


def run_proxy(messages: list[dict], tmp_path: Path, framing: str) -> list[dict]:
    request_stream = io.BytesIO()
    for message in messages:
        write_mcp_message(request_stream, message, framing=framing)

    process = subprocess.Popen(
        [
            sys.executable,
            "avanza_cli.py",
            "mcp",
            "--session-file",
            str(tmp_path / "missing-session.json"),
        ],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output, error = process.communicate(request_stream.getvalue(), timeout=10)
    assert process.returncode == 0, error.decode()

    responses: list[dict] = []
    response_stream = io.BytesIO(output)
    while True:
        payload, response_framing = read_mcp_message_frame(response_stream)
        if payload is None:
            break
        assert response_framing == framing
        responses.append(payload)
    return responses


@pytest.mark.parametrize("framing", [MCP_STDIO_NEWLINE, MCP_STDIO_CONTENT_LENGTH])
@pytest.mark.parametrize("protocol_version", MCP_LEGACY_PROTOCOL_VERSIONS)
def test_stdio_proxy_negotiates_handshake_protocol_versions(
    tmp_path: Path,
    framing: str,
    protocol_version: str,
) -> None:
    responses = run_proxy(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "compatibility-test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
        tmp_path,
        framing,
    )

    assert responses[0]["result"]["protocolVersion"] == protocol_version
    assert responses[0]["result"]["instructions"] == MCP_SERVER_INSTRUCTIONS
    assert any(tool["name"] == "avanza_status" for tool in responses[1]["result"]["tools"])


def test_stdio_proxy_falls_back_to_latest_handshake_protocol(tmp_path: Path) -> None:
    responses = run_proxy(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2099-01-01",
                    "capabilities": {},
                    "clientInfo": {"name": "future-client", "version": "1"},
                },
            }
        ],
        tmp_path,
        MCP_STDIO_NEWLINE,
    )

    assert responses[0]["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION


def test_stdio_proxy_supports_stateless_discovery_and_tools_list(tmp_path: Path) -> None:
    meta = {
        MCP_PROTOCOL_META_KEY: MCP_STATELESS_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "stateless-test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    responses = run_proxy(
        [
            {
                "jsonrpc": "2.0",
                "id": "discover",
                "method": "server/discover",
                "params": {"_meta": meta},
            },
            {
                "jsonrpc": "2.0",
                "id": "tools",
                "method": "tools/list",
                "params": {"_meta": meta},
            },
        ],
        tmp_path,
        MCP_STDIO_NEWLINE,
    )

    discovery = responses[0]["result"]
    assert discovery["resultType"] == "complete"
    assert discovery["supportedVersions"] == list(MCP_SUPPORTED_PROTOCOL_VERSIONS)
    assert discovery["instructions"] == MCP_SERVER_INSTRUCTIONS

    tools = responses[1]["result"]
    assert tools["resultType"] == "complete"
    assert tools["ttlMs"] > 0
    assert any(tool["name"] == "avanza_status" for tool in tools["tools"])


def test_stdio_proxy_rejects_unknown_per_request_protocol(tmp_path: Path) -> None:
    responses = run_proxy(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": {MCP_PROTOCOL_META_KEY: "2099-01-01"}},
            }
        ],
        tmp_path,
        MCP_STDIO_NEWLINE,
    )

    assert responses[0]["error"]["code"] == -32022
    assert responses[0]["error"]["data"]["supportedVersions"] == list(
        MCP_SUPPORTED_PROTOCOL_VERSIONS
    )


def test_provider_entrypoints_share_one_canonical_file() -> None:
    canonical = ROOT / "INSTRUCTIONS" / "PROVIDER_ENTRYPOINT.md"
    assert canonical.is_file()

    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        entrypoint = ROOT / name
        assert entrypoint.is_symlink()
        assert os.readlink(entrypoint) == "INSTRUCTIONS/PROVIDER_ENTRYPOINT.md"
        assert entrypoint.resolve() == canonical.resolve()

    content = canonical.read_text(encoding="utf-8")
    assert "INSTRUCTIONS/SESSION_HANDOFF.md" in content
    assert "INSTRUCTIONS/INSTRUCTIONS.md" in content
    assert "avanza_status" in content
    assert "Client trust or auto-approval does not grant trading" in content
