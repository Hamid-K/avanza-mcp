import io

from avanza_cli import (
    account_display_name,
    account_row,
    account_rows_from_overview,
    account_stats_text,
    amount,
    cash_row,
    cancel_badge,
    changed_position_row,
    change_style,
    create_paper_order,
    create_paper_stop_loss_order,
    default_account,
    flattened_search_hits,
    formatted_typed_value,
    holding_search_options,
    load_paper_session,
    market_clock_text,
    matches_account,
    pane_weights_after_drag,
    paper_orders,
    portfolio_day_summary,
    portfolio_profit_summary,
    quantity_text,
    position_state_row,
    position_holding_label,
    position_row,
    position_trade_action_row,
    position_trade_target,
    lookup_realtime_status,
    mcp_error,
    mcp_success,
    mcp_tool_response,
    open_order_activity_row,
    read_mcp_message,
    realtime_status,
    realtime_status_badge,
    sortable_cell_value,
    save_paper_session,
    side_panel_width_after_drag,
    side_badge,
    stoploss_holding_options,
    stoploss_volume_by_order_book,
    active_paper_order_row,
    stop_loss_activity_row,
    stop_loss_row,
    ticket_pane_width_after_drag,
    trade_action_from_cell,
    trade_action_badge,
    write_mcp_message,
)


def test_amount_formats_value_objects():
    assert amount({"value": {"value": 123.45, "unit": "SEK"}}, "value") == "123.45 SEK"


def test_formatted_typed_value_uses_percent_symbol():
    assert formatted_typed_value(5, "PERCENTAGE") == "5%"
    assert formatted_typed_value(95.5, "MONETARY") == "95.5 SEK"


def test_flattened_search_hits_accepts_avanza_list_shape():
    hits = flattened_search_hits(
        [
            {
                "name": "NewCo AB",
                "tickerSymbol": "NEW",
                "id": "ob-2",
                "currency": "SEK",
            }
        ]
    )

    assert hits[0]["name"] == "NewCo AB"
    assert hits[0]["id"] == "ob-2"


def test_sortable_cell_value_normalizes_human_table_values():
    assert sortable_cell_value("+1.25%") == (2, 1.25)
    assert sortable_cell_value("1,100.00 SEK") == (2, 1100.0)
    assert sortable_cell_value(realtime_status_badge("Yes")) > sortable_cell_value(realtime_status_badge("No"))
    assert sortable_cell_value("Yes") > sortable_cell_value("No")
    assert sortable_cell_value("Unknown") < sortable_cell_value("No")


def test_pane_weights_after_drag_changes_relative_sizes():
    assert pane_weights_after_drag(2, 1, 1) == (2.1, 1)
    assert pane_weights_after_drag(2, 3, -1) == (1.9, 3.1)


def test_side_panel_width_after_drag_changes_width():
    assert side_panel_width_after_drag(42, -5) == 47
    assert side_panel_width_after_drag(42, 20) == 30


def test_ticket_pane_width_after_drag_changes_width():
    assert ticket_pane_width_after_drag(64, -10) == 74
    assert ticket_pane_width_after_drag(64, 20) == 52


def test_market_clock_text_counts_to_open_and_close():
    assert "OMXS closes in 07:30:00" in market_clock_text(__import__("datetime").datetime(2026, 4, 28, 10, 0, 0))
    assert "OMXS opens in 01:00:00" in market_clock_text(__import__("datetime").datetime(2026, 4, 28, 8, 0, 0))


def test_account_display_name_prefers_user_defined_name():
    assert (
        account_display_name(
            {"name": {"defaultName": "ISK", "userDefinedName": "Trading"}}
        )
        == "Trading"
    )


def test_account_row_formats_overview_account():
    row = account_row(
        {
            "id": "acc-1",
            "name": {"defaultName": "ISK", "userDefinedName": None},
            "type": "ISK",
            "totalValue": {"value": 10000, "unit": "SEK"},
            "buyingPower": {"value": 2500, "unit": "SEK"},
            "status": "ACTIVE",
        }
    )

    assert row == ("acc-1", "ISK", "ISK", "10000 SEK", "2500 SEK", "ACTIVE")


def test_account_rows_from_overview_uses_accounts_list():
    accounts = account_rows_from_overview(
        {
            "accounts": [
                {"id": "acc-1", "name": {"defaultName": "ISK"}},
                {"name": {"defaultName": "missing id"}},
                "not-account",
            ]
        }
    )

    assert accounts == [{"id": "acc-1", "name": {"defaultName": "ISK"}}]


def test_default_account_uses_largest_total_value():
    accounts = [
        {"id": "acc-1", "totalValue": {"value": 1000, "unit": "SEK"}},
        {"id": "acc-2", "totalValue": {"value": 5000, "unit": "SEK"}},
    ]

    assert default_account(accounts) == accounts[1]


