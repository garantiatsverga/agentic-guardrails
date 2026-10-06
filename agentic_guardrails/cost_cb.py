"""Cost Circuit Breaker — тонкая обёртка над Rust-ядром.

API 1:1 с прежней Python-версией. Пользователь не знает, что под капотом Rust.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

from ._core import Budget as _RustBudget
from ._core import CostBreaker as _RustCostBreaker


class CostState(str, Enum):
    OK = "ok"
    SOFT = "soft"
    HARD = "hard"


@dataclass
class Budget:
    limit: int
    soft_ratio: float = 0.7

    def to_rust(self) -> _RustBudget:
        return _RustBudget(self.limit, self.soft_ratio)


class CostBreaker:
    """Потокобезопасный Cost Circuit Breaker.

    Три уровня бюджета: per-task, per-session, per-horizon.
    Гонки исключены на уровне Rust (AtomicU64).
    """

    def __init__(
        self,
        per_task: Budget,
        per_session: Budget,
        per_horizon: Budget,
    ) -> None:
        self._inner = _RustCostBreaker(
            per_task.to_rust(),
            per_session.to_rust(),
            per_horizon.to_rust(),
        )

    def charge(self, tokens: int) -> CostState:
        return CostState(self._inner.charge(tokens))

    def reset_task(self) -> None:
        self._inner.reset_task()

    def reset_session(self) -> None:
        self._inner.reset_session()

    @property
    def task_used(self) -> int:
        return self._inner.task_used

    @property
    def session_used(self) -> int:
        return self._inner.session_used

    @property
    def horizon_used(self) -> int:
        return self._inner.horizon_used
