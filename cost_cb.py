"""Cost Circuit Breaker — защита от зацикливания агента и взрыва токенов.

Три уровня бюджета: per-task, per-session, per-horizon.
Soft-порог (70%) -> эскалация; hard (100%) -> остановка.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from time import monotonic


class CostState(str, Enum):
    OK = "ok"
    SOFT = "soft"      # 70% — эскалация
    HARD = "hard"      # 100% — stop


@dataclass
class Budget:
    limit: int
    soft_ratio: float = 0.7

    def state(self, used: int) -> CostState:
        if used >= self.limit:
            return CostState.HARD
        if used >= self.limit * self.soft_ratio:
            return CostState.SOFT
        return CostState.OK


@dataclass
class CostBreaker:
    per_task: Budget
    per_session: Budget
    per_horizon: Budget
    horizon_seconds: float = 3600.0

    _task_used: int = 0
    _session_used: int = 0
    _horizon_used: int = 0
    _horizon_start: float = field(default_factory=monotonic)

    def charge(self, tokens: int) -> CostState:
        if monotonic() - self._horizon_start > self.horizon_seconds:
            self._horizon_used = 0
            self._horizon_start = monotonic()

        self._task_used += tokens
        self._session_used += tokens
        self._horizon_used += tokens

        for used, budget in (
            (self._task_used, self.per_task),
            (self._session_used, self.per_session),
            (self._horizon_used, self.per_horizon),
        ):
            state = budget.state(used)
            if state is not CostState.OK:
                return state
        return CostState.OK

    def reset_task(self) -> None:
        self._task_used = 0

    def reset_session(self) -> None:
        self._task_used = self._session_used = 0
