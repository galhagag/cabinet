"""App-level rate limiting.

Phase 1 ships a local in-process limiter for dev/test and single-instance
deployments. The API surface is intentionally small so a shared backend can be
slotted in later without changing routers.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from ..config import ConfigError, Settings


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int = 0


@dataclass
class _WindowBucket:
    count: int
    reset_at: float


class RateLimiter(Protocol):
    def acquire(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        ...


class InProcessRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _WindowBucket] = {}
        self._lock = threading.Lock()

    def acquire(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None or now >= bucket.reset_at:
                bucket = _WindowBucket(count=0, reset_at=now + window_seconds)
                self._buckets[key] = bucket

            if bucket.count < limit:
                bucket.count += 1
                return RateLimitDecision(allowed=True)

            retry_after = max(1, math.ceil(bucket.reset_at - now))
            return RateLimitDecision(allowed=False, retry_after=retry_after)


def build_rate_limiter(settings: Settings) -> RateLimiter:
    if settings.ratelimit_provider == "inprocess":
        return InProcessRateLimiter()
    raise ConfigError(
        "CABINET_RATELIMIT_PROVIDER must be 'inprocess' for now, got "
        f"{settings.ratelimit_provider!r}"
    )