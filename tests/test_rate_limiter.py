"""Тесты для rate_limiter — token bucket."""
import asyncio
import time

import pytest

from agentic_guardrails.rate_limiter import RateLimiter


class TestRateLimiter:
    async def test_acquire_under_limit(self):
        """Быстрый acquire при достатке токенов."""
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        t0 = time.monotonic()
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1  # не ждали
        assert limiter.tokens < 10

    async def test_acquire_blocks_when_exhausted(self):
        """Ждёт при исчерпании токенов."""
        limiter = RateLimiter(max_tokens=2, refill_rate=10.0)  # 10 токенов/сек
        await limiter.acquire()
        await limiter.acquire()
        assert limiter.tokens < 1.0

        t0 = time.monotonic()
        await limiter.acquire()  # должен подождать ~0.1 сек
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.05

    async def test_refill_over_time(self):
        """Токены восстанавливаются со временем."""
        limiter = RateLimiter(max_tokens=10, refill_rate=100.0)
        await limiter.acquire(cost=10)
        before = limiter.tokens
        await asyncio.sleep(0.05)
        # refill_rate=100 -> +5 токенов за 0.05 сек
        assert limiter.tokens > before
        assert limiter.tokens <= 10  # не превышаем max

    async def test_try_acquire_returns_false_when_empty(self):
        """Non-blocking: False при исчерпании."""
        limiter = RateLimiter(max_tokens=2, refill_rate=1.0)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False  # пусто

    async def test_weighted_acquire(self):
        """cost=5 списывает 5 токенов (с поправкой на refill)."""
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        await limiter.acquire(cost=5)
        # После acquire проходит микросекунда -> refill добавляет доли токена.
        # Проверяем с допуском 0.01.
        assert limiter.tokens == pytest.approx(5.0, abs=0.01)

    async def test_context_manager(self):
        """async with limiter."""
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        async with limiter:
            pass
        assert limiter.tokens < 10

    async def test_decorator_wrap(self):
        """@limiter.wrap."""
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        calls = []

        @limiter.wrap
        async def fn(x):
            calls.append(x)
            return x * 2

        assert await fn(5) == 10
        assert await fn(7) == 14
        assert calls == [5, 7]

    async def test_burst_allowed(self):
        """Можно быстро списать всю ёмкость (burst)."""
        limiter = RateLimiter(max_tokens=5, refill_rate=1.0)
        t0 = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1  # все 5 сразу, без ожидания


class TestRateLimiterCostValidation:
    async def test_acquire_rejects_zero_cost(self):
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        with pytest.raises(ValueError, match="cost"):
            await limiter.acquire(cost=0)

    async def test_acquire_rejects_negative_cost(self):
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        with pytest.raises(ValueError, match="cost"):
            await limiter.acquire(cost=-1)

    async def test_acquire_rejects_cost_over_max(self):
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        with pytest.raises(ValueError, match="max_tokens"):
            await limiter.acquire(cost=100)

    def test_try_acquire_rejects_zero_cost(self):
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        with pytest.raises(ValueError, match="cost"):
            limiter.try_acquire(cost=0)


class TestRateLimiterConfig:
    def test_invalid_max_tokens(self):
        with pytest.raises(ValueError, match="max_tokens"):
            RateLimiter(max_tokens=0, refill_rate=1.0)

    def test_invalid_refill_rate(self):
        with pytest.raises(ValueError, match="refill_rate"):
            RateLimiter(max_tokens=10, refill_rate=0)
