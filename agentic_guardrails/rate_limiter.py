"""Rate Limiter — token bucket для ограничения частоты вызовов.

Универсально: работает с любым async-кодом, ограничивая частоту.

Пример:
    limiter = RateLimiter(max_tokens=100, refill_rate=10.0)

    # Способ 1: явное ожидание
    await limiter.acquire()
    await call_api()

    # Способ 2: контекстный менеджер
    async with limiter:
        await call_api()

    # Способ 3: декоратор
    @limiter.wrap
    async def call_api():
        ...

    # Способ 4: без ожидания
    if limiter.try_acquire():
        await call_api()
"""
from __future__ import annotations
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

import asyncio
import time


T = TypeVar("T")


@dataclass
class RateLimiter:
    """Token bucket.

    max_tokens: ёмкость ведра (максимум токенов).
    refill_rate: скорость пополнения (токенов в секунду).
    """
    max_tokens: float
    refill_rate: float

    _tokens: float = field(init=False, default=0.0)
    _last_refill: float = field(init=False, default=0.0)
    _lock: asyncio.Lock = field(init=False)

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError(f"max_tokens must be > 0, got {self.max_tokens}")
        if self.refill_rate <= 0:
            raise ValueError(f"refill_rate must be > 0, got {self.refill_rate}")
        self._tokens = float(self.max_tokens)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> float:
        """Текущее количество токенов (с учётом времени)."""
        self._refill()
        return self._tokens

    def _refill(self) -> None:
        """Пополняет ведро в зависимости от прошедшего времени."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                self.max_tokens,
                self._tokens + elapsed * self.refill_rate,
            )
            self._last_refill = now

    async def acquire(self, cost: float = 1.0) -> None:
        """Ожидает, пока в ведре не будет `cost` токенов, потом списывает."""
        if cost <= 0:
            raise ValueError(f"cost must be > 0, got {cost}")
        if cost > self.max_tokens:
            raise ValueError(
                f"cost ({cost}) > max_tokens ({self.max_tokens}) — "
                f"невозможно выполнить"
            )

        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                # Сколько ждать до нужного количества
                deficit = cost - self._tokens
                wait = deficit / self.refill_rate
                await asyncio.sleep(wait)

    def try_acquire(self, cost: float = 1.0) -> bool:
        """Non-blocking: списывает токены, если хватает. Иначе False."""
        if cost <= 0:
            raise ValueError(f"cost must be > 0, got {cost}")
        self._refill()
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def wrap(self, fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        """Декоратор: списывает 1 токен перед каждым вызовом."""
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            await self.acquire()
            return await fn(*args, **kwargs)
        return wrapper
