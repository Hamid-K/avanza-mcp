#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[verify] py_compile"
python3 -m py_compile avanza_cli.py tests/test_cli.py tests/test_tui_helpers.py scripts/build_buyback_daily_coverage_json.py scripts/build_catalyst_coverage_audit.py scripts/build_scheduler_coverage_audit.py scripts/build_transaction_coverage_audit.py scripts/enrich_goal_completion_transaction_gate.py scripts/verify_buyback_ladder_artifact.py scripts/verify_catalyst_coverage_artifacts.py scripts/verify_scheduler_coverage_artifacts.py scripts/verify_transaction_coverage_artifacts.py scripts/enrich_instrument_strategy_scope.py scripts/enrich_portfolio_artifact_scope.py scripts/verify_instrument_strategy_master.py scripts/verify_portfolio_control_artifacts.py scripts/verify_portfolio_scope_artifacts.py scripts/verify_goal_completion_audit.py

echo "[verify] compileall"
python3 -m compileall -q avanza_cli.py avanza_mcp tests

echo "[verify] pytest"
set +e
PYTHONWARNINGS=ignore pytest -q
pytest_status=$?
set -e

echo "[verify] buyback ladder artifacts"
python3 scripts/build_buyback_daily_coverage_json.py
python3 scripts/verify_buyback_ladder_artifact.py
python3 scripts/build_forward_kpi_coverage_audit.py
python3 scripts/verify_forward_kpi_coverage_artifacts.py
python3 scripts/build_transaction_coverage_audit.py
python3 scripts/verify_transaction_coverage_artifacts.py
python3 scripts/build_scheduler_coverage_audit.py
python3 scripts/verify_scheduler_coverage_artifacts.py
python3 scripts/build_catalyst_coverage_audit.py
python3 scripts/verify_catalyst_coverage_artifacts.py
python3 scripts/enrich_instrument_strategy_scope.py
python3 scripts/enrich_portfolio_artifact_scope.py
python3 scripts/enrich_goal_completion_transaction_gate.py
python3 scripts/verify_portfolio_scope_artifacts.py
python3 scripts/verify_instrument_strategy_master.py
python3 scripts/verify_portfolio_control_artifacts.py
python3 scripts/verify_goal_completion_audit.py

if (( pytest_status != 0 )); then
  echo "[verify] pytest failed with status ${pytest_status}" >&2
  exit "$pytest_status"
fi

echo "[verify] OK"
