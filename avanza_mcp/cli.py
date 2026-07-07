"""Command-line interface: subcommands, argument parser, and entry point."""

import argparse
import os
import sys
import textwrap
import webbrowser

from avanza_mcp.auth import connect
from avanza_mcp.config import (
    APP_NAME,
    APP_VERSION,
    DEBUG_PROFILE_TOP_DEFAULT,
    HELP_FORMATTER,
    KNOWN_ORDERBOOK_METADATA,
    MCP_SESSION_FILE,
    ORDER_CONDITION_CHOICES,
    ORDER_TYPE_CHOICES,
    STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
    TRIGGER_TYPE_CHOICES,
    VALID_UNTIL_MAX_DAYS,
    WEB_DEFAULT_PORT,
    WEB_SESSION_FILE,
)
from avanza_mcp.market_data import merged_orderbook_metadata
from avanza_mcp.mcp.proxy import run_mcp_stdio_proxy
from avanza_mcp.records import (
    parse_transaction_types,
    render_orders,
    render_search_results,
    render_stoplosses,
    render_transactions_history,
    stoploss_instrument_metadata,
)
from avanza_mcp.rendering import (
    build_order_preview,
    build_stop_loss_preview,
    format_stop_loss_request,
    parse_date,
    parse_price_type,
    render_accounts_overview,
    render_message,
    render_order_request,
    render_portfolio_positions,
    render_portfolio_summary,
    render_result,
    render_stop_loss_request,
)
from avanza_mcp.stoploss_rules import (
    enforce_live_stoploss_order_valid_days,
    max_valid_until_date,
    stoploss_order_valid_days_warnings,
)
from pathlib import Path
from avanza_mcp.tui.app import AvanzaTradingTui
from avanza_mcp.uilock import acquire_ui_lock, release_ui_lock


def cmd_tui(args: argparse.Namespace) -> None:
    try:
        acquire_ui_lock("tui")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
    try:
        result = AvanzaTradingTui(
            debug=bool(getattr(args, "debug", False)),
            debug_profile_top=int(getattr(args, "debug_profile_top", DEBUG_PROFILE_TOP_DEFAULT)),
        ).run()
    finally:
        release_ui_lock()
    if isinstance(result, dict) and bool(result.get("reload_tui")):
        os.execv(sys.executable, [sys.executable, *sys.argv])


def cmd_web(args: argparse.Namespace) -> None:
    try:
        acquire_ui_lock("web")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
    try:
        import uvicorn

        from avanza_mcp.web.app import create_web_app
        from avanza_mcp.web.runtime import WebRuntime

        port = int(getattr(args, "port", WEB_DEFAULT_PORT))
        runtime = WebRuntime(port=port, debug=bool(getattr(args, "debug", False)))
        runtime.auth.write_session_file()
        app = create_web_app(runtime)
        url = runtime.auth.url
        print(f"Avanza-MCP Web UI: {url}")
        print(f"Login token: {runtime.auth.login_token}")
        print(f"(also written to {WEB_SESSION_FILE.name}; keep it private)")
        if not getattr(args, "no_browser", False):
            try:
                webbrowser.open(url)
            except Exception:
                pass
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        try:
            runtime.stop()
        except Exception:
            pass
        release_ui_lock()


def cmd_mcp(args: argparse.Namespace) -> None:
    run_mcp_stdio_proxy(Path(args.session_file))


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
    trigger, order_event, request_preview = build_stop_loss_preview(vars(args))
    metadata = merged_orderbook_metadata(
        {"orderbook_id": args.order_book_id},
        KNOWN_ORDERBOOK_METADATA.get(str(args.order_book_id), {}),
    )
    request_preview["warnings"] = stoploss_order_valid_days_warnings(order_event.valid_days, metadata)

    if not args.confirm:
        render_stop_loss_request(
            "Dry Run: add --confirm to place this stop-loss order.",
            request_preview,
        )
        return

    avanza = connect(args)
    live_metadata = stoploss_instrument_metadata(avanza, str(args.order_book_id), base=metadata)
    request_preview["warnings"] = enforce_live_stoploss_order_valid_days(
        order_event.valid_days,
        live_metadata,
        live=True,
    )
    result = avanza.place_stop_loss_order(
        parent_stop_loss_id=args.parent_stop_loss_id,
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        stop_loss_trigger=trigger,
        stop_loss_order_event=order_event,
    )
    render_result("Place Stop-Loss Result", result)


