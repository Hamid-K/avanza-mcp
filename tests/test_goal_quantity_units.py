"""Unlike instrument quantities must not become a portfolio economic total."""

from copy import deepcopy

import pytest

from scripts.verify_goal_completion_audit import _validate_full_history_governance_link
from tests.test_goal_completion_audit import full_history_governance_link


def errors_for(link):
    errors = []
    payload = {"full_history_governance": link, "current_live_reconciliation": {"full_history_governance": deepcopy(link)}}
    _validate_full_history_governance_link(payload, errors, require_present=True, require_clean=False)
    return errors


def test_explicit_no_cross_instrument_total_preserves_null_quantity():
    link = full_history_governance_link()
    link["canonical"].update(open_sale_quantity_exact=None, cross_instrument_antal_total_not_economic_metric=True)
    assert errors_for(link) == []
    assert link["objective_complete"] is False
    assert link["authority"]["trade_authority"] is False


@pytest.mark.parametrize("flag,quantity", [(False, None), (None, None), ("true", None), (True, "10")])
def test_missing_units_or_contradictory_total_fails_closed(flag, quantity):
    link = full_history_governance_link()
    link["canonical"].update(open_sale_quantity_exact=quantity, cross_instrument_antal_total_not_economic_metric=flag)
    assert "full-history open quantity is invalid" in errors_for(link)


def test_legacy_decimal_summary_contract_remains_valid():
    assert errors_for(full_history_governance_link()) == []