def test_account_stats_text_includes_profit_summary():
    account = {
        "name": {"defaultName": "ISK", "userDefinedName": "Trading"},
        "type": "ISK",
        "totalValue": {"value": 1100, "unit": "SEK"},
        "buyingPower": {"value": 100, "unit": "SEK"},
        "status": "ACTIVE",
    }
    positions = {
        "withOrderbook": [
            {
                "account": {"id": "acc-1"},
                "value": {"value": 1100, "unit": "SEK"},
                "acquiredValue": {"value": 1000, "unit": "SEK"},
            }
        ],
        "withoutOrderbook": [],
    }

    assert portfolio_profit_summary(positions, "acc-1") == (100.0, 10.0, "SEK")
    summary = account_stats_text(account, positions, "acc-1").plain
    assert "Trading (ISK)" in summary
    assert "Total 1100 SEK" in summary
    assert "Profit +100.00 SEK (+10.00%)" in summary


def test_portfolio_day_summary_uses_daily_absolute_against_account_total():
    account = {"totalValue": {"value": 5000, "unit": "SEK"}}
    positions = {
        "withOrderbook": [
            {
                "account": {"id": "acc-1"},
                "value": {"value": 1100, "unit": "SEK"},
                "lastTradingDayPerformance": {"absolute": {"value": 25, "unit": "SEK"}},
            },
            {
                "account": {"id": "acc-1"},
                "value": {"value": 900, "unit": "SEK"},
                "lastTradingDayPerformance": {"absolute": {"value": -5, "unit": "SEK"}},
            },
        ],
        "withoutOrderbook": [],
    }

    assert portfolio_day_summary(positions, "acc-1", account) == (20.0, 0.4, "SEK")


def test_matches_account_filters_by_nested_account_id():
    item = {"account": {"id": "acc-1"}}

    assert matches_account(item, None)
    assert matches_account(item, "acc-1")
    assert not matches_account(item, "acc-2")


def test_position_row_extracts_nested_position_data():
    row = position_row(
        {
            "id": "pos-1",
            "account": {"name": "ISK", "id": "acc-1"},
            "instrument": {
                "name": "Example AB",
                "isin": "SE0000000001",
                "orderbook": {"id": "ob-1"},
            },
            "volume": {"value": 10, "unit": "st"},
            "value": {"value": 1000, "unit": "SEK"},
            "averageAcquiredPrice": {"value": 90, "unit": "SEK"},
            "acquiredValue": {"value": 900, "unit": "SEK"},
            "lastTradingDayPerformance": {
                "relative": {"value": 1.2, "unit": "%"},
            },
        }
    )

    assert row == (
        "ISK",
        "acc-1",
        "Example AB",
        "ob-1",
        "SE0000000001",
        "10 st",
        "1000 SEK",
        "90 SEK",
        "900 SEK",
        "1.2 %",
    )


def test_position_state_row_includes_day_and_profit_state():
    row = position_state_row(
        {
            "account": {"name": "ISK", "id": "acc-1"},
            "instrument": {
                "name": "Example AB",
                "orderbook": {"id": "ob-1", "quote": {"isRealTime": True}},
            },
            "volume": {"value": 10, "unit": "st"},
            "value": {"value": 1100, "unit": "SEK"},
            "averageAcquiredPrice": {"value": 90, "unit": "SEK"},
            "acquiredValue": {"value": 900, "unit": "SEK"},
            "lastTradingDayPerformance": {
                "relative": {"value": 1.25, "unit": "%"},
                "absolute": {"value": 13.75, "unit": "SEK"},
            },
        }
    )

    assert row[:9] == (
        "Example AB",
        "ob-1",
        "10 st",
        "1100 SEK",
        "90 SEK",
        "+1.25%",
        "+13.75 SEK",
        "+22.22%",
        "+200.00 SEK",
    )
    assert row[9].plain == "●"
    assert str(row[9].style) == "#7fbf8f"


def test_position_trade_action_row_adds_buy_sell_actions():
    item = {
        "instrument": {
            "name": "Example AB",
            "orderbook": {"id": "ob-1", "quote": {"isRealTime": True}},
        },
        "volume": {"value": 10, "unit": "st"},
        "value": {"value": 1100, "unit": "SEK"},
    }

    row = position_trade_action_row(item)

    assert row[:4] == ("Example AB", trade_action_badge("buy"), trade_action_badge("sell"), "ob-1")
    assert trade_action_badge("buy").plain == " B "
    assert trade_action_badge("sell").plain == " S "
    assert trade_action_from_cell(trade_action_badge("buy")) == "buy"
    assert trade_action_from_cell(trade_action_badge("sell")) == "sell"
    assert position_trade_target(item) == {"stock": "Example AB", "order_book_id": "ob-1", "volume": "10"}


