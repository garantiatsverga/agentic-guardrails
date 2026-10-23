"""Тесты для parallel_runner."""
import asyncio
import pytest

from agentic_guardrails.parallel_runner import parallel, ParallelRunner


class TestParallelRunner:
    async def test_all_success(self):
        async def fn(x):
            return x * 2

        results = await parallel(
            [lambda: fn(1), lambda: fn(2), lambda: fn(3)],
        )
        assert all(r.success for r in results)
        assert [r.value for r in results] == [2, 4, 6]

    async def test_one_fails_others_continue(self):
        """Падение одной задачи не отменяет остальные."""
        async def ok(x):
            return x

        async def fail():
            raise RuntimeError("boom")

        results = await parallel([lambda: ok(1), fail, lambda: ok(3)])
        assert results[0].success is True
        assert results[1].success is False
        assert isinstance(results[1].error, RuntimeError)
        assert results[2].success is True
        assert results[2].value == 3

    async def test_concurrency_limit(self):
        """Не более max_concurrency одновременно."""
        current = 0
        max_seen = 0

        async def task():
            nonlocal current, max_seen
            current += 1
            max_seen = max(max_seen, current)
            await asyncio.sleep(0.02)
            current -= 1
            return "ok"

        await parallel(
            [task] * 10,
            max_concurrency=3,
        )
        assert max_seen <= 3

    async def test_timeout_per_task(self):
        async def slow():
            await asyncio.sleep(10)
            return "too late"

        async def fast():
            return "ok"

        results = await parallel(
            [slow, fast],
            timeout_per_task=0.05,
        )
        assert results[0].success is False
        assert isinstance(results[0].error, asyncio.TimeoutError)
        assert results[1].success is True

    async def test_results_order_preserved(self):
        async def fn(x):
            await asyncio.sleep(0.01 * (5 - x))
            return x

        results = await parallel([lambda x=x: fn(x) for x in [1, 2, 3, 4, 5]])
        assert [r.value for r in results] == [1, 2, 3, 4, 5]

    async def test_empty_input(self):
        results = await parallel([])
        assert results == []

    async def test_with_args(self):
        async def add(a, b):
            return a + b

        results = await parallel([
            (add, 1, 2),
            (add, 3, 4),
        ])
        assert [r.value for r in results] == [3, 7]

    def test_invalid_max_concurrency(self):
        with pytest.raises(ValueError, match="max_concurrency"):
            ParallelRunner(max_concurrency=0)

    def test_invalid_timeout(self):
        with pytest.raises(ValueError, match="timeout_per_task"):
            ParallelRunner(timeout_per_task=-1)


class TestParallelRunnerClass:
    async def test_class_usage(self):
        async def fn(x):
            return x + 1

        runner = ParallelRunner(max_concurrency=2)
        results = await runner.run([lambda: fn(1), lambda: fn(2)])
        assert [r.value for r in results] == [2, 3]

    async def test_class_reusable(self):
        async def fn(x):
            return x

        runner = ParallelRunner(max_concurrency=2)
        r1 = await runner.run([lambda: fn(1)])
        r2 = await runner.run([lambda: fn(2)])
        assert r1[0].value == 1
        assert r2[0].value == 2
