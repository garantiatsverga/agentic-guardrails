"""Failure Circuit Breaker — тонкая обёртка над Rust-ядром.

API 1:1 с прежней Python-версией. Пользователь не знает, что под капотом Rust.
"""
from __future__ import annotations
from enum import Enum

from ._core import FailureBreaker as _RustFailureBreaker


class CBState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class FailureBreaker:
    """Потокобезопасный Failure Circuit Breaker.

    State machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.
    Гонки исключены на уровне Rust (AtomicU8 + AtomicU32).

    Особенность: сбой в HALF_OPEN немедленно возвращает в OPEN,
    минуя ожидание failure_threshold (probe провален).
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_probes: int = 1,
    ) -> None:
        self._inner = _RustFailureBreaker(
            failure_threshold,
            recovery_timeout,
            half_open_probes,
        )

    @property
    def state(self) -> CBState:
        return CBState(self._inner.state())

    def allow(self) -> bool:
        return self._inner.allow()

    def on_success(self) -> None:
        self._inner.on_success()

    def on_failure(self) -> None:
        self._inner.on_failure()

    @property
    def failures(self) -> int:
        return self._inner.failures

    @property
    def probes_count(self) -> int:
        return self._inner.probes_count