def test_quantity_text_drops_float_suffix_for_whole_share_counts():
    assert quantity_text({"value": 20.0, "unit": "st"}) == "20"
    assert quantity_text(20.5) == "20.5"
    assert quantity_text("") == ""


def test_realtime_status_badge_uses_green_or_yellow_dot():
    assert realtime_status_badge("Yes").plain == "●"
    assert str(realtime_status_badge("Yes").style) == "#7fbf8f"
    assert realtime_status_badge("No").plain == "●"
    assert str(realtime_status_badge("No").style) == "#d7ba7d"


def test_realtime_status_reads_known_realtime_and_delayed_flags():
    assert realtime_status({"instrument": {"orderbook": {"quote": {"isRealTime": True}}}}) == "Yes"
    assert realtime_status({"instrument": {"orderbook": {"quote": {"isRealTime": False}}}}) == "No"
    assert realtime_status({"instrument": {"orderbook": {"quote": {"delayed": True}}}}) == "No"
    assert realtime_status({"instrument": {"orderbook": {"quote": {}}}}) == "Unknown"


def test_lookup_realtime_status_uses_instrument_details_when_portfolio_is_unknown():
    class FakeAvanza:
        def get_market_data(self, order_book_id):
            assert order_book_id == "ob-1"
            return {"quote": {}}

        def get_order_book(self, order_book_id):
            assert order_book_id == "ob-1"
            return {"instrumentId": "inst-1", "instrumentType": "STOCK"}

        def get_instrument_details(self, instrument_type, instrument_id):
            assert instrument_id == "inst-1"
            return {"quote": {"isRealTime": False}}

        def get_instrument(self, instrument_type, instrument_id):
            raise AssertionError("summary lookup should not run after details resolve")

    item = {"instrument": {"orderbook": {"id": "ob-1"}}}

    assert lookup_realtime_status(FakeAvanza(), item) == "No"


def test_mcp_tool_response_marks_errors():
    assert mcp_tool_response({"ok": True})["isError"] is False
    assert mcp_tool_response({"ok": False, "error": "nope"})["isError"] is True


def test_mcp_message_framing_round_trip():
    stream = io.BytesIO()
    payload = mcp_success(1, {"tools": []})
    write_mcp_message(stream, payload)
    stream.seek(0)

    assert read_mcp_message(stream) == payload


def test_mcp_error_shape():
    assert mcp_error(9, -32601, "missing") == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32601, "message": "missing"},
    }


def test_changed_position_row_styles_only_changed_numeric_cells():
    previous = (
        "Example AB",
        "ob-1",
        "10 st",
        "1000 SEK",
        "90 SEK",
        "+1.00%",
        "+10.00 SEK",
        "+11.11%",
        "+100.00 SEK",
        "Unknown",
    )
    current = (
        "Example AB",
        "ob-1",
        "10 st",
        "1100 SEK",
        "90 SEK",
        "+1.25%",
        "+13.75 SEK",
        "+22.22%",
        "+200.00 SEK",
        "Unknown",
    )

    row = changed_position_row(current, previous)

    assert row[0] == "Example AB"
    assert row[2] == "10 st"
    assert str(row[3]) == "1100 SEK"
    assert row[3].style
    assert str(row[5]) == "+1.25%"
    assert str(row[5].style) == "#7fbf8f"
    assert str(row[8]) == "+200.00 SEK"
    assert str(row[8].style) == "#7fbf8f"


def test_change_style_is_directional_and_muted():
    assert change_style("+1.25%") == "#7fbf8f"
    assert change_style("-1.25%") == "#d98f8f"
    assert change_style("1100 SEK") == "#d7ba7d"


def test_stoploss_holding_options_show_owned_volume():
    positions = {
        "withOrderbook": [
            {
                "account": {"id": "acc-1"},
                "instrument": {
                    "name": "Example AB",
                    "orderbook": {"id": "ob-1"},
                },
                "volume": {"value": 25, "unit": "st"},
            },
            {
                "account": {"id": "acc-2"},
                "instrument": {
                    "name": "Other AB",
                    "orderbook": {"id": "ob-2"},
                },
                "volume": {"value": 10, "unit": "st"},
            },
        ],
        "withoutOrderbook": [],
    }

    assert position_holding_label(positions["withOrderbook"][0]) == "Example AB - owned 25 st (ob-1)"
    assert stoploss_holding_options(positions, "acc-1") == [
        ("Example AB - owned 25 st (ob-1)", "ob-1")
    ]
    assert stoploss_volume_by_order_book(positions, "acc-1") == {"ob-1": "25"}


