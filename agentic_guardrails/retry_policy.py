"""Retry Policy — универсальные повторы для async-функций.

Настройка:
- количество попыток
- стратегия backoff (фиксированная, экспоненциальная)
- какие исключения повторять (retry_on)
- какие не повторять (give_up_on)

Универсально: работает с любой async-функцией.

Пример:
    policy = RetryPolicy(
        max_attempts=3,
        backoff=ExponentialBackoff(base=0.1, factor=2.0, jitter=True),
        retry_on=(TimeoutError, ConnectionError),
    )

    @retry(policy)
    async def call_llm(prompt):
        ...
"""
from __future__ import annotations
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Awaitable, Callable, Protocol, TypeVar

import asyncio
import random


T = TypeVar("T")


class Backoff(Protocol):
    """Интерфейс стратегии backoff."""

    def delay(self, attempt: int) -> float:
        """Задержка в секундах перед попыткой `attempt` (нумерация с 1)."""
        ...


@dataclass
class FixedBackoff:
    """Фиксированная задержка между попытками."""
    interval: float = 1.0

    def delay(self, attempt: int) -> float:  # noqa: ARG002
        return self.interval


@dataclass
class ExponentialBackoff:
    """Экспоненциальная задержка: base * factor^(attempt-1), с потолком max_delay.

    jitter=True добавляет случайность в диапазоне [0, d] — защита от
    синхронного «стада» повторов у нескольких клиентов.
    """
    base: float = 0.1
    factor: float = 2.0
    max_delay: float = 60.0
    jitter: bool = True

    def delay(self, attempt: int) -> float:
        d = self.base * (self.factor ** (attempt - 1))
        d = min(d, self.max_delay)
        if self.jitter:
            d = random.uniform(0, d)
        return d


@dataclass
class RetryPolicy:
    """Политика повторов для async-функций.

    max_attempts: общее число попыток (включая первую).
    backoff: стратегия задержки между попытками.
    retry_on: кортеж исключений, при которых повторять.
        Если пусто — повторять при любом Exception.
    give_up_on: кортеж исключений, при которых не повторять
        (приоритет выше retry_on).
    """
    max_attempts: int = 3
    backoff: Backoff = field(default_factory=ExponentialBackoff)
    retry_on: tuple[type[BaseException], ...] = ()
    give_up_on: tuple[type[BaseException], ...] = ()

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await fn(*args, **kwargs)
            except BaseException as exc:
                if self._should_give_up(exc):
                    raise
                if not self._should_retry(exc):
                    raise
                last_exc = exc
                if attempt < self.max_attempts:
                    d = self.backoff.delay(attempt)
                    if d > 0:
                        await asyncio.sleep(d)
        assert last_exc is not None
        raise last_exc

    def _should_give_up(self, exc: BaseException) -> bool:
        return bool(self.give_up_on) and isinstance(exc, self.give_up_on)

    def _should_retry(self, exc: BaseException) -> bool:
        if not self.retry_on:
            return True
        return isinstance(exc, self.retry_on)

    def __call__(self, fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await self.execute(fn, *args, **kwargs)
        return wrapper


def retry(policy: RetryPolicy):
    """Декоратор для применения RetryPolicy."""
    return policy
