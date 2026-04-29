#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[verify] py_compile"
python3 -m py_compile avanza_cli.py tests/test_cli.py tests/test_tui_helpers.py

echo "[verify] compileall"
python3 -m compileall -q avanza_cli.py tests

echo "[verify] pytest"
PYTHONWARNINGS=ignore pytest -q

echo "[verify] OK"
