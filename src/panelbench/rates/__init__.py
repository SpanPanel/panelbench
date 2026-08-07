"""ToU rate integration — OpenEI URDB rate plans and cost calculation."""

from panelbench.rates.cache import RateCache
from panelbench.rates.cost_engine import compute_costs
from panelbench.rates.openei import (
    OpenEIError,
    fetch_rate_detail,
    fetch_rate_plans,
    fetch_utilities,
)
from panelbench.rates.resolver import resolve_rate
from panelbench.rates.types import (
    AttributionMeta,
    CostLedger,
    OpenEIConfig,
    RateCacheEntry,
    RatePlanSummary,
    URDBRateTier,
    URDBRecord,
    UtilitySummary,
)

__all__ = [
    "AttributionMeta",
    "CostLedger",
    "OpenEIConfig",
    "OpenEIError",
    "RateCache",
    "RateCacheEntry",
    "RatePlanSummary",
    "URDBRateTier",
    "URDBRecord",
    "UtilitySummary",
    "compute_costs",
    "fetch_rate_detail",
    "fetch_rate_plans",
    "fetch_utilities",
    "resolve_rate",
]
