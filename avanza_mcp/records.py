"""Payload normalization: search hits, transactions, orders, stop-losses, movers."""

from datetime import date
from typing import Any

from avanza.constants import TransactionsDetailsType

from avanza_mcp import utils
from avanza_mcp.market_data import (
    display_symbol,
    infer_currency_from_metadata,
    iso_from_any_timestamp,
    market_quote_first_text,
    merged_orderbook_metadata,
    normalize_symbol_candidate,
    orderbook_quote_row,
    payload_to_json_safe,
    trailing_parenthesized_symbol,
)
from avanza_mcp.rendering import (
    amount,
    enum_value,
    formatted_typed_value,
    normalize_order_side,
    open_order_account_id,
    open_order_items,
    open_order_order_book_id,
    open_order_row,
    open_order_side_value,
    open_order_stock_name,
    position_state_row,
    quantity_text,
    render_message,
    render_table,
    side_badge,
    stop_loss_row,
)
from avanza_mcp.utils import nested_value, value_number

def stoploss_instrument_metadata(
    avanza: Any,
    order_book_id: str,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    order_book_id = str(order_book_id or "").strip()
    metadata = merged_orderbook_metadata(base or {}, {"orderbook_id": order_book_id})
    if not order_book_id:
        return metadata

    try:
        market_payload = payload_to_json_safe(avanza.get_market_data(order_book_id))
    except Exception:
        market_payload = {}
    if isinstance(market_payload, dict):
        quote = orderbook_quote_row(
            order_book_id,
            market_payload,
            fallback_name=str(metadata.get("name") or ""),
            fallback_ticker=str(metadata.get("ticker") or ""),
            fallback_market=str(metadata.get("market") or ""),
            fallback_currency=str(metadata.get("currency") or ""),
        )
        metadata = merged_orderbook_metadata(
            metadata,
            {
                "name": quote.get("name"),
                "ticker": quote.get("ticker"),
                "market": quote.get("market"),
                "currency": quote.get("currency"),
                "country_code": market_quote_first_text(
                    market_payload,
                    (("countryCode",), ("country",), ("flagCode",), ("orderbook", "countryCode")),
                ),
                "instrument_type": market_quote_first_text(
                    market_payload,
                    (("instrumentType",), ("orderbook", "instrumentType"), ("instrument", "instrumentType")),
                ),
            },
        )

    missing_currency = not str(metadata.get("currency") or "").strip()
    if missing_currency:
        try:
            hits = flattened_search_hits(avanza.search_for_stock(order_book_id, 15))
            rows = normalized_search_rows(hits, query=order_book_id)
            match = next((row for row in rows if str(row.get("orderbook_id") or "") == order_book_id), None)
            if not match and rows:
                match = rows[0]
            if isinstance(match, dict):
                metadata = merged_orderbook_metadata(
                    metadata,
                    {
                        "name": match.get("name"),
                        "ticker": match.get("ticker"),
                        "display_symbol": match.get("display_symbol"),
                        "market": match.get("market_place"),
                        "currency": match.get("currency"),
                        "country_code": match.get("country"),
                        "instrument_type": match.get("instrument_type"),
                    },
                )
        except Exception:
            pass
    return merged_orderbook_metadata(metadata, {})


def to_plain_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return dumped
    if hasattr(value, "dict"):
        try:
            dumped = value.dict()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return dumped
    return {}


def normalize_search_results_payload(results: Any) -> Any:
    if isinstance(results, list):
        normalized: list[Any] = []
        for item in results:
            if isinstance(item, dict):
                normalized.append(item)
                continue
            as_dict = to_plain_dict(item)
            if as_dict:
                normalized.append(as_dict)
        return normalized
    if isinstance(results, dict):
        return results
    as_dict = to_plain_dict(results)
    if as_dict:
        return as_dict
    return []


def flattened_search_hits(results: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    normalized = normalize_search_results_payload(results)

    if isinstance(normalized, list):
        source = normalized
    elif isinstance(normalized, dict):
        if "hits" in normalized:
            source = normalized.get("hits") or []
        elif "topHits" in normalized:
            source = normalized.get("topHits") or []
        elif "results" in normalized:
            source = normalized.get("results") or []
        elif any(isinstance(value, list) for value in normalized.values()):
            source = []
            for group_name, group_items in normalized.items():
                if not isinstance(group_items, list):
                    continue
                for raw_item in group_items:
                    item = to_plain_dict(raw_item)
                    if not item:
                        continue
                    if group_name and not item.get("instrumentType"):
                        item["instrumentType"] = str(group_name).rstrip("s").upper()
                    source.append(item)
        else:
            source = [normalized]
    else:
        return []

    for raw_group in source:
        hit_group = to_plain_dict(raw_group)
        if not hit_group:
            continue
        group_type = hit_group.get("instrumentType", "")
        top_hits = hit_group.get("topHits") or []
        if isinstance(top_hits, list) and top_hits:
            for raw_hit in top_hits:
                hit = to_plain_dict(raw_hit)
                if not hit:
                    continue
                row = dict(hit)
                if group_type:
                    row.setdefault("instrumentType", group_type)
                rows.append(row)
            continue

        if {"title", "urlSlugName", "marketPlaceName"} & set(hit_group.keys()):
            row = dict(hit_group)
            row.setdefault("name", row.get("title") or row.get("name") or "")
            if not row.get("tickerSymbol"):
                slug = str(row.get("urlSlugName", ""))
                if slug:
                    row["tickerSymbol"] = slug.split("-")[-1].upper()
            price = row.get("price")
            if isinstance(price, dict):
                row.setdefault("lastPrice", price.get("last"))
                row.setdefault("buy", price.get("buy"))
                row.setdefault("sell", price.get("sell"))
            rows.append(row)
            continue

        row = dict(hit_group)
        if group_type:
            row.setdefault("instrumentType", group_type)
        rows.append(row)
    return rows


def search_hit_order_book_id(hit: dict[str, Any]) -> str:
    orderbook = hit.get("orderbook") if isinstance(hit.get("orderbook"), dict) else {}
    return str(
        hit.get("id")
        or hit.get("orderbookId")
        or hit.get("orderBookId")
        or orderbook.get("id")
        or ""
    )


def search_hit_name(hit: dict[str, Any]) -> str:
    return str(hit.get("name") or hit.get("title") or hit.get("shortName") or hit.get("description") or "").strip()


def search_hit_ticker(hit: dict[str, Any]) -> str:
    name = search_hit_name(hit)
    paren_symbol = trailing_parenthesized_symbol(name)
    if paren_symbol:
        return paren_symbol
    for key in ("tickerSymbol", "symbol"):
        ticker = normalize_symbol_candidate(str(hit.get(key) or "").strip())
        if ticker:
            return ticker
    slug = str(hit.get("urlSlugName") or hit.get("slug") or "").strip()
    if slug:
        tail = normalize_symbol_candidate(slug.split("-")[-1])
        if tail:
            return tail
    return ""


def search_hit_country(hit: dict[str, Any]) -> str:
    return str(hit.get("country") or hit.get("countryCode") or hit.get("flagCode") or "").strip().upper()


def search_hit_tradeable_flags(hit: dict[str, Any]) -> tuple[bool | None, bool | None, bool | None]:
    tradeable = hit.get("tradeable")
    if tradeable is None:
        tradeable = hit.get("tradable")
    buyable = hit.get("buyable")
    sellable = hit.get("sellable")
    return (
        bool(tradeable) if isinstance(tradeable, bool) else None,
        bool(buyable) if isinstance(buyable, bool) else None,
        bool(sellable) if isinstance(sellable, bool) else None,
    )


def search_hit_price(hit: dict[str, Any], key: str) -> float | None:
    direct = utils.scalar_number(hit.get(key))
    if direct is not None:
        return direct
    price = hit.get("price")
    if isinstance(price, dict):
        return utils.scalar_number(price.get(key))
    return None


def normalized_search_rows(hits: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
    query_upper = str(query or "").strip().upper()
    rows: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        order_book_id = search_hit_order_book_id(hit)
        name = search_hit_name(hit)
        ticker = search_hit_ticker(hit)
        market_place = str(hit.get("marketPlaceName") or hit.get("market_place") or "").strip()
        country = search_hit_country(hit)
        currency = str(hit.get("currency") or "").strip().upper() or (infer_currency_from_metadata({"country": country, "market": market_place}) or "")
        instrument_type = str(hit.get("instrumentType") or hit.get("subType") or "").strip().upper()
        tradeable, buyable, sellable = search_hit_tradeable_flags(hit)
        last_price = search_hit_price(hit, "lastPrice") or search_hit_price(hit, "last")
        bid = search_hit_price(hit, "buy")
        ask = search_hit_price(hit, "sell")
        if last_price is not None and bid is not None and ask is not None:
            reference = (bid + ask) / 2.0
            if reference > 0:
                ratio = last_price / reference
                if 90.0 <= ratio <= 110.0:
                    last_price = last_price / 100.0
        spread_absolute = (ask - bid) if ask is not None and bid is not None else None
        spread_percent = ((spread_absolute / bid) * 100.0) if spread_absolute is not None and bid not in (None, 0) else None
        ticker_or_none = ticker or None
        rows.append(
            {
                "name": name or order_book_id,
                "ticker": ticker_or_none,
                "symbol": ticker_or_none,
                "display_symbol": display_symbol(ticker_or_none, name),
                "orderbook_id": order_book_id,
                "market_place": market_place or None,
                "country": country or None,
                "currency": currency or None,
                "instrument_type": instrument_type or None,
                "tradeable": tradeable,
                "buyable": buyable,
                "sellable": sellable,
                "last_price": last_price,
                "bid": bid,
                "ask": ask,
                "spread_absolute": spread_absolute,
                "spread_percent": spread_percent,
                "isin": str(hit.get("isin") or ""),
                "_source": hit,
            }
        )

    def sort_key(row: dict[str, Any]) -> tuple[int, int, int, str, str]:
        ticker = str(row.get("ticker") or "").upper()
        name = str(row.get("name") or "").upper()
        exact = 0
        starts = 1
        contains = 2
        if query_upper:
            if ticker == query_upper or name == query_upper:
                exact = 0
                starts = 0
                contains = 0
            elif ticker.startswith(query_upper) or name.startswith(query_upper):
                exact = 1
                starts = 0
                contains = 0
            elif query_upper in ticker or query_upper in name:
                exact = 2
                starts = 1
                contains = 0
            else:
                exact = 3
                starts = 1
                contains = 1
        tradeable = row.get("tradeable")
        tradeable_rank = 0 if tradeable is True else 1 if tradeable is None else 2
        return (exact, starts, contains, f"{tradeable_rank}", f"{name}|{ticker}")

    rows.sort(key=sort_key)
    return rows


def search_rows_with_market_data(
    avanza: Any,
    rows: list[dict[str, Any]],
    include_market_data: bool = True,
) -> list[dict[str, Any]]:
    if not include_market_data:
        return rows
    for row in rows:
        if row.get("last_price") is not None and row.get("bid") is not None and row.get("ask") is not None:
            continue
        order_book_id = str(row.get("orderbook_id") or "")
        if not order_book_id:
            continue
        try:
            market_data = payload_to_json_safe(avanza.get_market_data(order_book_id))
        except Exception:
            continue
        if not isinstance(market_data, dict):
            continue
        quote = market_data.get("quote")
        if not isinstance(quote, dict):
            quote = market_data
        if row.get("last_price") is None:
            row["last_price"] = utils.scalar_number(quote.get("last"))
        if row.get("bid") is None:
            row["bid"] = utils.scalar_number(quote.get("buy"))
        if row.get("ask") is None:
            row["ask"] = utils.scalar_number(quote.get("sell"))
        bid = row.get("bid")
        ask = row.get("ask")
        last_price = utils.scalar_number(row.get("last_price"))
        if last_price is not None and bid is not None and ask is not None:
            reference = (bid + ask) / 2.0
            if reference > 0:
                ratio = last_price / reference
                # Some Avanza search payloads return last in minor units (100x).
                if 90.0 <= ratio <= 110.0:
                    row["last_price"] = last_price / 100.0
        if ask is not None and bid is not None:
            row["spread_absolute"] = ask - bid
            row["spread_percent"] = ((ask - bid) / bid * 100.0) if bid not in (None, 0) else None
    return rows


def search_hit_label(hit: dict[str, Any]) -> str:
    name = str(hit.get("name") or hit.get("shortName") or "").strip()
    ticker = str(hit.get("tickerSymbol") or hit.get("symbol") or "").strip()
    instrument_type = str(hit.get("instrumentType") or "").strip()
    currency = str(hit.get("currency") or "").strip()
    order_book_id = search_hit_order_book_id(hit)

    parts = [name or order_book_id]
    meta = [value for value in (ticker, instrument_type, currency) if value]
    if meta:
        parts.append(f"({' / '.join(meta)})")
    return " ".join(parts)


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
                hit.get("isin", ""),
                hit.get("currency", ""),
            )
        )

    render_table(
        "Search Results",
        ["Name", "Ticker", "Type", "ISIN", "Currency"],
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
            "Status",
            "Account",
            "Stock",
            "Trigger",
            "Order",
            "Valid Until",
        ],
        rows,
    )


