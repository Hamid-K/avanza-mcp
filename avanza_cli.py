#!/usr/bin/env python3
import argparse
import getpass
import sys
from datetime import date
from typing import Any

from avanza import Avanza
from avanza.constants import OrderType, StopLossPriceType, StopLossTriggerType
from avanza.entities import StopLossOrderEvent, StopLossTrigger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static


console = Console()


def prompt_credentials(username: str | None) -> dict[str, str]:
    if not username:
        username = input("Avanza username: ").strip()

    password = getpass.getpass("Avanza password: ")
    totp_code = getpass.getpass("Avanza TOTP code: ").strip()

    if not username:
        raise ValueError("Username is required.")
    if not password:
        raise ValueError("Password is required.")
    if not totp_code:
        raise ValueError("TOTP code is required.")

    return {
        "username": username,
        "password": password,
        "totpToken": totp_code,
    }


def connect(args: argparse.Namespace) -> Avanza:
    return Avanza(prompt_credentials(args.username))


def render_table(title: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    table = Table(title=title, show_lines=False)
    for column in columns:
        table.add_column(column, overflow="fold")

    for row in rows:
        table.add_row(*(str(value) for value in row))

    console.print(table)


def render_message(title: str, lines: list[str]) -> None:
    console.print(Panel("\n".join(lines), title=title, expand=False))


def format_stop_loss_request(preview: dict[str, Any]) -> list[str]:
    trigger = preview["stop_loss_trigger"]
    order_event = preview["stop_loss_order_event"]
    return [
        f"Account: {preview['account_id']}",
        f"Order book: {preview['order_book_id']}",
        f"Trigger: {trigger['type']} {trigger['value']} {trigger['value_type']}",
        f"Trigger valid until: {trigger['valid_until']}",
        f"Order: {order_event['type']} {order_event['volume']} @ {order_event['price']} {order_event['price_type']}",
        f"Order valid days after trigger: {order_event['valid_days']}",
    ]


def render_stop_loss_request(title: str, preview: dict[str, Any]) -> None:
    render_message(title, format_stop_loss_request(preview))


def render_result(title: str, result: Any) -> None:
    if isinstance(result, dict):
        scalar_rows = [
            (key, value)
            for key, value in result.items()
            if not isinstance(value, (dict, list))
        ]
        if scalar_rows:
            render_table(title, ["Field", "Value"], scalar_rows)
            return

    render_message(title, ["Avanza accepted the request, but returned no concise status fields."])


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD format.") from exc


def enum_value(enum_class: Any, value: str) -> Any:
    normalized = value.strip().upper().replace("-", "_")
    try:
        return enum_class[normalized]
    except KeyError as exc:
        choices = ", ".join(item.name.lower().replace("_", "-") for item in enum_class)
        raise argparse.ArgumentTypeError(f"Invalid value '{value}'. Choices: {choices}") from exc


def nested_value(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def amount(data: dict[str, Any], *path: str) -> str:
    value = nested_value(data, *path)
    if isinstance(value, dict):
        raw = value.get("value", "")
        unit = value.get("unit", "")
        return f"{raw} {unit}".strip()
    return str(value)


def account_display_name(account: dict[str, Any]) -> str:
    name = account.get("name", "")
    if isinstance(name, dict):
        return str(name.get("userDefinedName") or name.get("defaultName") or "")
    return str(name)


def account_row(account: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(account.get("id", "")),
        account_display_name(account),
        str(account.get("type", "")),
        amount(account, "totalValue"),
        amount(account, "buyingPower"),
        str(account.get("status", "")),
    )


def account_rows_from_overview(overview: dict[str, Any]) -> list[dict[str, Any]]:
    accounts = overview.get("accounts", [])
    return [account for account in accounts if isinstance(account, dict) and account.get("id")]


def account_id_for_item(item: dict[str, Any]) -> str:
    return str(nested_value(item, "account", "id"))


def matches_account(item: dict[str, Any], account_id: str | None) -> bool:
    return not account_id or account_id_for_item(item) == account_id


def position_row(item: dict[str, Any]) -> tuple[str, ...]:
    instrument = item.get("instrument") or {}
    orderbook = instrument.get("orderbook") or {}
    performance = item.get("lastTradingDayPerformance") or {}

    return (
        str(nested_value(item, "account", "name")),
        str(nested_value(item, "account", "id")),
        str(instrument.get("name", "")),
        str(orderbook.get("id", "")),
        str(instrument.get("isin", "")),
        amount(item, "volume"),
        amount(item, "value"),
        amount(item, "averageAcquiredPrice"),
        amount(item, "acquiredValue"),
        amount(performance, "relative"),
    )


def cash_row(item: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(nested_value(item, "account", "name")),
        str(nested_value(item, "account", "id")),
        "Cash",
        "",
        "",
        "",
        amount(item, "totalBalance"),
        "",
        "",
        "",
    )


def stop_loss_row(item: dict[str, Any]) -> tuple[str, ...]:
    account = item.get("account") or {}
    orderbook = item.get("orderbook") or {}
    trigger = item.get("trigger") or {}
    order = item.get("order") or {}

    return (
        str(item.get("id", "")),
        str(item.get("status", "")),
        str(account.get("name", "")),
        str(account.get("id", "")),
        str(orderbook.get("name", "")),
        str(orderbook.get("id", "")),
        f"{trigger.get('type', '')} {trigger.get('value', '')} {trigger.get('valueType', '')}",
        f"{order.get('type', '')} {order.get('volume', '')} @ {order.get('price', '')} {order.get('priceType', '')}",
        str(trigger.get("validUntil", "")),
    )


def stop_loss_request_log_lines(preview: dict[str, Any]) -> list[str]:
    return [line.replace("[", "\\[").replace("]", "\\]") for line in format_stop_loss_request(preview)]


def render_accounts_overview(overview: dict[str, Any]) -> None:
    accounts = account_rows_from_overview(overview)
    if not accounts:
        render_message("Accounts", ["No accounts found."])
        return

    render_table(
        "Accounts",
        ["Account ID", "Name", "Type", "Total Value", "Buying Power", "Status"],
        [account_row(account) for account in accounts],
    )


def render_portfolio_positions(positions: dict[str, Any]) -> None:
    position_rows: list[tuple[Any, ...]] = []
    for section in ("withOrderbook", "withoutOrderbook"):
        for item in positions.get(section, []):
            if isinstance(item, dict):
                position_rows.append(position_row(item))

    cash_rows = [
        cash_row(item)
        for item in positions.get("cashPositions", [])
        if isinstance(item, dict)
    ]

    if position_rows:
        render_table(
            "Portfolio Positions",
            [
                "Account",
                "Account ID",
                "Instrument",
                "Order Book ID",
                "ISIN",
                "Volume",
                "Value",
                "Avg Price",
                "Acquired",
                "Day %",
            ],
            position_rows,
        )
    else:
        render_message("Portfolio Positions", ["No instrument positions found."])

    if cash_rows:
        render_table(
            "Cash Positions",
            [
                "Account",
                "Account ID",
                "Type",
                "Order Book ID",
                "ISIN",
                "Volume",
                "Balance",
                "Avg Price",
                "Acquired",
                "Day %",
            ],
            cash_rows,
        )


def render_portfolio_summary(positions: dict[str, Any]) -> None:
    render_message(
        "Portfolio Summary",
        [
            f"Listed positions: {len(positions.get('withOrderbook', []))}",
            f"Unlisted positions: {len(positions.get('withoutOrderbook', []))}",
            f"Cash positions: {len(positions.get('cashPositions', []))}",
        ],
    )
    cash_rows = [
        cash_row(item)
        for item in positions.get("cashPositions", [])
        if isinstance(item, dict)
    ]
    if cash_rows:
        render_table(
            "Cash Positions",
            [
                "Account",
                "Account ID",
                "Type",
                "Order Book ID",
                "ISIN",
                "Volume",
                "Balance",
                "Avg Price",
                "Acquired",
                "Day %",
            ],
            cash_rows,
        )


def flattened_search_hits(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, dict):
        return []

    rows: list[dict[str, Any]] = []
    for hit_group in results.get("hits", []):
        if not isinstance(hit_group, dict):
            continue
        group_type = hit_group.get("instrumentType", "")
        top_hits = hit_group.get("topHits") or []
        for hit in top_hits:
            if isinstance(hit, dict):
                row = dict(hit)
                row.setdefault("instrumentType", group_type)
                rows.append(row)
    return rows


def render_search_results(results: Any) -> None:
    hits = flattened_search_hits(results)
    if not hits:
        render_message("Search Results", ["No matching stocks found."])
        return

    rows = []
    for hit in hits:
        rows.append(
            (
                hit.get("name", ""),
                hit.get("tickerSymbol", ""),
                hit.get("instrumentType", ""),
                hit.get("id", "") or hit.get("orderbookId", ""),
                hit.get("isin", ""),
                hit.get("currency", ""),
            )
        )

    render_table(
        "Search Results",
        ["Name", "Ticker", "Type", "Order Book ID", "ISIN", "Currency"],
        rows,
    )


def render_stoplosses(stoplosses: Any) -> None:
    if not isinstance(stoplosses, list):
        render_message("Stop-Loss Orders", ["Unexpected response shape from Avanza."])
        return

    rows = [stop_loss_row(item) for item in stoplosses if isinstance(item, dict)]
    if not rows:
        render_message("Stop-Loss Orders", ["No open stop-loss orders found."])
        return

    render_table(
        "Stop-Loss Orders",
        [
            "ID",
            "Status",
            "Account",
            "Account ID",
            "Instrument",
            "Order Book ID",
            "Trigger",
            "Order",
            "Valid Until",
        ],
        rows,
    )


class AvanzaTradingTui(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #content {
        height: 1fr;
    }

    .panel {
        border: solid $accent;
        padding: 1;
        height: auto;
    }

    #left {
        width: 42;
        height: 100%;
    }

    #right {
        width: 1fr;
        height: 100%;
    }

    DataTable {
        height: 1fr;
    }

    #portfolio-table {
        height: 2fr;
        margin-bottom: 1;
    }

    #stoploss-table {
        height: 2fr;
        margin-bottom: 1;
    }

    #log {
        height: 9;
        border: solid $secondary;
    }

    Button {
        margin-top: 1;
        margin-right: 1;
    }

    Input {
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_stoplosses", "Refresh Stop-Losses"),
        ("p", "refresh_portfolio", "Refresh Portfolio"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.avanza: Avanza | None = None
        self.accounts: list[dict[str, Any]] = []
        self.selected_account_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="content"):
            with Vertical(id="left"):
                yield Static("Login", classes="panel")
                yield Input(placeholder="Username", id="username")
                yield Input(placeholder="Password", id="password", password=True)
                yield Input(
                    placeholder="Current TOTP code",
                    id="totp",
                    password=True,
                    restrict=r"[0-9]*",
                    max_length=8,
                )
                yield Button("Login", id="login", variant="primary")

                yield Static("New Stop-Loss", classes="panel")
                yield Input(placeholder="Account ID", id="account-id")
                yield Input(placeholder="Order book ID", id="order-book-id")
                yield Input(placeholder="Volume", id="volume", type="number")
                yield Input(value="follow-upwards", placeholder="Trigger type", id="trigger-type")
                yield Input(placeholder="Trigger value", id="trigger-value", type="number")
                yield Input(value="percentage", placeholder="Trigger value type", id="trigger-value-type")
                yield Input(placeholder=f"Valid until ({date.today().isoformat()})", id="valid-until")
                yield Input(value="sell", placeholder="Order type", id="order-type")
                yield Input(placeholder="Order price", id="order-price", type="number")
                yield Input(value="percentage", placeholder="Order price type", id="order-price-type")
                yield Input(value="1", placeholder="Order valid days", id="order-valid-days", type="integer")
                yield Input(placeholder='Type "PLACE" to enable live placement', id="place-confirm")
                with Horizontal():
                    yield Button("Dry Run", id="dry-run", variant="default")
                    yield Button("Place Live", id="place-live", variant="error")

            with Vertical(id="right"):
                yield Static("Accounts", classes="panel")
                yield DataTable(id="accounts-table")
                with Horizontal():
                    yield Button("Use Selected Account", id="select-account", variant="primary")
                    yield Button("Refresh Accounts", id="refresh-accounts")
                yield Static("Selected account: none", id="selected-account")
                yield Static("Portfolio Positions", classes="panel")
                yield DataTable(id="portfolio-table")
                with Horizontal():
                    yield Button("Refresh Portfolio", id="refresh-portfolio", variant="primary")
                yield Static("Open Stop-Loss Orders", classes="panel")
                yield DataTable(id="stoploss-table")
                with Horizontal():
                    yield Button("Refresh Stop-Losses", id="refresh", variant="primary")
                    yield Button("Clear Log", id="clear-log")
                yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        accounts_table = self.query_one("#accounts-table", DataTable)
        accounts_table.add_columns(
            "Account ID",
            "Name",
            "Type",
            "Total Value",
            "Buying Power",
            "Status",
        )
        accounts_table.cursor_type = "row"
        accounts_table.zebra_stripes = True

        stoploss_table = self.query_one("#stoploss-table", DataTable)
        stoploss_table.add_columns(
            "ID",
            "Status",
            "Account",
            "Account ID",
            "Instrument",
            "Order Book ID",
            "Trigger",
            "Order",
            "Valid Until",
        )
        stoploss_table.cursor_type = "row"
        stoploss_table.zebra_stripes = True

        portfolio_table = self.query_one("#portfolio-table", DataTable)
        portfolio_table.add_columns(
            "Account",
            "Account ID",
            "Instrument",
            "Order Book ID",
            "ISIN",
            "Volume",
            "Value",
            "Avg Price",
            "Acquired",
            "Day %",
        )
        portfolio_table.cursor_type = "row"
        portfolio_table.zebra_stripes = True
        self.log("Ready. Log in, then refresh portfolio or stop-losses.")

    def input_value(self, widget_id: str) -> str:
        return self.query_one(f"#{widget_id}", Input).value.strip()

    def clear_secret_inputs(self) -> None:
        self.query_one("#password", Input).value = ""
        self.query_one("#totp", Input).value = ""

    def log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def require_connection(self) -> Avanza:
        if self.avanza is None:
            raise RuntimeError("Log in first.")
        return self.avanza

    def require_selected_account_id(self) -> str:
        if not self.selected_account_id:
            raise RuntimeError("Select an account first.")
        return self.selected_account_id

    def set_selected_account(self, account: dict[str, Any]) -> None:
        account_id = str(account.get("id", ""))
        if not account_id:
            raise ValueError("Selected account has no id.")

        self.selected_account_id = account_id
        self.query_one("#account-id", Input).value = account_id
        label = f"Selected account: {account_display_name(account)} ({account_id})"
        self.query_one("#selected-account", Static).update(label)
        self.log(f"Selected account {account_display_name(account)} ({account_id}).")

    def build_stop_loss_request(self) -> tuple[StopLossTrigger, StopLossOrderEvent, dict[str, Any]]:
        selected_account_id = self.require_selected_account_id()
        valid_until = date.fromisoformat(self.input_value("valid-until"))
        trigger = StopLossTrigger(
            type=enum_value(StopLossTriggerType, self.input_value("trigger-type")),
            value=float(self.input_value("trigger-value")),
            valid_until=valid_until,
            value_type=enum_value(StopLossPriceType, self.input_value("trigger-value-type")),
        )
        order_event = StopLossOrderEvent(
            type=enum_value(OrderType, self.input_value("order-type")),
            price=float(self.input_value("order-price")),
            volume=float(self.input_value("volume")),
            valid_days=int(self.input_value("order-valid-days")),
            price_type=enum_value(StopLossPriceType, self.input_value("order-price-type")),
            short_selling_allowed=False,
        )
        preview = {
            "account_id": selected_account_id,
            "order_book_id": self.input_value("order-book-id"),
            "parent_stop_loss_id": "0",
            "stop_loss_trigger": {
                "type": trigger.type.value,
                "value": trigger.value,
                "valid_until": trigger.valid_until.isoformat(),
                "value_type": trigger.value_type.value,
            },
            "stop_loss_order_event": {
                "type": order_event.type.value,
                "price": order_event.price,
                "volume": order_event.volume,
                "valid_days": order_event.valid_days,
                "price_type": order_event.price_type.value,
                "short_selling_allowed": order_event.short_selling_allowed,
            },
        }
        return trigger, order_event, preview

    def refresh_stoplosses(self) -> None:
        avanza = self.require_connection()
        table = self.query_one("#stoploss-table", DataTable)
        table.clear()

        data = avanza.get_all_stop_losses()
        if not isinstance(data, list):
            self.log(f"[yellow]Unexpected stop-loss response type:[/yellow] {type(data).__name__}")
            return

        visible_count = 0
        for item in data:
            if isinstance(item, dict):
                if not matches_account(item, self.selected_account_id):
                    continue
                table.add_row(*stop_loss_row(item), key=str(item.get("id", "")))
                visible_count += 1

        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.log(f"Loaded {visible_count} open stop-loss order(s){suffix}.")

    def refresh_accounts(self) -> None:
        avanza = self.require_connection()
        table = self.query_one("#accounts-table", DataTable)
        table.clear()

        overview = avanza.get_overview()
        if not isinstance(overview, dict):
            self.log(f"[yellow]Unexpected account overview response type:[/yellow] {type(overview).__name__}")
            return

        self.accounts = account_rows_from_overview(overview)
        for account in self.accounts:
            table.add_row(*account_row(account), key=str(account.get("id", "")))

        self.log(f"Loaded {len(self.accounts)} account(s).")
        if self.accounts and not self.selected_account_id:
            self.set_selected_account(self.accounts[0])

    def refresh_portfolio(self) -> None:
        avanza = self.require_connection()
        table = self.query_one("#portfolio-table", DataTable)
        table.clear()

        data = avanza.get_accounts_positions()
        if not isinstance(data, dict):
            self.log(f"[yellow]Unexpected portfolio response type:[/yellow] {type(data).__name__}")
            return

        count = 0
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in data.get(section, []):
                if isinstance(item, dict):
                    if not matches_account(item, self.selected_account_id):
                        continue
                    table.add_row(*position_row(item), key=str(item.get("id", f"{section}-{count}")))
                    count += 1

        for item in data.get("cashPositions", []):
            if isinstance(item, dict):
                if not matches_account(item, self.selected_account_id):
                    continue
                table.add_row(*cash_row(item), key=str(item.get("id", f"cash-{count}")))
                count += 1

        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.log(f"Loaded {count} portfolio row(s){suffix}.")

    def action_refresh_stoplosses(self) -> None:
        try:
            self.refresh_stoplosses()
        except Exception as exc:
            self.log(f"[red]Refresh failed:[/red] {exc}")

    def action_refresh_portfolio(self) -> None:
        try:
            self.refresh_portfolio()
        except Exception as exc:
            self.log(f"[red]Portfolio refresh failed:[/red] {exc}")

    def selected_account_from_table(self) -> dict[str, Any]:
        table = self.query_one("#accounts-table", DataTable)
        row_index = table.cursor_coordinate.row
        if row_index < 0 or row_index >= len(self.accounts):
            raise ValueError("No account row is selected.")
        return self.accounts[row_index]

    def select_account_from_table(self) -> None:
        self.set_selected_account(self.selected_account_from_table())
        self.refresh_portfolio()
        self.refresh_stoplosses()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        try:
            if button_id == "login":
                self.handle_login()
            elif button_id == "select-account":
                self.select_account_from_table()
            elif button_id == "refresh-accounts":
                self.refresh_accounts()
            elif button_id == "refresh":
                self.refresh_stoplosses()
            elif button_id == "refresh-portfolio":
                self.refresh_portfolio()
            elif button_id == "clear-log":
                self.query_one("#log", RichLog).clear()
            elif button_id == "dry-run":
                self.handle_dry_run()
            elif button_id == "place-live":
                self.handle_place_live()
        except Exception as exc:
            self.log(f"[red]Error:[/red] {exc}")

    def handle_login(self) -> None:
        username = self.input_value("username")
        password = self.input_value("password")
        totp = self.input_value("totp")
        if not username or not password or not totp:
            raise ValueError("Username, password, and TOTP are required.")

        self.log("Logging in...")
        self.avanza = Avanza({"username": username, "password": password, "totpToken": totp})
        self.clear_secret_inputs()
        self.log("[green]Logged in. Secret fields cleared.[/green]")
        self.refresh_accounts()
        self.refresh_portfolio()
        self.refresh_stoplosses()

    def handle_dry_run(self) -> None:
        _, _, preview = self.build_stop_loss_request()
        self.log("[yellow]Dry-run stop-loss request:[/yellow]")
        for line in stop_loss_request_log_lines(preview):
            self.log(line)

    def handle_place_live(self) -> None:
        if self.input_value("place-confirm") != "PLACE":
            raise ValueError('Type "PLACE" in the confirmation field before live placement.')

        avanza = self.require_connection()
        trigger, order_event, preview = self.build_stop_loss_request()
        self.log("[red]Placing live stop-loss request:[/red]")
        for line in stop_loss_request_log_lines(preview):
            self.log(line)

        result = avanza.place_stop_loss_order(
            parent_stop_loss_id="0",
            account_id=self.require_selected_account_id(),
            order_book_id=self.input_value("order-book-id"),
            stop_loss_trigger=trigger,
            stop_loss_order_event=order_event,
        )
        if isinstance(result, dict):
            status = result.get("status") or result.get("orderRequestStatus") or "response received"
            identifier = result.get("stoplossOrderId") or result.get("orderId") or ""
            suffix = f" ({identifier})" if identifier else ""
            self.log(f"[green]Avanza status:[/green] {status}{suffix}")
        else:
            self.log("[green]Avanza accepted the request.[/green]")
        self.refresh_stoplosses()


def cmd_tui(_args: argparse.Namespace) -> None:
    AvanzaTradingTui().run()


def cmd_accounts(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_accounts_overview(avanza.get_overview())


def cmd_portfolio_positions(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_portfolio_positions(avanza.get_accounts_positions())


def cmd_portfolio_summary(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_portfolio_summary(avanza.get_accounts_positions())


def cmd_search(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_search_results(avanza.search_for_stock(args.query, args.limit))


def cmd_stoploss_list(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_stoplosses(avanza.get_all_stop_losses())


def cmd_stoploss_delete(args: argparse.Namespace) -> None:
    if not args.confirm:
        render_message(
            "Dry Run",
            [
                "Add --confirm to delete this stop-loss order.",
                f"Account: {args.account_id}",
                f"Stop-loss ID: {args.stop_loss_id}",
            ],
        )
        return

    avanza = connect(args)
    result = avanza.delete_stop_loss_order(args.account_id, args.stop_loss_id)
    render_result("Delete Stop-Loss Result", {"deleted": True, "result": result})


def cmd_stoploss_set(args: argparse.Namespace) -> None:
    trigger_type = enum_value(StopLossTriggerType, args.trigger_type)
    trigger_value_type = enum_value(StopLossPriceType, args.trigger_value_type)
    order_type = enum_value(OrderType, args.order_type)
    order_price_type = enum_value(StopLossPriceType, args.order_price_type)

    trigger = StopLossTrigger(
        type=trigger_type,
        value=args.trigger_value,
        valid_until=args.valid_until,
        value_type=trigger_value_type,
        trigger_on_market_maker_quote=args.trigger_on_market_maker_quote,
    )
    order_event = StopLossOrderEvent(
        type=order_type,
        price=args.order_price,
        volume=args.volume,
        valid_days=args.order_valid_days,
        price_type=order_price_type,
        short_selling_allowed=args.short_selling_allowed,
    )

    request_preview = {
        "parent_stop_loss_id": args.parent_stop_loss_id,
        "account_id": args.account_id,
        "order_book_id": args.order_book_id,
        "stop_loss_trigger": {
            "type": trigger.type.value,
            "value": trigger.value,
            "valid_until": trigger.valid_until.isoformat(),
            "value_type": trigger.value_type.value,
            "trigger_on_market_maker_quote": trigger.trigger_on_market_maker_quote,
        },
        "stop_loss_order_event": {
            "type": order_event.type.value,
            "price": order_event.price,
            "volume": order_event.volume,
            "valid_days": order_event.valid_days,
            "price_type": order_event.price_type.value,
            "short_selling_allowed": order_event.short_selling_allowed,
        },
    }

    if not args.confirm:
        render_stop_loss_request(
            "Dry Run: add --confirm to place this stop-loss order.",
            request_preview,
        )
        return

    avanza = connect(args)
    result = avanza.place_stop_loss_order(
        parent_stop_loss_id=args.parent_stop_loss_id,
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        stop_loss_trigger=trigger,
        stop_loss_order_event=order_event,
    )
    render_result("Place Stop-Loss Result", result)


def add_common_auth(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--username", help="Avanza username. If omitted, prompts interactively.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI for viewing Avanza portfolio data and managing stop-loss orders."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tui = subparsers.add_parser("tui", help="Launch the interactive Textual terminal UI.")
    tui.set_defaults(func=cmd_tui)

    accounts = subparsers.add_parser("accounts", help="Show account overview.")
    add_common_auth(accounts)
    accounts.set_defaults(func=cmd_accounts)

    portfolio = subparsers.add_parser("portfolio", help="View portfolio details.")
    portfolio_subparsers = portfolio.add_subparsers(dest="portfolio_command", required=True)

    portfolio_summary = portfolio_subparsers.add_parser("summary", help="Show portfolio counts and cash positions.")
    add_common_auth(portfolio_summary)
    portfolio_summary.set_defaults(func=cmd_portfolio_summary)

    portfolio_positions = portfolio_subparsers.add_parser("positions", help="Show all portfolio positions.")
    add_common_auth(portfolio_positions)
    portfolio_positions.set_defaults(func=cmd_portfolio_positions)

    search = subparsers.add_parser("search-stock", help="Search stocks by name, ticker, or ISIN.")
    add_common_auth(search)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=cmd_search)

    stoploss = subparsers.add_parser("stoploss", help="Manage stop-loss orders.")
    stoploss_subparsers = stoploss.add_subparsers(dest="stoploss_command", required=True)

    stoploss_list = stoploss_subparsers.add_parser("list", help="List open stop-loss orders.")
    add_common_auth(stoploss_list)
    stoploss_list.set_defaults(func=cmd_stoploss_list)

    stoploss_delete = stoploss_subparsers.add_parser("delete", help="Delete a stop-loss order.")
    add_common_auth(stoploss_delete)
    stoploss_delete.add_argument("--account-id", required=True)
    stoploss_delete.add_argument("--stop-loss-id", required=True)
    stoploss_delete.add_argument("--confirm", action="store_true")
    stoploss_delete.set_defaults(func=cmd_stoploss_delete)

    stoploss_set = stoploss_subparsers.add_parser("set", help="Set a stop-loss order.")
    add_common_auth(stoploss_set)
    stoploss_set.add_argument("--account-id", required=True)
    stoploss_set.add_argument("--order-book-id", required=True)
    stoploss_set.add_argument("--parent-stop-loss-id", default="0")
    stoploss_set.add_argument("--trigger-type", required=True, help="less-or-equal, more-or-equal, follow-upwards, follow-downwards")
    stoploss_set.add_argument("--trigger-value", required=True, type=float)
    stoploss_set.add_argument("--trigger-value-type", default="monetary", help="monetary or percentage")
    stoploss_set.add_argument("--valid-until", required=True, type=parse_date)
    stoploss_set.add_argument("--trigger-on-market-maker-quote", action="store_true")
    stoploss_set.add_argument("--order-type", default="sell", help="buy or sell")
    stoploss_set.add_argument("--order-price", required=True, type=float)
    stoploss_set.add_argument("--order-price-type", default="monetary", help="monetary or percentage")
    stoploss_set.add_argument("--volume", required=True, type=float)
    stoploss_set.add_argument("--order-valid-days", default=1, type=int)
    stoploss_set.add_argument("--short-selling-allowed", action="store_true")
    stoploss_set.add_argument("--confirm", action="store_true")
    stoploss_set.set_defaults(func=cmd_stoploss_set)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
        return 0
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
