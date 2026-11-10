"""A panel that rate-limits registration, for rehearsing a client against one.

A panel may limit how often a client registers, refusing ``POST /api/v2/auth/register``
with 429 until the client may try again. A config can ask for that with
``panel_config.registration_limit``: how many registrations each client may make in
any sixty seconds, and whether the refusal carries ``Retry-After``, so both answers a
client may meet can be rehearsed. A config naming none registers every client every
time, as a panel always did here.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

_WINDOW_S: Final = 60.0


@dataclass(frozen=True)
class RegistrationLimit:
    """How often each client may register, and whether a refusal says when to retry."""

    per_minute: int
    retry_after: bool = True


def registration_limit(panel_config: Mapping[str, object]) -> RegistrationLimit | None:
    """The limit ``panel_config`` asks for, or None for a panel that sets none.

    Raises:
        ValueError: the limit is not a positive number of registrations.
    """
    raw = panel_config.get("registration_limit")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("panel_config.registration_limit must be a mapping")
    per_minute = raw.get("per_minute")
    if not isinstance(per_minute, int) or isinstance(per_minute, bool) or per_minute < 1:
        raise ValueError("panel_config.registration_limit.per_minute must be a positive integer")
    return RegistrationLimit(per_minute=per_minute, retry_after=bool(raw.get("retry_after", True)))


class RegistrationLimiter:
    """Each client's registrations in the last minute, refused past the limit."""

    def __init__(
        self, limit: RegistrationLimit, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._clock = clock
        self._registered: dict[str, deque[float]] = {}

    @property
    def retry_after(self) -> bool:
        """Whether a refusal says when to retry."""
        return self._limit.retry_after

    def refusal(self, client: str) -> int | None:
        """Seconds until *client* may register again, or None, recording a registration.

        A refused attempt is not a registration, so it does not extend the wait.
        """
        now = self._clock()
        recent = self._registered.setdefault(client, deque())
        while recent and now - recent[0] >= _WINDOW_S:
            recent.popleft()
        if len(recent) >= self._limit.per_minute:
            return max(1, math.ceil(recent[0] + _WINDOW_S - now))
        recent.append(now)
        return None
