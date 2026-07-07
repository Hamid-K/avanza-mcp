from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_ui_lock(monkeypatch, tmp_path):
    """Tests must never observe or mutate the workstation's real UI lock or
    web session file (issue #2: suite failed when a local web UI was running)."""
    monkeypatch.setattr("avanza_mcp.config.UI_LOCK_FILE", tmp_path / "ui.lock")
    monkeypatch.setattr("avanza_mcp.config.WEB_SESSION_FILE", tmp_path / "web-session.json")
