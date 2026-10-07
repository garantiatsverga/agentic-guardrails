"""Parallel Runner — параллельное выполнение async-задач с ограничением.

В отличие от asyncio.gather:
- ограничивает количество одновременных задач (max_concurrency)
- не отменяет остальные при падении одной
- возвращает TaskResult с value/error для каждой
- поддерживает таймаут на задачу

Пример:
    results = await parallel([
        lambda: fetch("a"),
        lambda: fetch("b"),
    ], max_concurrency=2, timeout_per_task=5.0)

    for r in results:
        if r.success:
            print(r.value)
        else:
            print(f"error: {r.error}")
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import asyncio


@dataclass
class TaskResult:
    """Результат одной задачи."""
    index: int
    value: Any = None
    error: Exception | None = None

    @property
    def success(self) -> bool:
        return self.error is None


# Тип задачи: либо callable без аргументов, либо (callable, *args)
Task = Callable[[], Awaitable[Any]] | tuple[Callable[..., Awaitable[Any]], ...]


@dataclass
class ParallelRunner:
    """Ограниченный параллельный запуск async-задач.

    max_concurrency: сколько задач выполнять одновременно.
    timeout_per_task: таймаут на одну задачу в секундах (None — без лимита).
    """
    max_concurrency: int = 10
    timeout_per_task: float | None = None

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError(
                f"max_concurrency must be >= 1, got {self.max_concurrency}"
            )
        if self.timeout_per_task is not None and self.timeout_per_task <= 0:
            raise ValueError(
                f"timeout_per_task must be > 0 or None, got {self.timeout_per_task}"
            )

    async def run(self, tasks: list[Task]) -> list[TaskResult]:
        """Запускает все задачи с ограничением одновременности.

        Возвращает список TaskResult в порядке входа.
        """
        if not tasks:
            return []

        semaphore = asyncio.Semaphore(self.max_concurrency)
        results: list[TaskResult] = [
            TaskResult(index=i) for i in range(len(tasks))
        ]

        async def run_one(idx: int, task: Task) -> None:
            async with semaphore:
                try:
                    coro = self._invoke(task)
                    if self.timeout_per_task is not None:
                        value = await asyncio.wait_for(
                            coro, timeout=self.timeout_per_task
                        )
                    else:
                        value = await coro
                    results[idx].value = value
                except Exception as exc:
                    results[idx].error = exc

        await asyncio.gather(
            *(run_one(i, t) for i, t in enumerate(tasks))
        )
        return results

    @staticmethod
    def _invoke(task: Task) -> Awaitable[Any]:
        """Вызывает задачу: callable() или callable(*args)."""
        if isinstance(task, tuple):
            fn, *args = task
            return fn(*args)
        return task()


async def parallel(
    tasks: list[Task],
    max_concurrency: int = 10,
    timeout_per_task: float | None = None,
) -> list[TaskResult]:
    """Удобная функция: parallel([...], max_concurrency=N)."""
    runner = ParallelRunner(
        max_concurrency=max_concurrency,
        timeout_per_task=timeout_per_task,
    )
    return await runner.run(tasks)
