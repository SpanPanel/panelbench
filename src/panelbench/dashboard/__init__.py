"""Dashboard application for the SPAN panel simulator.

Runs as a standalone aiohttp server on its own port (default 8080)
and provides a web UI for importing, editing, and exporting panel
configurations.
"""

from __future__ import annotations

from pathlib import Path

import aiohttp_jinja2
import jinja2
import yaml
from aiohttp import web

from panelbench.dashboard.config_store import ConfigStore
from panelbench.dashboard.context import DashboardContext
from panelbench.dashboard.keys import (
    APP_KEY_DASHBOARD_CONTEXT,
    APP_KEY_PENDING_CLONES,
    APP_KEY_PRESET_REGISTRY,
    APP_KEY_RATE_CACHE,
    APP_KEY_STORE,
)
from panelbench.dashboard.presets import init_presets
from panelbench.dashboard.routes import refuse_unloadable_edits, setup_routes
from panelbench.rates.cache import RateCache

__all__ = ["DashboardContext", "create_dashboard_app"]


def _view_first_default(context: DashboardContext, store: ConfigStore) -> None:
    """Show the first shipped template, read-only, or nothing when there is none."""
    defaults = sorted(context.config_dir.glob("default_*.yaml"))
    if defaults:
        store.load_from_file(defaults[0])
        context.edit(defaults[0].name)
    else:
        context.edit(None)


def create_dashboard_app(context: DashboardContext) -> web.Application:
    """Create and return the dashboard aiohttp application."""
    app = web.Application(middlewares=[refuse_unloadable_edits])

    store = ConfigStore()

    # Load the active config into the editor/viewer. One the panel would refuse is
    # not opened, so nothing can be saved over it: the dashboard still starts on the
    # first default template, read-only, says why, and lets the user pick or fix a
    # config.
    if context.config_filter:
        config_path = context.config_dir / context.config_filter
        if config_path.exists():
            try:
                store.load_from_file(config_path)
            except (ValueError, TypeError, yaml.YAMLError) as exc:
                load_error = f"{context.config_filter} was not opened: {exc}"
                _view_first_default(context, store)
                context.load_error = load_error
    else:
        # No active config — show first default template (read-only).
        _view_first_default(context, store)

    app[APP_KEY_STORE] = store
    app[APP_KEY_DASHBOARD_CONTEXT] = context
    app[APP_KEY_PRESET_REGISTRY] = init_presets(context.config_dir)
    app[APP_KEY_PENDING_CLONES] = {}
    app[APP_KEY_RATE_CACHE] = RateCache(context.config_dir / "rates" / "rates_cache.yaml")

    template_dir = Path(__file__).parent / "templates"
    env = aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(template_dir)),
    )
    env.globals["static_url"] = "static"

    static_dir = Path(__file__).parent / "static"
    app.router.add_static("/static", static_dir, name="static")

    setup_routes(app)

    return app
