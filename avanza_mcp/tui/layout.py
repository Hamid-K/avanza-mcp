"""Pane drag math, DataTable selection helpers, and resizer widgets."""

from typing import Any

from textual import events
from textual.widgets import DataTable, Static

from avanza_mcp.config import (
    MAX_ACTIVE_TRADES_WIDTH,
    MAX_PANE_WEIGHT,
    MAX_TICKET_PANE_WIDTH,
    MIN_ACTIVE_TRADES_WIDTH,
    MIN_PANE_WEIGHT,
    MIN_TICKET_PANE_WIDTH,
    PANE_RESIZE_STEP,
)
from avanza_mcp.utils import clamp

def pane_weights_after_drag(
    start_positions_weight: float,
    start_activity_weight: float,
    delta_rows: int,
) -> tuple[float, float]:
    delta_weight = delta_rows * PANE_RESIZE_STEP
    positions_weight = clamp(start_positions_weight + delta_weight, MIN_PANE_WEIGHT, MAX_PANE_WEIGHT)
    activity_weight = clamp(start_activity_weight - delta_weight, MIN_PANE_WEIGHT, MAX_PANE_WEIGHT)
    return positions_weight, activity_weight


def side_panel_width_after_drag(start_width: int, delta_columns: int) -> int:
    return clamp(start_width - delta_columns, MIN_ACTIVE_TRADES_WIDTH, MAX_ACTIVE_TRADES_WIDTH)


def ticket_pane_width_after_drag(start_width: int, delta_columns: int) -> int:
    return clamp(start_width - delta_columns, MIN_TICKET_PANE_WIDTH, MAX_TICKET_PANE_WIDTH)


def selected_table_row_key(table: DataTable) -> Any | None:
    if table.row_count == 0:
        return None
    try:
        return table.ordered_rows[table.cursor_row].key
    except Exception:
        return None


def restore_table_row_selection(table: DataTable, row_key: Any | None) -> None:
    if row_key is None:
        return
    try:
        table.move_cursor(row=table.get_row_index(row_key), animate=False, scroll=False)
    except Exception:
        return



class PaneResizer(Static):
    def __init__(self) -> None:
        super().__init__("─", id="pane-resizer")

    @staticmethod
    def event_y(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_y if event.screen_y is not None else event.y)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_pane_resize(self.event_y(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_pane_resize(self.event_y(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_pane_resize()
        event.stop()


class ActivityPaneResizer(Static):
    def __init__(self) -> None:
        super().__init__("─", id="activity-resizer")

    @staticmethod
    def event_y(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_y if event.screen_y is not None else event.y)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_activity_resize(self.event_y(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_activity_resize(self.event_y(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_activity_resize()
        event.stop()


class SidePaneResizer(Static):
    def __init__(self) -> None:
        super().__init__("│", id="side-pane-resizer")

    @staticmethod
    def event_x(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_x if event.screen_x is not None else event.x)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_side_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_side_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_side_pane_resize()
        event.stop()


class TicketPaneResizer(Static):
    def __init__(self, ticket: str) -> None:
        super().__init__("│", id=f"{ticket}-ticket-resizer", classes="ticket-resizer")

    @staticmethod
    def event_x(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_x if event.screen_x is not None else event.x)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_ticket_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_ticket_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_ticket_pane_resize()
        event.stop()
