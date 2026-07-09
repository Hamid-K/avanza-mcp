"""Single-instance UI lock: the TUI and Web UI are mutually exclusive.

Both ``avanza-cli tui`` and ``avanza-cli web`` acquire this lock at startup
and release it on exit. A stale lock (dead pid) is reclaimed automatically.
"""

import atexit
import json
import os
from typing import Any

from avanza_mcp import config


def _read_lock() -> dict[str, Any] | None:
    try:
        raw = json.loads(config.UI_LOCK_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire_ui_lock(mode: str) -> None:
    """Acquire the exclusive UI lock for ``mode`` ("tui" or "web").

    Raises RuntimeError with a user-facing message if another UI is running.
    """
    existing = _read_lock()
    if existing:
        pid = int(existing.get("pid", 0) or 0)
        held_mode = str(existing.get("mode", "unknown"))
        if pid != os.getpid() and _pid_alive(pid):
            raise RuntimeError(
                f"Another Avanza-MCP UI is already running: {held_mode} (pid {pid}). "
                "The TUI and Web UI are mutually exclusive — quit the other one first, "
                f"or remove {config.UI_LOCK_FILE.name} if that process is gone."
            )
    payload = {"pid": os.getpid(), "mode": str(mode)}
    config.UI_LOCK_FILE.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def release_ui_lock() -> None:
    existing = _read_lock()
    if existing and int(existing.get("pid", 0) or 0) != os.getpid():
        return
    try:
        config.UI_LOCK_FILE.unlink()
    except (FileNotFoundError, OSError):
        pass