def test_holding_search_options_finds_owned_stock_case_insensitively():
    positions = {
        "withOrderbook": [
            {
                "account": {"id": "acc-1"},
                "instrument": {"name": "Broadcom", "orderbook": {"id": "369636"}},
                "volume": {"value": 17, "unit": "st"},
            },
            {
                "account": {"id": "acc-1"},
                "instrument": {"name": "Apple", "orderbook": {"id": "3323"}},
                "volume": {"value": 42, "unit": "st"},
            },
        ],
        "withoutOrderbook": [],
    }

    assert holding_search_options(positions, "acc-1", "broad") == [
        ("Broadcom - owned 17 st (369636)", "369636")
    ]
    assert holding_search_options(positions, "acc-1", "BROAD") == [
        ("Broadcom - owned 17 st (369636)", "369636")
    ]


def test_cash_row_formats_cash_position():
    row = cash_row(
        {
            "id": "cash-1",
            "account": {"name": "ISK", "id": "acc-1"},
            "totalBalance": {"value": 5000, "unit": "SEK"},
        }
    )

    assert row[:7] == ("ISK", "acc-1", "Cash", "", "", "", "5000 SEK")


def test_stop_loss_row_extracts_order_data():
    row = stop_loss_row(
        {
            "id": "sl-1",
            "status": "ACTIVE",
            "account": {"name": "ISK", "id": "acc-1"},
            "orderbook": {"name": "Example AB", "id": "ob-1"},
            "trigger": {
                "type": "FOLLOW_UPWARDS",
                "value": 5,
                "valueType": "PERCENTAGE",
                "validUntil": "2026-05-28",
            },
            "order": {
                "type": "SELL",
                "volume": 10,
                "price": 1,
                "priceType": "PERCENTAGE",
            },
        }
    )

    assert row == (
        "sl-1",
        "ACTIVE",
        "ISK",
        "acc-1",
        "Example AB",
        "ob-1",
        "FOLLOW_UPWARDS 5%",
        "SELL 10 @ 1%",
        "2026-05-28",
    )


def test_stop_loss_activity_row_labels_order_price_type():
    row = stop_loss_activity_row(
        {
            "id": "sl-1",
            "status": "ACTIVE",
            "orderbook": {"name": "Example AB", "id": "ob-1"},
            "trigger": {"type": "FOLLOW_UPWARDS", "value": 5, "valueType": "PERCENTAGE"},
            "order": {"type": "SELL", "volume": 10, "price": 1, "priceType": "PERCENTAGE"},
        }
    )

    assert row[5] == "FOLLOW_UPWARDS 5%"
    assert row[6] == side_badge("sell")
    assert row[8] == "1%"


def test_open_order_activity_row_aligns_with_stop_loss_table_columns():
    row = open_order_activity_row(
        {
            "id": "order-1",
            "status": "ACTIVE",
            "orderbook": {"name": "Example AB", "id": "ob-1"},
            "type": "BUY",
            "volume": 5,
            "price": 123,
            "priceType": "MONETARY",
            "validUntil": "2026-05-28",
        }
    )

    assert len(row) == 11
    assert row[5] == ""
    assert row[6] == side_badge("buy")
    assert row[8] == "123 SEK"
    assert row[10] == cancel_badge()


def test_paper_session_round_trip_and_active_row(tmp_path):
    path = tmp_path / "paper.json"
    session = load_paper_session(path)
    order = create_paper_stop_loss_order(
        {
            "account_id": "acc-1",
            "order_book_id": "ob-1",
            "trigger_value": 5,
            "trigger_value_type": "%",
            "valid_until": "2026-05-28",
            "order_price": 1,
            "order_price_type": "%",
            "volume": 10,
        },
        instrument="Example AB",
    )
    session["orders"].append(order)
    save_paper_session(session, path)

    loaded = load_paper_session(path)

    assert paper_orders(loaded, "acc-1", active_only=True)[0]["id"] == order["id"]
    assert active_paper_order_row(order) == (
        "Paper",
        "Stop-loss",
        order["id"],
        "Example AB",
        "ob-1",
        side_badge("SELL"),
        "10.0",
        "FOLLOW_UPWARDS 5.0%",
        "2026-05-28",
        "ACTIVE",
        cancel_badge(),
    )


def test_paper_regular_order_active_row():
    order = create_paper_order(
        {
            "account_id": "acc-1",
            "order_book_id": "ob-1",
            "order_type": "buy",
            "price": 100,
            "valid_until": "2026-05-28",
            "volume": 10,
            "condition": "fill-and-kill",
        },
        instrument="Example AB",
    )

    assert active_paper_order_row(order) == (
        "Paper",
        "Order",
        order["id"],
        "Example AB",
        "ob-1",
        side_badge("BUY"),
        "10",
        "100.0 SEK FILL_AND_KILL",
        "2026-05-28",
        "ACTIVE",
        cancel_badge(),
    )
