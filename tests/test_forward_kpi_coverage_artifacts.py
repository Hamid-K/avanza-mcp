import json

from scripts.build_forward_kpi_coverage_audit import build_audit
from scripts.verify_forward_kpi_coverage_artifacts import validate


def test_forward_kpi_audit_preserves_incomplete_outcome_state():
    payload = build_audit(generated_at="2026-08-06T13:10:00+02:00")

    assert payload["status"] == "INCOMPLETE_OUTCOME_EVIDENCE"
    assert payload["freshness"]["status"] == "STAMPED_ANALYSIS_SNAPSHOT"
    assert payload["freshness"]["live_state_current"] is False
    assert payload["validation"]["scorecard_measure_count"] == 12
    assert payload["validation"]["completed_forward_scorecard_measures"] == 0
    assert payload["validation"]["forward_outcome_proven"] is False
    assert payload["validation"]["hard_churn_brake_active"] is True
    assert payload["freshness"]["descriptive_live_observation_only"] is True
    assert payload["validation"]["descriptive_live_observation_account_count"] == 2
    assert payload["validation"]["descriptive_live_observation_scored"] is False
    assert validate(payload) == []


def test_forward_kpi_validator_rejects_false_completion():
    payload = build_audit(generated_at="2026-08-06T13:10:00+02:00")
    payload["validation"]["forward_outcome_proven"] = True

    errors = validate(payload)

    assert "forward KPI forward_outcome_proven must remain false" in errors
