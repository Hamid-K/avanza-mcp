"""Kernel integration for durable stop-loss strategy metadata."""

from typing import Any, Iterable

from avanza_mcp import config
from avanza_mcp.records import stop_loss_mcp_dict
from avanza_mcp.stoploss_strategy_registry import StopLossStrategyRegistry


class CoreStopLossStrategyMixin:
    """Expose registry operations to snapshots and trading entrypoints."""

    def init_stoploss_strategy_state(self) -> None:
        self.stoploss_strategy_registry_path = config.STOPLOSS_STRATEGY_REGISTRY_FILE
        self.stoploss_strategy_registry = StopLossStrategyRegistry(
            self.stoploss_strategy_registry_path
        )

    def stoploss_mcp_row(self, item: dict[str, Any]) -> dict[str, Any]:
        return self.enrich_stoploss_strategy_row(stop_loss_mcp_dict(item))

    def enrich_stoploss_strategy_row(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        return self.stoploss_strategy_registry.enrich(row)

    def stoploss_strategy_summary(
        self,
        account_id: str,
        rows: Iterable[dict[str, Any]],
        *,
        prune_missing: bool = False,
    ) -> dict[str, Any]:
        return self.stoploss_strategy_registry.reconcile_account(
            account_id,
            rows,
            prune_missing=prune_missing,
        )

    def persist_stoploss_strategy_from_preview(
        self,
        preview: dict[str, Any],
        readback_row: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        return self.stoploss_strategy_registry.register_from_preview(
            preview,
            readback_row,
            tenant_session_id=self.active_session_id,
            source=source,
        )

    def ensure_stoploss_strategy_registry_writable(self) -> None:
        self.stoploss_strategy_registry.ensure_writable()

    def persist_existing_stoploss_strategies(
        self,
        candidates: Iterable[dict[str, Any]],
        *,
        source: str,
    ) -> list[dict[str, Any]]:
        return self.stoploss_strategy_registry.register_many_existing(
            candidates,
            tenant_session_id=self.active_session_id,
            source=source,
        )

    def remove_stoploss_strategy(
        self,
        account_id: str,
        stop_loss_id: str,
    ) -> bool:
        return self.stoploss_strategy_registry.remove(account_id, stop_loss_id)
