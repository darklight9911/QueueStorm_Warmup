"""Tests for rate limiting (unit + endpoint integration)."""

import asyncio

from fastapi.testclient import TestClient

import app.main as main
from app.main import app
from app.ratelimit import InMemoryRateLimiter, build_rate_limiter


def test_in_memory_limiter_blocks_after_limit():
    limiter = InMemoryRateLimiter(limit=3, window_seconds=60)

    async def run():
        return [await limiter.hit("client-a") for _ in range(5)]

    results = asyncio.run(run())
    assert [r.allowed for r in results] == [True, True, True, False, False]
    assert results[0].remaining == 2
    assert results[3].remaining == 0
    assert results[0].limit == 3


def test_in_memory_limiter_keys_are_independent():
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60)

    async def run():
        return [await limiter.hit("a"), await limiter.hit("b")]

    a, b = asyncio.run(run())
    assert a.allowed and b.allowed  # different clients don't share a budget


def test_factory_without_redis_returns_in_memory():
    assert isinstance(build_rate_limiter(10, 60, redis_url=None), InMemoryRateLimiter)


def test_endpoint_is_rate_limited(monkeypatch):
    monkeypatch.setattr(main, "_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(main, "rate_limiter", InMemoryRateLimiter(limit=3, window_seconds=60))
    client = TestClient(app)
    body = {"ticket_id": "T", "message": "hello"}

    codes = [client.post("/sort-ticket", json=body).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    assert codes[4] == 429


def test_rate_limit_headers_and_retry_after(monkeypatch):
    monkeypatch.setattr(main, "_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(main, "rate_limiter", InMemoryRateLimiter(limit=1, window_seconds=60))
    client = TestClient(app)
    body = {"ticket_id": "T", "message": "hello"}

    ok = client.post("/sort-ticket", json=body)
    assert ok.status_code == 200
    assert ok.headers.get("x-ratelimit-limit") == "1"
    assert ok.headers.get("x-ratelimit-remaining") == "0"

    blocked = client.post("/sort-ticket", json=body)
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "rate_limited"
    assert blocked.headers.get("retry-after") is not None


def test_health_is_exempt_from_rate_limiting(monkeypatch):
    monkeypatch.setattr(main, "_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(main, "rate_limiter", InMemoryRateLimiter(limit=1, window_seconds=60))
    client = TestClient(app)
    codes = [client.get("/health").status_code for _ in range(5)]
    assert all(code == 200 for code in codes)
