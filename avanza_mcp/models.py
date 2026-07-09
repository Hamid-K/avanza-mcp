"""Shared dataclasses for account and tenant-session state."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from avanza import Avanza

@dataclass
class AccountDataSnapshot:
    account_id: str
    portfolio_data: dict[str, Any] | None = None
    stoploss_items: list[dict[str, Any]] = field(default_factory=list)
    open_order_items: list[dict[str, Any]] = field(default_factory=list)
    open_orders_payload: Any = None
    refreshed_at: datetime | None = None
    portfolio_refreshed_at: datetime | None = None
    orders_refreshed_at: datetime | None = None
    stoploss_refreshed_at: datetime | None = None
    open_orders_refreshed_at: datetime | None = None
    auth_valid: bool = True
    auth_error: str = ""


@dataclass
class AvanzaTenantSession:
    session_id: str
    label: str
    color: str
    avanza: Avanza
    accounts: list[dict[str, Any]] = field(default_factory=list)
    selected_account_id: str | None = None
    latest_portfolio_data: dict[str, Any] | None = None
    latest_stoploss_items: list[dict[str, Any]] = field(default_factory=list)
    latest_open_order_items: list[dict[str, Any]] = field(default_factory=list)
    account_snapshots: dict[str, AccountDataSnapshot] = field(default_factory=dict)
    holding_volumes_by_order_book: dict[str, str] = field(default_factory=dict)
    holding_labels_by_order_book: dict[str, str] = field(default_factory=dict)
    order_search_labels_by_order_book: dict[str, str] = field(default_factory=dict)
    auth_valid: bool = True
    auth_error: str = ""
