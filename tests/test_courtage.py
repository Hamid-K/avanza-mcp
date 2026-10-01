"""Brokerage-class MCP behavior without touching a live Avanza account."""

from types import SimpleNamespace

import pytest
from avanza.constants import HttpMethod

from avanza_mcp import courtage
from avanza_mcp.mcp.catalog import TENANT_SESSION_SCOPED_TOOLS
from avanza_mcp.models import AvanzaTenantSession
from avanza_mcp.tui.app import AvanzaTradingTui


class FakeAvanza:
    def __init__(self, *, current="MINI", group="BRONS", derivative=False, accounts=None):
        self.current = current
        self.group = group
        self.derivative = derivative
        self.accounts = accounts or [{"id": "acc-1", "name": {"defaultName": "ISK"}, "type": "ISK"}]
        self.calls = []
        self.post_error = None
        self.readback_error = False

    def get_overview(self):
        return {"accounts": self.accounts}

    def _Avanza__call(self, method, path, options=None, return_content=False):
        self.calls.append((method, path, options))
        if method is HttpMethod.GET and path == courtage.COURTAGE_CLASS_PATH:
            if self.readback_error and any(call[0] is HttpMethod.POST for call in self.calls):
                raise TimeoutError("readback unavailable")
            return {
                "currentCourtageClass": self.current,
                "eligibleForStart": self.current == "START",
                "specialAgreement": False,
            }
        if method is HttpMethod.GET and path == courtage.SESSION_INFO_PATH:
            return {"user": {"loggedIn": True, "customerGroup": self.group}}
        if method is HttpMethod.GET and path == courtage.DERIVATIVE_ORDER_PATH:
            return {"hasDerivatives": self.derivative}
        if method is HttpMethod.POST and path == courtage.COURTAGE_CLASS_UPDATE_PATH:
            if self.post_error == "before":
                raise TimeoutError("submission unavailable")
            self.current = options["newClass"]
            if self.post_error == "after":
                raise TimeoutError("response unavailable")
            return None
        raise AssertionError((method, path, options))

    @property
    def post_calls(self):
        return [call for call in self.calls if call[0] is HttpMethod.POST]


def make_app(fake=None):
    app = AvanzaTradingTui()
    app.avanza = fake or FakeAvanza()
    app.active_session_id = "tenant-1"
    app.selected_account_id = "acc-1"
    return app


def test_courtage_tool_schemas_are_tenant_scoped_and_set_requires_explicit_target():
    from avanza_mcp.mcp.catalog import MCP_TOOLS

    catalog = {tool["name"]: tool for tool in MCP_TOOLS}
    assert {"avanza_courtage_class_get", "avanza_courtage_class_set"} <= TENANT_SESSION_SCOPED_TOOLS
    assert catalog["avanza_courtage_class_get"]["inputSchema"]["required"] == ["account_id"]
    assert catalog["avanza_courtage_class_set"]["inputSchema"]["required"] == ["account_id", "target_class"]


def test_read_courtage_class_for_explicit_account_is_read_only():
    app = make_app()
    result = app.execute_mcp_tool("avanza_courtage_class_get", {"account_id": "acc-1"})
    assert result["account_id"] == "acc-1"
    assert result["tenant_session_id"] == "tenant-1"
    assert result["current_class"] == "MINI"
    assert [item["code"] for item in result["available_classes"]] == list(courtage.NORMAL_CLASSES)
    assert result["derivative_order_exists"] is False
    assert app.avanza.post_calls == []


def test_dry_run_never_changes_courtage_class():
    app = make_app()
    preview = app.execute_mcp_tool(
        "avanza_courtage_class_set", {"account_id": "acc-1", "target_class": "FASTPRIS"}
    )
    assert preview["dry_run"] is True
    assert preview["would_change"] is True
    assert app.avanza.current == "MINI"
    assert app.avanza.post_calls == []


def test_confirmed_change_requires_live_gates_and_reads_back():
    app = make_app()
    args = {"account_id": "acc-1", "target_class": "FASTPRIS", "confirm": True}
    with pytest.raises(PermissionError, match="read-only"):
        app.execute_mcp_tool("avanza_courtage_class_set", args)
    app.mcp_write_enabled = True
    with pytest.raises(PermissionError, match="Live trading is blocked"):
        app.execute_mcp_tool("avanza_courtage_class_set", args)
    app.live_trading_allowed_for_session = True
    with pytest.raises(PermissionError, match="paper mode"):
        app.execute_mcp_tool("avanza_courtage_class_set", args)
    assert app.avanza.post_calls == []

    app.paper_mode_enabled = False
    result = app.execute_mcp_tool("avanza_courtage_class_set", args)
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["current_class_after"] == "FASTPRIS"
    assert app.avanza.post_calls == [(HttpMethod.POST, courtage.COURTAGE_CLASS_UPDATE_PATH, {"newClass": "FASTPRIS"})]


def test_unknown_account_or_wrong_tenant_fails_before_post():
    app = make_app()
    with pytest.raises(ValueError, match="not visible"):
        app.execute_mcp_tool("avanza_courtage_class_set", {
            "account_id": "acc-other", "target_class": "SMALL", "confirm": True,
        })
    assert app.avanza.post_calls == []


def test_duplicate_account_across_tenants_requires_explicit_session():
    app = make_app()
    app.tenant_sessions = {
        "tenant-1": SimpleNamespace(session_id="tenant-1", accounts=[{"id": "acc-1"}]),
        "tenant-2": SimpleNamespace(session_id="tenant-2", accounts=[{"id": "acc-1"}]),
    }
    with pytest.raises(ValueError, match="multiple sessions"):
        app.resolve_session_id_for_mcp("avanza_courtage_class_set", {"account_id": "acc-1"})
    assert app.resolve_session_id_for_mcp(
        "avanza_courtage_class_set", {"account_id": "acc-1", "tenant_session_id": "tenant-2"}
    ) == "tenant-2"


