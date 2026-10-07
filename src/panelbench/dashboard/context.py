"""Dashboard context type shared by keys, routes, and the app factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from ebus_panel_sim import BESSCommunication

    from panelbench.config_types import BESSConfigYAML
    from panelbench.history import HistoryProvider


async def _noop_modeling_data(
    _hours: int, _config_filename: str | None = None
) -> dict[str, Any] | None:
    return None


@dataclass
class DashboardContext:
    """Clean interface boundary between the dashboard and SimulatorApp."""

    config_dir: Path
    config_filter: str | None
    get_panel_configs: Callable[[], dict[Path, str]]  # path -> serial
    get_panel_ports: Callable[[], dict[str, int]]  # serial -> port
    request_reload: Callable[[], None]
    get_panel_start_errors: Callable[[], dict[str, str]] = lambda: {}  # filename -> error message
    set_config_filter: Callable[[str | None], None] = lambda _: None
    start_panel: Callable[[str], None] = lambda _: None
    stop_panel: Callable[[str], None] = lambda _: None
    restart_panel: Callable[[str], None] = lambda _: None
    get_power_summary: Callable[[], dict[str, Any] | None] = lambda: None
    set_simulation_time: Callable[[str], None] = lambda _: None
    set_time_acceleration: Callable[[float], None] = lambda _: None
    set_grid_online: Callable[[bool], None] = lambda _: None
    set_grid_islandable: Callable[[bool], None] = lambda _: None
    set_bess_link: Callable[[BESSCommunication], None] = lambda _: None
    set_circuit_priority: Callable[[str, str], None] = lambda _id, _pri: None
    set_circuit_relay: Callable[[str, str], None] = lambda _id, _state: None
    apply_bess_config_live: Callable[[str, BESSConfigYAML], bool] = lambda _f, _c: False
    get_modeling_data: Callable[[int, str | None], Awaitable[dict[str, Any] | None]] = (
        _noop_modeling_data
    )
    ha_client: Any = None  # HAClient | None — optional, set when HA API is available
    history_provider: HistoryProvider | None = None
    panel_browser: Any = None  # PanelBrowser | None — mDNS discovery for standalone mode
    # Why the active config could not be opened when the dashboard started, shown
    # until the editor moves to another config.
    load_error: str | None = None

    def edit(self, filename: str | None) -> None:
        """Move the editor to *filename*, or to none.

        The one way the editor changes file, so a load error, which is always about
        the file the editor last failed to open, goes when it does.
        """
        self.config_filter = filename
        self.load_error = None
