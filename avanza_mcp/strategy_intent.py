"""Strategy-intent guards for MCP stop-loss mutations."""

from typing import Any


BUY_STOPLOSS_STRATEGY_INTENTS = frozenset(
    {
        "CORE_RESTORATION",
        "PARTIAL_PARTICIPATION",
        "DEEP_RESIDUAL",
        "TACTICAL_RECOVERY",
        "OPTIONAL_GROWTH",
        "SPECIAL_APPROVED",
    }
)

SELL_STOPLOSS_STRATEGY_INTENTS = frozenset(
    {
        "TACTICAL_HARVEST",
        "PROFIT_PROTECTION",
        "RISK_OFF_EXIT",
        "THESIS_BREAK_EXIT",
        "SPECIAL_APPROVED",
    }
)

STOPLOSS_STRATEGY_INTENTS = tuple(
    sorted(BUY_STOPLOSS_STRATEGY_INTENTS | SELL_STOPLOSS_STRATEGY_INTENTS)
)


def _normalized_intent(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def validate_mcp_stoploss_strategy_intent(
    arguments: dict[str, Any],
    preview: dict[str, Any],
    *,
    live: bool,
) -> list[str]:
    """Validate and attach auditable strategy intent to an MCP stop request."""

    warnings: list[str] = []
    intent = _normalized_intent(arguments.get("strategy_intent"))
    reason = str(arguments.get("strategy_reason") or "").strip()

    if not intent:
        message = "strategy_intent is required for live stop-loss placement."
        if live:
            raise ValueError(message)
        warnings.append(message)
    elif intent not in STOPLOSS_STRATEGY_INTENTS:
        choices = ", ".join(STOPLOSS_STRATEGY_INTENTS)
        raise ValueError(f"Unsupported strategy_intent {intent!r}; choose one of: {choices}.")

    if not reason:
        message = "strategy_reason is required for live stop-loss placement."
        if live:
            raise ValueError(message)
        warnings.append(message)

    order = preview.get("stop_loss_order_event")
    order = order if isinstance(order, dict) else {}
    side = str(order.get("type") or "").upper()
    if intent:
        allowed = BUY_STOPLOSS_STRATEGY_INTENTS if side == "BUY" else SELL_STOPLOSS_STRATEGY_INTENTS
        if intent not in allowed:
            raise ValueError(f"strategy_intent {intent} is incompatible with stop side {side or 'UNKNOWN'}.")

    trigger = preview.get("stop_loss_trigger")
    trigger = trigger if isinstance(trigger, dict) else {}
    if intent == "DEEP_RESIDUAL":
        trigger_type = str(trigger.get("type") or "").upper()
        trigger_value_type = str(trigger.get("value_type") or "").upper()
        order_price_type = str(order.get("price_type") or "").upper()
        if (
            side != "BUY"
            or trigger_type != "LESS_OR_EQUAL"
            or trigger_value_type != "MONETARY"
            or order_price_type != "MONETARY"
        ):
            raise ValueError(
                "DEEP_RESIDUAL must be a fixed BUY with LESS_OR_EQUAL monetary trigger "
                "and monetary child price; trailing recovery would change its strategy."
            )

    preview["strategy_intent"] = intent or None
    preview["strategy_reason"] = reason or None
    return warnings
