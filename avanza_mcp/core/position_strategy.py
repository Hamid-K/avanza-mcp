"""Kernel integration for durable per-position strategy plans."""

from typing import Any, Iterable

from avanza_mcp import config
from avanza_mcp.position_strategy_registry import PositionStrategyRegistry


class CorePositionStrategyMixin:
    """Expose reviewed position-plan operations to MCP and snapshots."""

    def init_position_strategy_state(self) -> None:
        self.position_strategy_registry_path = config.POSITION_STRATEGY_REGISTRY_FILE
        self.position_strategy_registry = PositionStrategyRegistry(
            self.position_strategy_registry_path
        )

    def position_strategy_summary(
        self,
        account_id: str,
        positions: Iterable[dict[str, Any]],
        stoplosses: Iterable[dict[str, Any]],
        open_orders: Iterable[dict[str, Any]],
        *,
        prune_stale: bool = False,
    ) -> dict[str, Any]:
        return self.position_strategy_registry.reconcile_account(
            account_id,
            positions,
            stoplosses,
            open_orders,
            prune_stale=prune_stale,
        )

    def ensure_position_strategy_registry_writable(self) -> None:
        self.position_strategy_registry.ensure_writable()

    def persist_existing_position_strategies(
        self,
        candidates: Iterable[dict[str, Any]],
        *,
        source: str,
    ) -> list[dict[str, Any]]:
        return self.position_strategy_registry.register_many_existing(
            candidates,
            tenant_session_id=self.active_session_id,
            source=source,
        )