def cmd_stoploss_edit(args: argparse.Namespace) -> None:
    trigger, order_event, request_preview = build_stop_loss_preview(vars(args))
    request_preview["stop_loss_id"] = args.stop_loss_id
    metadata = merged_orderbook_metadata(
        {"orderbook_id": args.order_book_id},
        KNOWN_ORDERBOOK_METADATA.get(str(args.order_book_id), {}),
    )
    request_preview["warnings"] = stoploss_order_valid_days_warnings(order_event.valid_days, metadata)

    if not args.confirm:
        render_message(
            "Dry Run: add --confirm to update this stop-loss (delete + place replacement).",
            [
                f"Existing stop-loss ID: {args.stop_loss_id}",
                *format_stop_loss_request(request_preview),
            ],
        )
        return

    avanza = connect(args)
    live_metadata = stoploss_instrument_metadata(avanza, str(args.order_book_id), base=metadata)
    request_preview["warnings"] = enforce_live_stoploss_order_valid_days(
        order_event.valid_days,
        live_metadata,
        live=True,
    )
    delete_result = avanza.delete_stop_loss_order(args.account_id, args.stop_loss_id)
    place_result = avanza.place_stop_loss_order(
        parent_stop_loss_id=args.parent_stop_loss_id,
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        stop_loss_trigger=trigger,
        stop_loss_order_event=order_event,
    )
    render_result(
        "Update Stop-Loss Result",
        {"updated": True, "deleted": delete_result, "placed": place_result},
    )


