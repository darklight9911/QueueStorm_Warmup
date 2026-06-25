"""Pluggable rate limiting for the QueueStorm service.

Two backends share one async interface so the rest of the app doesn't care
which is in use:

* ``InMemoryRateLimiter`` — per-process fixed-window counter. Zero dependencies,
  ideal for a single instance or local dev. With multiple workers/instances each
  process keeps its own counters, so the *effective* limit is multiplied by the
  number of processes.
* ``RedisRateLimiter`` — a shared fixed-window counter in Redis (atomic
  ``INCR`` + ``EXPIRE``). Use this to enforce **one global limit across many
  instances/workers** — the configuration you want when scaling horizontally to
  handle very high traffic.

``build_rate_limiter()`` returns the Redis backend when ``REDIS_URL`` is set,
otherwise the in-memory one. Both **fail open**: if the backing store errors, the
request is allowed. A rate limiter must never become a single point of failure
that takes the whole API down.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("queuestorm.ratelimit")


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after: int  # seconds until the current window resets


class InMemoryRateLimiter:
    """Fixed-window counter kept in this process's memory."""

    def __init__(self, limit: int, window_seconds: int, max_keys: int = 100_000):
        self.limit = limit
        self.window = window_seconds
        self._max_keys = max_keys
        self._hits: dict[str, tuple[int, int]] = {}  # key -> (window_id, count)
        self._lock = threading.Lock()

    async def hit(self, key: str) -> RateLimitResult:
        now = time.time()
        window_id = int(now // self.window)
        reset_after = self.window - int(now % self.window)
        with self._lock:
            if len(self._hits) > self._max_keys:
                self._sweep(window_id)
            stored = self._hits.get(key)
            count = stored[1] + 1 if stored and stored[0] == window_id else 1
            self._hits[key] = (window_id, count)
        allowed = count <= self.limit
        remaining = max(0, self.limit - count)
        return RateLimitResult(allowed, self.limit, remaining, reset_after)

    def _sweep(self, current_window: int) -> None:
        """Drop keys from previous windows to bound memory use."""
        stale = [k for k, (w, _) in self._hits.items() if w < current_window]
        for k in stale:
            self._hits.pop(k, None)


class RedisRateLimiter:
    """Fixed-window counter shared across instances via Redis.

    The Redis client is created lazily and reused. All errors are swallowed and
    the request is allowed (fail-open) so a Redis blip cannot break the API.
    """

    def __init__(self, redis_url: str, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._redis_url = redis_url
        self._client = None

    def _get_client(self):
        if self._client is None:
            from redis import asyncio as aioredis  # lazy import; optional dep

            self._client = aioredis.from_url(
                self._redis_url, encoding="utf-8", decode_responses=True
            )
        return self._client

    async def hit(self, key: str) -> RateLimitResult:
        now = time.time()
        window_id = int(now // self.window)
        reset_after = self.window - int(now % self.window)
        redis_key = f"rl:{key}:{window_id}"
        try:
            client = self._get_client()
            count = await client.incr(redis_key)
            if count == 1:
                # First hit in this window — set the expiry once.
                await client.expire(redis_key, self.window)
        except Exception as exc:  # pragma: no cover - fail open, never break API
            logger.warning("redis rate limiter unavailable, allowing request: %s", exc)
            return RateLimitResult(True, self.limit, self.limit, reset_after)
        allowed = count <= self.limit
        remaining = max(0, self.limit - count)
        return RateLimitResult(allowed, self.limit, remaining, reset_after)


def _redact(url: str) -> str:
    """Hide any credentials in a Redis URL before logging it."""
    if "@" in url:
        scheme, _, rest = url.partition("://")
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def build_rate_limiter(limit: int, window_seconds: int, redis_url: Optional[str] = None):
    """Return the Redis limiter when a URL is given, else the in-memory one."""
    if redis_url:
        logger.info(
            "rate limiting: Redis backend (distributed) at %s — %d req / %ds",
            _redact(redis_url),
            limit,
            window_seconds,
        )
        return RedisRateLimiter(redis_url, limit, window_seconds)
    logger.info(
        "rate limiting: in-memory backend (per-process) — %d req / %ds",
        limit,
        window_seconds,
    )
    return InMemoryRateLimiter(limit, window_seconds)


def get_client_ip(request, trust_proxy: bool) -> str:
    """Best-effort client IP, honouring proxy headers when trusted.

    Behind a PaaS/CDN the real client IP arrives in ``X-Forwarded-For``; trust it
    only when ``trust_proxy`` is set, otherwise clients could spoof the header to
    dodge the limit.
    """
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    client = request.client
    return client.host if client else "unknown"
