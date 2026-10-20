"""Spend protection for a public demo: every question costs real LLM credits.

Two in-memory limits: a sliding one-hour window per client, and a global daily cap. The
per-client limit is best effort (client ids come from a spoofable forwarding header); the
daily cap is the hard bound on spend. State
is per process, which matches a single small Cloud Run / Render instance; a multi-instance
deployment would move these counters to Redis.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime


class UsageLimitExceeded(Exception):
    pass


class UsageLimiter:
    def __init__(
        self,
        per_client_per_hour: int = 0,
        daily_cap: int = 0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """A limit of 0 disables that check."""
        self.per_client_per_hour = per_client_per_hour
        self.daily_cap = daily_cap
        self._clock = clock
        self._lock = threading.Lock()
        self._recent: dict[str, deque[float]] = defaultdict(deque)
        self._day = ""
        self._day_count = 0

    def check_and_record(self, client_id: str) -> None:
        now = self._clock()
        today = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
        with self._lock:
            if today != self._day:
                self._day, self._day_count = today, 0
            if self.daily_cap and self._day_count >= self.daily_cap:
                raise UsageLimitExceeded(
                    "The demo has reached its daily question limit. Please try again tomorrow, "
                    "or run it locally with your own key."
                )
            window = self._recent[client_id]
            while window and window[0] <= now - 3600:
                window.popleft()
            if self.per_client_per_hour and len(window) >= self.per_client_per_hour:
                raise UsageLimitExceeded(
                    f"Limit of {self.per_client_per_hour} questions per hour reached."
                )
            window.append(now)
            self._day_count += 1
