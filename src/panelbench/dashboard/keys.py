"""AppKey instances for dashboard application state.

Using web.AppKey instead of string keys avoids NotAppKeyWarning and
improves type safety. See: https://docs.aiohttp.org/en/stable/web_advanced.html#application-s-config
"""

from __future__ import annotations

from aiohttp import web

from panelbench.dashboard.config_store import ConfigStore
from panelbench.dashboard.context import DashboardContext
from panelbench.dashboard.presets import PresetRegistry
from panelbench.rates.cache import RateCache

APP_KEY_STORE = web.AppKey("store", ConfigStore)
APP_KEY_DASHBOARD_CONTEXT = web.AppKey("dashboard_context", DashboardContext)
APP_KEY_PRESET_REGISTRY = web.AppKey("preset_registry", PresetRegistry)
APP_KEY_PENDING_CLONES: web.AppKey[dict[str, dict[str, object]]] = web.AppKey(
    "pending_clones", dict
)
APP_KEY_RATE_CACHE = web.AppKey("rate_cache", RateCache)
