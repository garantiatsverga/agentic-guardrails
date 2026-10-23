"""Тесты для retry_policy."""
import pytest

from agentic_guardrails.retry_policy import (
    RetryPolicy,
    FixedBackoff,
    ExponentialBackoff,
    retry,
)


class TestRetryPolicy:
    async def test_success_first_attempt(self):
        calls = []

        async def fn():
            calls.append(1)
            return "ok"

        policy = RetryPolicy(max_attempts=3)
        result = await policy.execute(fn)
        assert result == "ok"
        assert len(calls) == 1

    async def test_retry_then_success(self):
        calls = []

        async def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("fail")
            return "ok"

        policy = RetryPolicy(
            max_attempts=5,
            backoff=FixedBackoff(interval=0.001),
            retry_on=(ConnectionError,),
        )
        result = await policy.execute(fn)
        assert result == "ok"
        assert len(calls) == 3

    async def test_gives_up_after_max_attempts(self):
        calls = []

        async def fn():
            calls.append(1)
            raise ConnectionError(f"fail {len(calls)}")

        policy = RetryPolicy(
            max_attempts=3,
            backoff=FixedBackoff(interval=0.001),
            retry_on=(ConnectionError,),
        )
        with pytest.raises(ConnectionError) as exc:
            await policy.execute(fn)
        assert len(calls) == 3
        assert "fail 3" in str(exc.value)

    async def test_give_up_on_specific_exception(self):
        calls = []

        async def fn():
            calls.append(1)
            raise ValueError("bad input")

        policy = RetryPolicy(
            max_attempts=5,
            backoff=FixedBackoff(interval=0.001),
            retry_on=(ConnectionError,),
            give_up_on=(ValueError,),
        )
        with pytest.raises(ValueError):
            await policy.execute(fn)
        assert len(calls) == 1

    async def test_retry_on_empty_retries_any_exception(self):
        """Пустой retry_on — повторяем любое исключение."""
        calls = []

        async def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("transient")
            return "ok"

        policy = RetryPolicy(
            max_attempts=5,
            backoff=FixedBackoff(interval=0.001),
            # retry_on не указан -> повторяем всё
        )
        result = await policy.execute(fn)
        assert result == "ok"
        assert len(calls) == 3

    async def test_retry_on_only_matching(self):
        calls = []

        async def fn():
            calls.append(1)
            raise RuntimeError("other")

        policy = RetryPolicy(
            max_attempts=5,
            backoff=FixedBackoff(interval=0.001),
            retry_on=(ConnectionError,),
        )
        with pytest.raises(RuntimeError):
            await policy.execute(fn)
        assert len(calls) == 1

    async def test_decorator(self):
        calls = []

        policy = RetryPolicy(
            max_attempts=3,
            backoff=FixedBackoff(interval=0.001),
            retry_on=(ConnectionError,),
        )

        @retry(policy)
        async def fn():
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionError("fail")
            return "ok"

        result = await fn()
        assert result == "ok"
        assert len(calls) == 2

    async def test_custom_backoff(self):
        delays = []

        class RecordingBackoff:
            def delay(self, attempt: int) -> float:
                delays.append(attempt)
                return 0.001

        calls = []

        async def fn():
            calls.append(1)
            if len(calls) < 4:
                raise ConnectionError("fail")
            return "ok"

        policy = RetryPolicy(
            max_attempts=5,
            backoff=RecordingBackoff(),
            retry_on=(ConnectionError,),
        )
        result = await policy.execute(fn)
        assert result == "ok"
        assert delays == [1, 2, 3]


class TestBackoffs:
    def test_fixed_backoff(self):
        b = FixedBackoff(interval=1.0)
        assert b.delay(1) == 1.0
        assert b.delay(5) == 1.0

    def test_exponential_backoff(self):
        b = ExponentialBackoff(base=0.1, factor=2.0, max_delay=10.0, jitter=False)
        assert b.delay(1) == pytest.approx(0.1)
        assert b.delay(2) == pytest.approx(0.2)
        assert b.delay(3) == pytest.approx(0.4)
        assert b.delay(10) == 10.0

    def test_exponential_backoff_with_jitter(self):
        b = ExponentialBackoff(base=0.1, factor=2.0, jitter=True)
        d1 = b.delay(3)
        d2 = b.delay(3)
        assert d1 != d2
        assert 0 < d1 <= 0.4
        assert 0 < d2 <= 0.4