def cmd_orders_list(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_orders(avanza.get_orders())


def cmd_transactions_list(args: argparse.Namespace) -> None:
    avanza = connect(args)
    if args.max_elements < 1:
        raise ValueError("--max-elements must be >= 1.")
    transaction_types = parse_transaction_types(args.types)
    transactions_from = None if args.all else args.transactions_from
    transactions_to = None if args.all else args.transactions_to
    if transactions_from and transactions_to and transactions_from > transactions_to:
        raise ValueError("--from cannot be after --to.")
    payload = avanza.get_transactions_details(
        transaction_details_types=transaction_types,
        transactions_from=transactions_from,
        transactions_to=transactions_to,
        isin=args.isin,
        max_elements=args.max_elements,
    )
    render_transactions_history(
        payload,
        account_id=args.account_id,
        executed_only=not args.include_non_executed,
    )


def cmd_order_delete(args: argparse.Namespace) -> None:
    if not args.confirm:
        render_message(
            "Dry Run",
            [
                "Add --confirm to delete this regular order.",
                f"Account: {args.account_id}",
                f"Order ID: {args.order_id}",
            ],
        )
        return

    avanza = connect(args)
    result = avanza.delete_order(args.account_id, args.order_id)
    render_result("Delete Order Result", {"deleted": True, "result": result})


def cmd_order_set(args: argparse.Namespace) -> None:
    order_type, condition, preview = build_order_preview(
        {
            "account_id": args.account_id,
            "order_book_id": args.order_book_id,
            "order_type": args.order_type,
            "price": args.price,
            "valid_until": args.valid_until,
            "volume": args.volume,
            "condition": args.condition,
        }
    )

    if not args.confirm:
        render_order_request(
            "Dry Run: add --confirm to place this buy/sell order.",
            preview,
        )
        return

    avanza = connect(args)
    result = avanza.place_order(
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        order_type=order_type,
        price=args.price,
        valid_until=args.valid_until,
        volume=args.volume,
        condition=condition,
    )
    render_result("Place Order Result", result)


def add_common_auth(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--username",
        metavar="USER",
        help="Avanza username. If omitted, you are prompted interactively.",
    )
    parser.add_argument(
        "--onepassword-item",
        metavar="ITEM",
        help="Read Avanza username, password, and TOTP from a 1Password item via the op CLI.",
    )
    parser.add_argument(
        "--onepassword-vault",
        metavar="VAULT",
        help="Optional 1Password vault name or ID for --onepassword-item.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avanza_cli.py",
        formatter_class=HELP_FORMATTER,
        description="Human-readable Avanza account, portfolio, search, order, and stop-loss tools.",
        epilog=textwrap.dedent(
            """\
            Common examples:
              python avanza_cli.py tui
              python avanza_cli.py accounts
              python avanza_cli.py portfolio summary
              python avanza_cli.py portfolio positions
              python avanza_cli.py search-stock "VOLV B"
              python avanza_cli.py transactions list
              python avanza_cli.py orders list
              python avanza_cli.py stoploss list

            Credentials:
              Password and current TOTP code are prompted interactively and masked.
              Or use --onepassword-item ITEM with the 1Password CLI.

            Safety:
              Mutating commands dry-run unless you pass --confirm.
            """
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tui = subparsers.add_parser(
        "tui",
        formatter_class=HELP_FORMATTER,
        help="Launch the interactive Textual terminal UI.",
        description="Launch the interactive terminal UI for account switching, portfolio viewing, and stop-loss management.",
    )
    tui.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug profiling mode. Writes timing/profile logs under avanza-cli/logs/.",
    )
    tui.add_argument(
        "--debug-profile-top",
        metavar="N",
        type=int,
        default=DEBUG_PROFILE_TOP_DEFAULT,
        help=f"How many top functions to include per profile sample in --debug mode. Default: {DEBUG_PROFILE_TOP_DEFAULT}.",
    )
    tui.set_defaults(func=cmd_tui)

    web = subparsers.add_parser(
        "web",
        formatter_class=HELP_FORMATTER,
        help="Launch the local Web UI (mutually exclusive with the TUI).",
        description=textwrap.dedent(
            """\
            Launch the Avanza-MCP Web UI on 127.0.0.1. Prints a one-time login
            token to the terminal; paste it into the browser login form. The
            Web UI and the TUI are mutually exclusive - run one at a time.
            """
        ),
    )
    web.add_argument(
        "--port",
        type=int,
        default=WEB_DEFAULT_PORT,
        help=f"Port to bind on 127.0.0.1. Default: {WEB_DEFAULT_PORT}.",
    )
    web.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically.",
    )
    web.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for the web kernel.",
    )
    web.set_defaults(func=cmd_web)

    mcp = subparsers.add_parser(
        "mcp",
        formatter_class=HELP_FORMATTER,
        help="Run the stdio MCP proxy for a TUI-managed authenticated session.",
        description=textwrap.dedent(
            """\
            Run a stdio MCP server proxy that forwards tool calls to the currently
            authenticated TUI MCP bridge. Start `python avanza_cli.py tui`, log in,
            enable MCP mode in the TUI, then configure Codex/desktop clients to run
            this command.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Example MCP server command:
              python avanza_cli.py mcp
            """
        ),
    )
    mcp.add_argument(
        "--session-file",
        default=str(MCP_SESSION_FILE),
        help="Path to the TUI-written MCP session file. Default: .avanza_mcp_session.json next to avanza_cli.py.",
    )
    mcp.set_defaults(func=cmd_mcp)

    accounts = subparsers.add_parser(
        "accounts",
        formatter_class=HELP_FORMATTER,
        help="Show all accounts with balances and buying power.",
        description="Show all Avanza accounts in a readable table.",
        epilog="Example:\n  python avanza_cli.py accounts",
    )
    add_common_auth(accounts)
    accounts.set_defaults(func=cmd_accounts)

    portfolio = subparsers.add_parser(
        "portfolio",
        formatter_class=HELP_FORMATTER,
        help="View portfolio summaries and positions.",
        description="View portfolio data across accounts in readable terminal tables.",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py portfolio summary
              python avanza_cli.py portfolio positions
            """
        ),
    )
    portfolio_subparsers = portfolio.add_subparsers(dest="portfolio_command", required=True)

    portfolio_summary = portfolio_subparsers.add_parser(
        "summary",
        formatter_class=HELP_FORMATTER,
        help="Show position counts and cash balances.",
        description="Show portfolio position counts and cash positions.",
        epilog="Example:\n  python avanza_cli.py portfolio summary",
    )
    add_common_auth(portfolio_summary)
    portfolio_summary.set_defaults(func=cmd_portfolio_summary)

    portfolio_positions = portfolio_subparsers.add_parser(
        "positions",
        formatter_class=HELP_FORMATTER,
        help="Show instrument and cash positions.",
        description="Show all portfolio stock positions and cash balances in tables.",
        epilog="Example:\n  python avanza_cli.py portfolio positions",
    )
    add_common_auth(portfolio_positions)
    portfolio_positions.set_defaults(func=cmd_portfolio_positions)

    search = subparsers.add_parser(
        "search-stock",
        formatter_class=HELP_FORMATTER,
        help="Search stocks by name, ticker, or ISIN.",
        description="Search Avanza stocks and show matching order book ids.",
        epilog='Example:\n  python avanza_cli.py search-stock "VOLV B" --limit 5',
    )
    add_common_auth(search)
    search.add_argument("query", help="Name, ticker, or ISIN to search for.")
    search.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=10,
        help="Maximum number of search results to request. Default: 10.",
    )
    search.set_defaults(func=cmd_search)

    transactions = subparsers.add_parser(
        "transactions",
        formatter_class=HELP_FORMATTER,
        help="View transaction history / executed orders.",
        description="List transaction history. Defaults to executed orders (BUY/SELL).",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py transactions list
              python avanza_cli.py transactions list --account-id ACCOUNT_ID --max-elements 5000
              python avanza_cli.py transactions list --all
              python avanza_cli.py transactions list --types BUY,SELL,DIVIDEND --from 2026-01-01 --to 2026-05-01
            """
        ),
    )
    transactions_subparsers = transactions.add_subparsers(dest="transactions_command", required=True)

    transactions_list = transactions_subparsers.add_parser(
        "list",
        formatter_class=HELP_FORMATTER,
        help="List transaction history entries.",
        description="List transaction history entries with account/date/type filters.",
    )
    add_common_auth(transactions_list)
    transactions_list.add_argument("--account-id", metavar="ID", help="Optional Avanza account id filter.")
    transactions_list.add_argument(
        "--from",
        dest="transactions_from",
        metavar="YYYY-MM-DD",
        type=parse_date,
        help="Start date filter (inclusive).",
    )
    transactions_list.add_argument(
        "--to",
        dest="transactions_to",
        metavar="YYYY-MM-DD",
        type=parse_date,
        help="End date filter (inclusive).",
    )
    transactions_list.add_argument(
        "--types",
        metavar="CSV",
        default="BUY,SELL",
        help="Comma-separated transaction types. Default: BUY,SELL.",
    )
    transactions_list.add_argument("--isin", metavar="ISIN", help="Optional ISIN filter.")
    transactions_list.add_argument(
        "--max-elements",
        metavar="N",
        type=int,
        default=1000,
        help="Maximum number of transactions to request. Default: 1000.",
    )
    transactions_list.add_argument(
        "--include-non-executed",
        action="store_true",
        help="Include non-executed types (deposits/dividends/withdrawals) in output.",
    )
    transactions_list.add_argument(
        "--all",
        action="store_true",
        help="Request practically all available history by removing date filters.",
    )
    transactions_list.set_defaults(func=cmd_transactions_list)

    orders = subparsers.add_parser(
        "orders",
        formatter_class=HELP_FORMATTER,
        help="List, create, and delete regular buy/sell orders.",
        description="Manage regular Avanza buy/sell orders. Placement and deletion dry-run unless --confirm is passed.",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py orders list
              python avanza_cli.py orders set --help
              python avanza_cli.py orders delete --help
            """
        ),
    )
    orders_subparsers = orders.add_subparsers(dest="orders_command", required=True)

    orders_list = orders_subparsers.add_parser(
        "list",
        formatter_class=HELP_FORMATTER,
        help="List open regular orders.",
        description="List open regular buy/sell orders in a readable table.",
        epilog="Example:\n  python avanza_cli.py orders list",
    )
    add_common_auth(orders_list)
    orders_list.set_defaults(func=cmd_orders_list)

    orders_delete = orders_subparsers.add_parser(
        "delete",
        formatter_class=HELP_FORMATTER,
        help="Delete a regular order.",
        description="Delete a regular order. Without --confirm this only prints the intended deletion.",
        epilog=textwrap.dedent(
            """\
            Dry-run:
              python avanza_cli.py orders delete --account-id ACCOUNT_ID --order-id ORDER_ID

            Live deletion:
              python avanza_cli.py orders delete --account-id ACCOUNT_ID --order-id ORDER_ID --confirm
            """
        ),
    )
    add_common_auth(orders_delete)
    orders_delete.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id that owns the order.")
    orders_delete.add_argument("--order-id", metavar="ID", required=True, help="Order id to delete.")
    orders_delete.add_argument("--confirm", action="store_true", help="Actually delete the order. Omit for dry-run.")
    orders_delete.set_defaults(func=cmd_order_delete)

    orders_set = orders_subparsers.add_parser(
        "set",
        formatter_class=HELP_FORMATTER,
        help="Create a regular buy/sell order.",
        description=textwrap.dedent(
            """\
            Create a regular buy/sell order.

            Without --confirm, this command prints a readable dry-run summary and does not log in.

            Conditions:
              normal         normal limit order
              fill-or-kill   fill entire order immediately or cancel
              fill-and-kill  fill available volume immediately and cancel remainder
            """
        ),
        epilog=textwrap.dedent(
            """\
            Buy order dry-run:
              python avanza_cli.py orders set \\
                --account-id ACCOUNT_ID \\
                --order-book-id ORDER_BOOK_ID \\
                --order-type buy \\
                --price 100 \\
                --valid-until 2026-05-28 \\
                --volume 10 \\
                --condition normal

            Add --confirm only after reviewing the dry-run summary.
            """
        ),
    )
    add_common_auth(orders_set)
    orders_set.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id to place the order on.")
    orders_set.add_argument("--order-book-id", metavar="ID", required=True, help="Avanza order book id for the instrument.")
    orders_set.add_argument("--order-type", choices=ORDER_TYPE_CHOICES, default="buy", help="Order side. Default: buy.")
    orders_set.add_argument("--price", metavar="SEK", required=True, type=float, help="Limit price in SEK.")
    orders_set.add_argument("--valid-until", metavar="YYYY-MM-DD", required=True, type=parse_date, help="Last date the order remains valid.")
    orders_set.add_argument("--volume", metavar="QTY", required=True, type=int, help="Number of shares/contracts to order.")
    orders_set.add_argument("--condition", choices=ORDER_CONDITION_CHOICES, default="normal", help="Order condition. Default: normal.")
    orders_set.add_argument("--confirm", action="store_true", help="Actually place the order. Omit for dry-run.")
    orders_set.set_defaults(func=cmd_order_set)

    stoploss = subparsers.add_parser(
        "stoploss",
        formatter_class=HELP_FORMATTER,
        help="List, create, and delete stop-loss orders.",
        description="Manage Avanza stop-loss orders. Placement and deletion dry-run unless --confirm is passed.",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py stoploss list
              python avanza_cli.py stoploss set --help
              python avanza_cli.py stoploss delete --help
              python avanza_cli.py stoploss edit --help
            """
        ),
    )
    stoploss_subparsers = stoploss.add_subparsers(dest="stoploss_command", required=True)

    stoploss_list = stoploss_subparsers.add_parser(
        "list",
        formatter_class=HELP_FORMATTER,
        help="List open stop-loss orders.",
        description="List open stop-loss orders in a readable table.",
        epilog="Example:\n  python avanza_cli.py stoploss list",
    )
    add_common_auth(stoploss_list)
    stoploss_list.set_defaults(func=cmd_stoploss_list)

    stoploss_delete = stoploss_subparsers.add_parser(
        "delete",
        formatter_class=HELP_FORMATTER,
        help="Delete a stop-loss order.",
        description="Delete a stop-loss order. Without --confirm this only prints the intended deletion.",
        epilog=textwrap.dedent(
            """\
            Dry-run:
              python avanza_cli.py stoploss delete --account-id ACCOUNT_ID --stop-loss-id STOP_LOSS_ID

            Live deletion:
              python avanza_cli.py stoploss delete --account-id ACCOUNT_ID --stop-loss-id STOP_LOSS_ID --confirm
            """
        ),
    )
    add_common_auth(stoploss_delete)
    stoploss_delete.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id that owns the stop-loss.")
    stoploss_delete.add_argument("--stop-loss-id", metavar="ID", required=True, help="Stop-loss id to delete.")
    stoploss_delete.add_argument("--confirm", action="store_true", help="Actually delete the stop-loss. Omit for dry-run.")
    stoploss_delete.set_defaults(func=cmd_stoploss_delete)

    stoploss_set = stoploss_subparsers.add_parser(
        "set",
        formatter_class=HELP_FORMATTER,
        help="Create a fixed or gliding stop-loss order.",
        description=textwrap.dedent(
            """\
            Create a stop-loss order.

            Without --confirm, this command prints a readable dry-run summary and does not log in.

            Trigger types:
              less-or-equal   fixed trigger at or below a price
              more-or-equal   fixed trigger at or above a price
              follow-upwards  gliding/trailing trigger for long positions
              follow-downwards gliding/trailing trigger for short/downward logic

            Price/value types:
              SEK             explicit currency value
              %               relative offset/value, interpreted by Avanza

            If --valid-until is omitted, avanza_cli auto-sets it to the longest allowed date.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Gliding sell stop-loss dry-run:
              python avanza_cli.py stoploss set \\
                --account-id ACCOUNT_ID \\
                --order-book-id ORDER_BOOK_ID \\
                --trigger-type follow-upwards \\
                --trigger-value 5 \\
                --trigger-value-type % \\
                --order-type sell \\
                --order-price 1 \\
                --order-price-type % \\
                --volume 10

            Add --confirm only after reviewing the dry-run summary.
            """
        ),
    )
    add_common_auth(stoploss_set)
    stoploss_set.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id to place the stop-loss on.")
    stoploss_set.add_argument("--order-book-id", metavar="ID", required=True, help="Avanza order book id for the instrument.")
    stoploss_set.add_argument("--parent-stop-loss-id", metavar="ID", default="0", help="Parent stop-loss id. Default: 0.")
    stoploss_set.add_argument("--trigger-type", choices=TRIGGER_TYPE_CHOICES, required=True, help="Stop-loss trigger behavior.")
    stoploss_set.add_argument("--trigger-value", metavar="VALUE", required=True, type=float, help="Trigger value, interpreted with --trigger-value-type.")
    stoploss_set.add_argument(
        "--trigger-value-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --trigger-value. Use SEK or %%. Default: SEK.",
    )
    stoploss_set.add_argument(
        "--valid-until",
        metavar="YYYY-MM-DD",
        default=max_valid_until_date().isoformat(),
        type=parse_date,
        help=f"Last date the trigger remains valid. Default: max allowed ({VALID_UNTIL_MAX_DAYS} days from today).",
    )
    stoploss_set.add_argument("--trigger-on-market-maker-quote", action="store_true", help="Allow market-maker quote to trigger the stop-loss.")
    stoploss_set.add_argument("--order-type", choices=ORDER_TYPE_CHOICES, default="sell", help="Order side after trigger. Default: sell.")
    stoploss_set.add_argument("--order-price", metavar="VALUE", required=True, type=float, help="Order price or offset, interpreted with --order-price-type.")
    stoploss_set.add_argument(
        "--order-price-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --order-price. Use SEK or %%. Default: SEK.",
    )
    stoploss_set.add_argument("--volume", metavar="QTY", required=True, type=float, help="Number of shares/contracts to include in the triggered order.")
    stoploss_set.add_argument(
        "--order-valid-days",
        metavar="DAYS",
        default=STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
        type=int,
        help=f"Triggered order validity in days. Default: {STOPLOSS_ORDER_VALID_DAYS_DEFAULT}.",
    )
    stoploss_set.add_argument("--short-selling-allowed", action="store_true", help="Allow short selling for the triggered order.")
    stoploss_set.add_argument("--confirm", action="store_true", help="Actually place the stop-loss. Omit for dry-run.")
    stoploss_set.set_defaults(func=cmd_stoploss_set)

    stoploss_edit = stoploss_subparsers.add_parser(
        "edit",
        formatter_class=HELP_FORMATTER,
        help="Update an existing stop-loss (replace workflow).",
        description=textwrap.dedent(
            """\
            Update an existing stop-loss by deleting the old one and placing a replacement.

            This command uses the same trigger/order fields as `stoploss set`, plus --stop-loss-id.
            Without --confirm, it prints a dry-run summary.
            """
        ),
    )
    add_common_auth(stoploss_edit)
    stoploss_edit.add_argument("--stop-loss-id", metavar="ID", required=True, help="Existing stop-loss id to update.")
    stoploss_edit.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id that owns the stop-loss.")
    stoploss_edit.add_argument("--order-book-id", metavar="ID", required=True, help="Avanza order book id for the instrument.")
    stoploss_edit.add_argument("--parent-stop-loss-id", metavar="ID", default="0", help="Parent stop-loss id. Default: 0.")
    stoploss_edit.add_argument("--trigger-type", choices=TRIGGER_TYPE_CHOICES, required=True, help="Stop-loss trigger behavior.")
    stoploss_edit.add_argument("--trigger-value", metavar="VALUE", required=True, type=float, help="Trigger value, interpreted with --trigger-value-type.")
    stoploss_edit.add_argument(
        "--trigger-value-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --trigger-value. Use SEK or %%. Default: SEK.",
    )
    stoploss_edit.add_argument(
        "--valid-until",
        metavar="YYYY-MM-DD",
        default=max_valid_until_date().isoformat(),
        type=parse_date,
        help=f"Last date the trigger remains valid. Default: max allowed ({VALID_UNTIL_MAX_DAYS} days from today).",
    )
    stoploss_edit.add_argument("--trigger-on-market-maker-quote", action="store_true", help="Allow market-maker quote to trigger the stop-loss.")
    stoploss_edit.add_argument("--order-type", choices=ORDER_TYPE_CHOICES, default="sell", help="Order side after trigger. Default: sell.")
    stoploss_edit.add_argument("--order-price", metavar="VALUE", required=True, type=float, help="Order price or offset, interpreted with --order-price-type.")
    stoploss_edit.add_argument(
        "--order-price-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --order-price. Use SEK or %%. Default: SEK.",
    )
    stoploss_edit.add_argument("--volume", metavar="QTY", required=True, type=float, help="Number of shares/contracts to include in the triggered order.")
    stoploss_edit.add_argument(
        "--order-valid-days",
        metavar="DAYS",
        default=STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
        type=int,
        help=f"Triggered order validity in days. Default: {STOPLOSS_ORDER_VALID_DAYS_DEFAULT}.",
    )
    stoploss_edit.add_argument("--short-selling-allowed", action="store_true", help="Allow short selling for the triggered order.")
    stoploss_edit.add_argument("--confirm", action="store_true", help="Actually update the stop-loss (delete + place replacement).")
    stoploss_edit.set_defaults(func=cmd_stoploss_edit)

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
