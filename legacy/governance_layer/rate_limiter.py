"""In-memory deterministic rate limiter for per-user/per-role/per-endpoint controls."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable, Deque, DefaultDict, Iterable


class RateLimitViolation(RuntimeError):
    """Raised when a rate limit check fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RateLimiter:
    """Sliding-window limiter enforcing minute and hour thresholds."""

    def __init__(self, time_source: Callable[[], float] | None = None) -> None:
        self._time_source = time_source or time.time
        self._buckets: DefaultDict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def _bucket_key(dimension: str, identity: str, endpoint: str, window_name: str) -> str:
        return f"{dimension}:{identity}:{endpoint}:{window_name}"

    @staticmethod
    def _prune(bucket: Deque[float], now: float, window_seconds: int) -> None:
        threshold = now - float(window_seconds)
        while bucket and bucket[0] <= threshold:
            bucket.popleft()

    def _count_after_prune(self, key: str, now: float, window_seconds: int) -> int:
        bucket = self._buckets[key]
        self._prune(bucket, now, window_seconds)
        return len(bucket)

    def _append(self, key: str, now: float) -> None:
        self._buckets[key].append(now)

    def check_and_record(
        self,
        *,
        user_id: str,
        role: str,
        endpoint: str,
        per_minute_limit: int,
        per_hour_limit: int,
    ) -> None:
        if not user_id.strip() or not role.strip() or not endpoint.strip():
            raise RateLimitViolation(code="GOV_MISSING_IDENTITY", message="missing identity context for rate limiting")

        if per_minute_limit <= 0 or per_hour_limit <= 0:
            raise RateLimitViolation(code="GOV_INVALID_RATE_CONFIG", message="invalid rate limit configuration")

        now = float(self._time_source())

        checks: list[tuple[str, int, int, str]] = [
            (self._bucket_key("user", user_id, endpoint, "minute"), 60, per_minute_limit, "user minute limit exceeded"),
            (self._bucket_key("role", role, endpoint, "minute"), 60, per_minute_limit, "role minute limit exceeded"),
            (self._bucket_key("user", user_id, endpoint, "hour"), 3600, per_hour_limit, "user hour limit exceeded"),
            (self._bucket_key("role", role, endpoint, "hour"), 3600, per_hour_limit, "role hour limit exceeded"),
        ]

        with self._lock:
            for key, window_seconds, limit_value, message in checks:
                current = self._count_after_prune(key, now, window_seconds)
                if current >= limit_value:
                    raise RateLimitViolation(code="GOV_RATE_LIMIT_EXCEEDED", message=message)

            for key, _, _, _ in checks:
                self._append(key, now)
