import io
from types import SimpleNamespace

import pytest

from avanza_mcp.mcp.server import AvanzaMcpRequestHandler


class DisconnectingWriter:
    def __init__(self, fail_on_write: int) -> None:
        self.fail_on_write = fail_on_write
        self.write_count = 0
        self.buffer = io.BytesIO()

    def write(self, data: bytes) -> int:
        self.write_count += 1
        if self.write_count == self.fail_on_write:
            raise BrokenPipeError(32, "Broken pipe")
        return self.buffer.write(data)


class RecordingApp:
    def __init__(self) -> None:
        self.events = []

    def record_event(self, category, event, details) -> None:
        self.events.append((category, event, details))


def make_handler(writer):
    handler = object.__new__(AvanzaMcpRequestHandler)
    app = RecordingApp()
    handler.server = SimpleNamespace(app=app)
    handler.wfile = writer
    handler.request_version = "HTTP/1.1"
    handler.command = "POST"
    handler.requestline = "POST /call HTTP/1.1"
    handler.path = "/call"
    handler.close_connection = False
    return handler, app


@pytest.mark.parametrize("fail_on_write", [1, 2])
def test_send_json_quietly_handles_client_disconnect(fail_on_write):
    handler, app = make_handler(DisconnectingWriter(fail_on_write))

    sent = handler.send_json(200, {"ok": True})

    assert sent is False
    assert handler.close_connection is True
    assert app.events == [
        (
            "mcp",
            "client_disconnected",
            {"method": "POST", "path": "/call", "error": "BrokenPipeError"},
        )
    ]


def test_send_json_preserves_normal_response():
    writer = io.BytesIO()
    handler, app = make_handler(writer)

    sent = handler.send_json(200, {"ok": True})

    assert sent is True
    assert handler.close_connection is False
    assert app.events == []
    assert writer.getvalue().endswith(b'{"ok": true}')