def render_orders(orders: Any) -> None:
    items = open_order_items(orders)
    rows = [open_order_row(item) for item in items if isinstance(item, dict)]
    if not rows:
        render_message("Open Orders", ["No open orders found."])
        return
    render_table(
        "Open Orders",
        ["Kind", "Status", "Stock", "Side", "Volume", "Price", "Valid Until"],
        rows,
    )


def parse_transaction_types(values: Any) -> list[TransactionsDetailsType]:
    if values in (None, "", []):
        return [TransactionsDetailsType.BUY, TransactionsDetailsType.SELL]

    raw_values: list[Any]
    if isinstance(values, str):
        raw_values = [chunk.strip() for chunk in values.split(",") if chunk.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw_values = [value for value in values if str(value).strip()]
    else:
        raise ValueError("transaction types must be a comma-separated string or a list.")

    if not raw_values:
        return [TransactionsDetailsType.BUY, TransactionsDetailsType.SELL]

    parsed: list[TransactionsDetailsType] = []
    for value in raw_values:
        if isinstance(value, TransactionsDetailsType):
            parsed.append(value)
        else:
            parsed.append(enum_value(TransactionsDetailsType, str(value).strip()))
    return parsed


def transactions_items(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if hasattr(payload, "model_dump"):
        try:
            payload = payload.model_dump()
        except Exception:
            pass
    if isinstance(payload, dict):
        raw_items = payload.get("transactions") or payload.get("items") or []
        first_date = str(payload.get("firstTransactionDate") or "") or None
    elif isinstance(payload, list):
        raw_items = payload
        first_date = None
    else:
        raw_items = []
        first_date = None
    return [item for item in raw_items if isinstance(item, dict)], first_date


def transaction_account_id(item: dict[str, Any]) -> str:
    account = item.get("account")
    account_data = account if isinstance(account, dict) else {}
    return str(
        account_data.get("id")
        or account_data.get("accountId")
        or item.get("accountId")
        or item.get("account_id")
        or item.get("accountNumber")
        or ""
    )


def transaction_account_name(item: dict[str, Any]) -> str:
    account = item.get("account")
    if isinstance(account, dict):
        return str(account.get("name") or account.get("accountName") or "")
    if isinstance(account, str):
        return account
    return str(item.get("accountName") or item.get("account_name") or "")


def transaction_matches_filters(
    item: dict[str, Any],
    account_id: str | None,
    executed_only: bool,
    account_name: str | None = None,
) -> bool:
    if account_id:
        item_account_id = transaction_account_id(item)
        if item_account_id and item_account_id != account_id:
            return False
        item_account_name = transaction_account_name(item)
        if not item_account_id and item_account_name and account_name:
            if compact_filter_text(item_account_name) != compact_filter_text(account_name):
                return False
    if not executed_only:
        return True
    return str(item.get("type", "")).upper() in {"BUY", "SELL"}


def transaction_history_row(item: dict[str, Any]) -> tuple[Any, ...]:
    orderbook = item.get("orderbook") if isinstance(item.get("orderbook"), dict) else {}
    side = str(item.get("type", "")).upper()
    side_cell = side_badge(side.lower()) if side in {"BUY", "SELL"} else side
    return (
        str(item.get("tradeDate") or item.get("date") or ""),
        str(nested_value(item, "account", "name") or ""),
        str(item.get("instrumentName") or orderbook.get("name") or item.get("description") or ""),
        side_cell,
        amount(item, "volume"),
        amount(item, "priceInTransactionCurrency") or amount(item, "priceInTradedCurrency"),
        amount(item, "amount"),
        amount(item, "commission"),
        amount(item, "result"),
    )


def transaction_order_history_row(item: dict[str, Any]) -> tuple[Any, ...]:
    orderbook = item.get("orderbook") if isinstance(item.get("orderbook"), dict) else {}
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    return (
        str(item.get("tradeDate", "")),
        side_badge(str(item.get("type", "")).lower()),
        str(item.get("instrumentName", "") or orderbook.get("name", "")),
        quantity_text(nested_value(item, "volume", "value")),
        amount(item, "priceInTransactionCurrency"),
        amount(item, "amount"),
        amount(item, "result"),
        str(account.get("name", "")),
    )


def transaction_activity_row(item: dict[str, Any]) -> tuple[Any, ...]:
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    return (
        str(item.get("tradeDate", "")),
        str(account.get("name", "")),
        str(item.get("type", "")),
        str(item.get("instrumentName", "") or item.get("description", "")),
        quantity_text(nested_value(item, "volume", "value")),
        amount(item, "priceInTransactionCurrency"),
        amount(item, "amount"),
        amount(item, "result"),
        str(item.get("isin", "")),
    )


def transaction_history_dict_row(item: dict[str, Any]) -> dict[str, Any]:
    orderbook = item.get("orderbook") if isinstance(item.get("orderbook"), dict) else {}
    result = amount(item, "result")
    return {
        "Trade Date": str(item.get("tradeDate") or item.get("date") or ""),
        "Account": transaction_account_name(item),
        "Stock": str(item.get("instrumentName") or orderbook.get("name") or item.get("description") or ""),
        "Type": str(item.get("type", "")).upper(),
        "Volume": amount(item, "volume"),
        "Price": amount(item, "priceInTransactionCurrency") or amount(item, "priceInTradedCurrency"),
        "Amount": amount(item, "amount"),
        "Commission": amount(item, "commission"),
        "Result": result,
        "P/L SEK": result,
        "ISIN": str(item.get("isin") or orderbook.get("isin") or ""),
        "Description": str(item.get("description") or ""),
    }


def compact_filter_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def name_matches_filter(value: Any, expected: Any) -> bool:
    expected_text = compact_filter_text(expected)
    if not expected_text:
        return True
    return expected_text in compact_filter_text(value)


def status_matches_filter(value: Any, expected: Any) -> bool:
    expected_text = str(expected or "").strip().upper()
    if not expected_text:
        return True
    return str(value or "").strip().upper() == expected_text


def side_matches_filter(value: Any, expected: Any) -> bool:
    expected_side = normalize_order_side(expected)
    if not expected_side:
        return True
    return normalize_order_side(value) == expected_side


def stop_loss_account_id(item: dict[str, Any]) -> str:
    return str(nested_value(item, "account", "id") or item.get("accountId") or item.get("account_id") or "")


def stop_loss_account_name(item: dict[str, Any]) -> str:
    return str(nested_value(item, "account", "name") or item.get("accountName") or "")


def stop_loss_order_book_id(item: dict[str, Any]) -> str:
    return str(nested_value(item, "orderbook", "id") or item.get("orderBookId") or item.get("order_book_id") or "")


def stop_loss_stock_name(item: dict[str, Any]) -> str:
    return str(nested_value(item, "orderbook", "name") or item.get("instrumentName") or item.get("name") or "")


def stop_loss_side(item: dict[str, Any]) -> str:
    return normalize_order_side(nested_value(item, "order", "type") or item.get("side") or item.get("orderType"))


def stop_loss_volume(item: dict[str, Any]) -> float:
    value = nested_value(item, "order", "volume") or item.get("volume")
    parsed = utils.scalar_number(value)
    return float(parsed or 0.0)


def position_volume(item: dict[str, Any]) -> float:
    parsed = value_number(item, "volume")
    return float(parsed or 0.0)


def stop_loss_trigger_percent(item: dict[str, Any]) -> float | None:
    value_type = str(nested_value(item, "trigger", "valueType") or "").strip().lower()
    if value_type not in {"percentage", "percent", "%"}:
        return None
    return utils.scalar_number(nested_value(item, "trigger", "value"))


def transaction_order_book_id(item: dict[str, Any]) -> str:
    return str(
        nested_value(item, "orderbook", "id")
        or item.get("orderBookId")
        or item.get("order_book_id")
        or nested_value(item, "instrument", "orderbook", "id")
        or ""
    )


def transaction_stock_name(item: dict[str, Any]) -> str:
    orderbook = item.get("orderbook") if isinstance(item.get("orderbook"), dict) else {}
    return str(item.get("instrumentName") or orderbook.get("name") or item.get("description") or "")


def transaction_trade_date(item: dict[str, Any]) -> str:
    return str(item.get("tradeDate") or item.get("date") or "")


def transaction_side(item: dict[str, Any]) -> str:
    return normalize_order_side(item.get("type"))


def transaction_volume(item: dict[str, Any]) -> float:
    parsed = value_number(item, "volume")
    return float(parsed or 0.0)


def transaction_price(item: dict[str, Any]) -> float | None:
    return value_number(item, "priceInTransactionCurrency") or value_number(item, "priceInTradedCurrency")


def transaction_amount(item: dict[str, Any]) -> float | None:
    return value_number(item, "amount")


def instrument_is_eth_like(name: Any, orderbook_id: Any = "") -> bool:
    text = compact_filter_text(f"{name} {orderbook_id}")
    return any(token in text for token in ("ethereum", "ether", "ethusd", "etheur", "eth xbt"))


def position_mcp_dict(item: dict[str, Any], realtime_status_value: str = "") -> dict[str, Any]:
    row = list(position_state_row(item, realtime_status_value or None))
    if realtime_status_value:
        row[-1] = realtime_status_value
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    return {
        "Stock": row[0],
        "Order Book ID": row[1],
        "Volume": row[2],
        "Value": row[3],
        "Avg Price": row[4],
        "Day %": row[5],
        "Day SEK": row[6],
        "Profit %": row[7],
        "Profit": row[8],
        "Real-time": row[9],
        "account_id": str(account.get("id") or ""),
        "account_name": str(account.get("name") or ""),
        "orderbook_id": row[1],
        "stock": row[0],
        "volume": position_volume(item),
    }


def stop_loss_mcp_dict(item: dict[str, Any]) -> dict[str, Any]:
    trigger = item.get("trigger") if isinstance(item.get("trigger"), dict) else {}
    order = item.get("order") if isinstance(item.get("order"), dict) else {}
    account_id = stop_loss_account_id(item)
    account_name = stop_loss_account_name(item)
    stock = stop_loss_stock_name(item)
    orderbook_id = stop_loss_order_book_id(item)
    side = stop_loss_side(item)
    trigger_type = str(trigger.get("type", "") or "")
    trigger_value = trigger.get("value", "")
    trigger_value_type = str(trigger.get("valueType", "") or "")
    order_price = order.get("price", "")
    order_price_type = str(order.get("priceType", "") or "")
    valid_until = str(trigger.get("validUntil", "") or "")
    return {
        "Stop Loss ID": str(item.get("id", "") or ""),
        "Status": str(item.get("status", "") or ""),
        "Account": account_name,
        "Account ID": account_id,
        "Stock": stock,
        "Order Book ID": orderbook_id,
        "Trigger": f"{trigger_type} {formatted_typed_value(trigger_value, trigger_value_type)}".strip(),
        "Order": f"{str(order.get('type', '') or '')} {order.get('volume', '')} @ {formatted_typed_value(order_price, order_price_type)}".strip(),
        "Valid Until": valid_until,
        "stop_loss_id": str(item.get("id", "") or ""),
        "status": str(item.get("status", "") or ""),
        "account_id": account_id,
        "account_name": account_name,
        "stock": stock,
        "orderbook_id": orderbook_id,
        "side": side,
        "volume": stop_loss_volume(item),
        "trigger_type": trigger_type,
        "trigger_value": utils.scalar_number(trigger_value),
        "trigger_value_type": trigger_value_type,
        "order_price": utils.scalar_number(order_price),
        "order_price_type": order_price_type,
        "valid_until": valid_until,
        "order_valid_days": order.get("validDays") or order.get("valid_days"),
    }


def stop_loss_matches_filters(
    item: dict[str, Any],
    *,
    account_id: str | None = None,
    orderbook_id: str | None = None,
    instrument_name: str | None = None,
    side: str | None = None,
    status: str | None = None,
) -> bool:
    if account_id and stop_loss_account_id(item) != account_id:
        return False
    if orderbook_id and stop_loss_order_book_id(item) != str(orderbook_id):
        return False
    if not name_matches_filter(stop_loss_stock_name(item), instrument_name):
        return False
    if not side_matches_filter(stop_loss_side(item), side):
        return False
    if not status_matches_filter(item.get("status"), status):
        return False
    return True


def open_order_matches_filters(
    item: dict[str, Any],
    *,
    account_id: str | None = None,
    orderbook_id: str | None = None,
    instrument_name: str | None = None,
    side: str | None = None,
    status: str | None = None,
) -> bool:
    if account_id and open_order_account_id(item) != account_id:
        return False
    if orderbook_id and open_order_order_book_id(item) != str(orderbook_id):
        return False
    if not name_matches_filter(open_order_stock_name(item), instrument_name):
        return False
    if not side_matches_filter(open_order_side_value(item), side):
        return False
    if not status_matches_filter(item.get("status"), status):
        return False
    return True


def transaction_matches_instrument_filters(
    item: dict[str, Any],
    *,
    account_id: str | None = None,
    account_name: str | None = None,
    orderbook_id: str | None = None,
    instrument_name: str | None = None,
    side: str | None = None,
    status: str | None = None,
    executed_only: bool = True,
) -> bool:
    if not transaction_matches_filters(item, account_id, executed_only, account_name=account_name):
        return False
    if orderbook_id and transaction_order_book_id(item) != str(orderbook_id):
        return False
    if not name_matches_filter(transaction_stock_name(item), instrument_name):
        return False
    if not side_matches_filter(transaction_side(item), side):
        return False
    if status:
        candidate_status = str(item.get("status") or item.get("orderStatus") or "").strip().upper()
        if candidate_status != str(status).strip().upper():
            return False
    return True


def summarize_stop_protection(position: dict[str, Any] | None, stoplosses: list[dict[str, Any]]) -> dict[str, Any]:
    holding_volume = position_volume(position) if isinstance(position, dict) else 0.0
    active_sell_volume = sum(
        stop_loss_volume(item)
        for item in stoplosses
        if str(item.get("status", "")).upper() == "ACTIVE" and stop_loss_side(item) == "SELL"
    )
    active_buy_volume = sum(
        stop_loss_volume(item)
        for item in stoplosses
        if str(item.get("status", "")).upper() == "ACTIVE" and stop_loss_side(item) == "BUY"
    )
    failed_volume = sum(
        stop_loss_volume(item)
        for item in stoplosses
        if str(item.get("status", "")).upper() in {"ERROR", "FAILED", "REJECTED"}
    )
    gap = max(holding_volume - active_sell_volume, 0.0)
    overcoverage = max(active_sell_volume - holding_volume, 0.0)
    return {
        "holding_volume": holding_volume,
        "active_sell_stop_volume": active_sell_volume,
        "active_buy_stop_volume": active_buy_volume,
        "failed_stop_volume": failed_volume,
        "sell_protection_gap": gap,
        "sell_overcoverage": overcoverage,
        "is_fully_sell_protected": holding_volume <= active_sell_volume if holding_volume > 0 else True,
    }


def summarize_sold_transactions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for item in items:
        if transaction_side(item) != "SELL":
            continue
        key = transaction_order_book_id(item) or transaction_stock_name(item)
        if not key:
            continue
        volume = abs(transaction_volume(item))
        price = transaction_price(item)
        bucket = buckets.setdefault(
            key,
            {
                "orderbook_id": transaction_order_book_id(item),
                "stock": transaction_stock_name(item),
                "sold_volume": 0.0,
                "sold_notional": 0.0,
                "sell_count": 0,
                "transactions": [],
            },
        )
        bucket["sold_volume"] += volume
        if price is not None:
            bucket["sold_notional"] += volume * price
        bucket["sell_count"] += 1
        bucket["transactions"].append(transaction_history_dict_row(item))
    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        volume = float(bucket["sold_volume"] or 0.0)
        notional = float(bucket["sold_notional"] or 0.0)
        bucket["avg_sell_price"] = (notional / volume) if volume else None
        rows.append(bucket)
    return sorted(rows, key=lambda row: str(row.get("stock") or row.get("orderbook_id") or ""))


def first_nested_text_for_keys(data: Any, keys: set[str]) -> str:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and value not in (None, ""):
                return str(value)
        for value in data.values():
            found = first_nested_text_for_keys(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = first_nested_text_for_keys(value, keys)
            if found:
                return found
    return ""


def parse_optional_iso_date(value: Any, *, label: str = "date") -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date string.") from exc


def mcp_orderbook_filter(arguments: dict[str, Any]) -> str | None:
    value = arguments.get("orderbook_id", arguments.get("order_book_id"))
    text = str(value or "").strip()
    return text or None


def render_transactions_history(
    payload: Any,
    account_id: str | None = None,
    executed_only: bool = True,
) -> None:
    items, first_date = transactions_items(payload)
    rows = [
        transaction_history_row(item)
        for item in items
        if transaction_matches_filters(item, account_id, executed_only)
    ]
    if not rows:
        render_message("Transaction History", ["No matching transactions found."])
        return

    heading = "Executed Orders History" if executed_only else "Transaction History"
    if first_date:
        heading = f"{heading} (first available: {first_date})"

    render_table(
        heading,
        ["Trade Date", "Account", "Stock", "Type", "Volume", "Price", "Amount", "Commission", "Result"],
        rows,
    )


def movers_rows_from_payload(items: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return rows
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or nested_value(item, "orderbook", "name") or "")
        market = str(item.get("marketPlaceName") or item.get("market") or nested_value(item, "orderbook", "marketPlaceName") or "")
        country = str(item.get("countryCode") or item.get("country") or item.get("flagCode") or "").upper() or None
        currency = str(item.get("currency") or "").upper() or infer_currency_from_metadata({"country": country, "market": market})
        ticker = normalize_symbol_candidate(str(item.get("tickerSymbol") or item.get("symbol") or nested_value(item, "orderbook", "symbol") or ""))
        rows.append(
            {
                "orderbook_id": str(
                    item.get("orderBookId")
                    or item.get("orderbookId")
                    or item.get("id")
                    or nested_value(item, "orderbook", "id")
                    or ""
                ),
                "name": name,
                "ticker": ticker or None,
                "display_symbol": display_symbol(ticker or None, name),
                "market": market or None,
                "country": country,
                "country_code": country,
                "currency": currency,
                "instrument_type": str(item.get("instrumentType") or nested_value(item, "orderbook", "instrumentType") or "") or None,
                "last_price": utils.scalar_number(item.get("lastPrice")) or utils.scalar_number(item.get("last")),
                "one_day_change_percent": (
                    utils.scalar_number(item.get("oneDayChangePercent"))
                    or utils.scalar_number(item.get("changePercent"))
                    or utils.scalar_number(item.get("change"))
                ),
                "total_value_traded": (
                    utils.scalar_number(item.get("totalValueTraded"))
                    or utils.scalar_number(item.get("turnover"))
                    or utils.scalar_number(item.get("valueTraded"))
                ),
                "last_price_updated": (
                    iso_from_any_timestamp(item.get("lastPriceUpdated"))
                    or iso_from_any_timestamp(item.get("updated"))
                    or iso_from_any_timestamp(item.get("timestamp"))
                ),
            }
        )
    return rows


def filter_mover_rows(
    rows: list[dict[str, Any]],
    *,
    min_price: float | None = None,
    min_total_value_traded: float | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        last_price = utils.scalar_number(row.get("last_price"))
        total_value_traded = utils.scalar_number(row.get("total_value_traded"))
        if min_price is not None and (last_price is None or last_price < min_price):
            continue
        if min_total_value_traded is not None and (total_value_traded is None or total_value_traded < min_total_value_traded):
            continue
        filtered.append(row)
    filtered.sort(key=lambda item: utils.scalar_number(item.get("one_day_change_percent")) or 0.0, reverse=True)
    return filtered[: max(1, min(int(limit), 200))]


def index_constituent_row(item: dict[str, Any]) -> dict[str, Any]:
    orderbook_id = str(
        item.get("orderBookId")
        or item.get("orderbookId")
        or item.get("id")
        or nested_value(item, "orderbook", "id")
        or ""
    ).strip()
    name = str(item.get("name") or item.get("title") or nested_value(item, "orderbook", "name") or "").strip()
    ticker = normalize_symbol_candidate(str(item.get("tickerSymbol") or item.get("symbol") or "").strip())
    if not ticker:
        slug = str(item.get("urlSlugName") or "").strip()
        if slug:
            ticker = normalize_symbol_candidate(slug.split("-")[-1])
    market = str(item.get("marketPlaceName") or item.get("market") or nested_value(item, "orderbook", "marketPlaceName") or "").strip()
    country = str(item.get("countryCode") or item.get("flagCode") or item.get("country") or "").strip().upper() or None
    return {
        "name": name,
        "orderbook_id": orderbook_id,
        "country_code": country,
        "country": country,
        "change_percent": (
            utils.scalar_number(item.get("changePercent"))
            or utils.scalar_number(item.get("oneDayChangePercent"))
            or utils.scalar_number(item.get("change"))
        ),
        "ticker": ticker or None,
        "symbol": ticker or None,
        "display_symbol": display_symbol(ticker, name),
        "market": market or None,
        "currency": str(item.get("currency") or nested_value(item, "orderbook", "currency") or "").strip().upper()
        or infer_currency_from_metadata({"country": country, "market": market}),
        "instrument_type": str(item.get("instrumentType") or nested_value(item, "orderbook", "instrumentType") or "").strip().upper() or None,
    }