def test_courtage_mutation_routes_to_owner_tenant_and_restores_active_session():
    first = FakeAvanza(accounts=[{"id": "acc-1"}])
    second = FakeAvanza(accounts=[{"id": "acc-2"}], current="SMALL")
    app = make_app(first)
    app.accounts = first.accounts
    app.tenant_sessions = {
        "tenant-1": AvanzaTenantSession("tenant-1", "First", "blue", first, first.accounts, "acc-1"),
        "tenant-2": AvanzaTenantSession("tenant-2", "Second", "green", second, second.accounts, "acc-2"),
    }
    app.mcp_write_enabled = True
    app.paper_mode_enabled = False
    app.live_trading_allowed_for_session = True
    args = {"account_id": "acc-2", "target_class": "MEDIUM", "confirm": True}
    with pytest.raises(PermissionError, match="Live trading is blocked"):
        app.execute_mcp_tool("avanza_courtage_class_set", args)
    assert app.active_session_id == "tenant-1"
    assert first.post_calls == second.post_calls == []

    app.active_session_id = "tenant-2"
    app.live_trading_allowed_for_session = True
    app.active_session_id = "tenant-1"
    result = app.execute_mcp_tool("avanza_courtage_class_set", args)
    assert result["tenant_session_id"] == "tenant-2"
    assert result["verified"] is True
    assert second.current == "MEDIUM"
    assert first.current == "MINI"
    assert first.post_calls == []
    assert app.active_session_id == "tenant-1"
    assert app.avanza is first


@pytest.mark.parametrize("group,target", [("BRONS", "PRIVATE_BANKING"), ("PRIVATE_BANKING", "FASTPRIS"), ("PRO1", "MINI"), ("UNKNOWN", "SMALL")])
def test_unavailable_class_never_posts(group, target):
    app = make_app(FakeAvanza(group=group))
    with pytest.raises(ValueError, match="available courtage class|could not be verified"):
        app.execute_mcp_tool("avanza_courtage_class_set", {
            "account_id": "acc-1", "target_class": target, "confirm": True,
        })
    assert app.avanza.post_calls == []


def test_derivative_order_blocks_confirmed_class_change():
    app = make_app(FakeAvanza(derivative=True))
    app.mcp_write_enabled = True
    app.live_trading_allowed_for_session = True
    app.paper_mode_enabled = False
    with pytest.raises(PermissionError, match="derivative"):
        app.execute_mcp_tool("avanza_courtage_class_set", {
            "account_id": "acc-1", "target_class": "SMALL", "confirm": True,
        })
    assert app.avanza.post_calls == []


def test_leaving_start_requires_separate_acknowledgement():
    app = make_app(FakeAvanza(current="START"))
    app.mcp_write_enabled = True
    app.live_trading_allowed_for_session = True
    app.paper_mode_enabled = False
    args = {"account_id": "acc-1", "target_class": "MINI", "confirm": True}
    with pytest.raises(PermissionError, match="irreversible"):
        app.execute_mcp_tool("avanza_courtage_class_set", args)
    assert app.avanza.post_calls == []
    result = app.execute_mcp_tool("avanza_courtage_class_set", {**args, "acknowledge_start_exit": True})
    assert result["verified"] is True


def test_joint_account_or_mismatched_account_class_is_not_switched():
    for account in (
        {"id": "acc-1", "jointlyOwned": True},
        {"id": "acc-1", "courtageClass": "MEDIUM"},
    ):
        app = make_app(FakeAvanza(accounts=[account]))
        app.mcp_write_enabled = True
        app.live_trading_allowed_for_session = True
        app.paper_mode_enabled = False
        with pytest.raises(PermissionError, match="joint or differently priced"):
            app.execute_mcp_tool("avanza_courtage_class_set", {
                "account_id": "acc-1", "target_class": "SMALL", "confirm": True,
            })
        assert app.avanza.post_calls == []


def test_account_level_readback_must_match_when_avanza_exposes_it():
    fake = FakeAvanza(accounts=[{"id": "acc-1", "courtageClass": "MINI"}])
    app = make_app(fake)
    app.mcp_write_enabled = True
    app.live_trading_allowed_for_session = True
    app.paper_mode_enabled = False
    result = app.execute_mcp_tool("avanza_courtage_class_set", {
        "account_id": "acc-1", "target_class": "SMALL", "confirm": True,
    })
    assert len(fake.post_calls) == 1
    assert result["current_class_after"] == "SMALL"
    assert result["account_class_after"] == "MINI"
    assert result["ok"] is False
    assert result["verified"] is False


@pytest.mark.parametrize("post_error,readback_error,verified,outcome_unknown", [
    ("after", False, True, None),
    ("before", False, False, None),
    (None, True, None, True),
])
def test_post_response_or_readback_failure_is_not_retried(post_error, readback_error, verified, outcome_unknown):
    fake = FakeAvanza()
    fake.post_error = post_error
    fake.readback_error = readback_error
    app = make_app(fake)
    app.mcp_write_enabled = True
    app.live_trading_allowed_for_session = True
    app.paper_mode_enabled = False
    result = app.execute_mcp_tool("avanza_courtage_class_set", {
        "account_id": "acc-1", "target_class": "SMALL", "confirm": True,
    })
    assert len(fake.post_calls) == 1
    if verified is not None:
        assert result["verified"] is verified
    if outcome_unknown is not None:
        assert result["outcome_unknown"] is outcome_unknown
