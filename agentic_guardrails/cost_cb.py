"""Cost Circuit Breaker (чистый Python).

Три уровня бюджета: per-task, per-session, per-horizon.
Soft-порог (по умолчанию 70%) -> SOFT; hard (100%) -> HARD.

Потокобезопасен через threading.Lock.
Замена Rust-версии (PyO3) на Python. API 1:1.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum


class CostState(str, Enum):
    OK = "ok"
    SOFT = "soft"
    HARD = "hard"


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


class CostBreaker:
    """Потокобезопасный Cost Circuit Breaker.

    horizon_seconds: если > 0, счётчик horizon автоматически сбрасывается
    по истечении интервала. 0.0 — без сброса.
    """

    def __init__(
        self,
        per_task: Budget,
        per_session: Budget,
        per_horizon: Budget,
        horizon_seconds: float = 0.0,
    ) -> None:
        self._per_task = per_task
        self._per_session = per_session
        self._per_horizon = per_horizon
        self._horizon_seconds = horizon_seconds
        self._lock = threading.Lock()
        self._task_used = 0
        self._session_used = 0
        self._horizon_used = 0
        self._horizon_start = time.monotonic()

    def _maybe_reset_horizon(self) -> None:
        if self._horizon_seconds <= 0:
            return
        if time.monotonic() - self._horizon_start >= self._horizon_seconds:
            self._horizon_used = 0
            self._horizon_start = time.monotonic()

    def charge(self, tokens: int) -> CostState:
        with self._lock:
            self._maybe_reset_horizon()
            self._task_used += tokens
            self._session_used += tokens
            self._horizon_used += tokens

            states = [
                self._per_task.state(self._task_used),
                self._per_session.state(self._session_used),
                self._per_horizon.state(self._horizon_used),
            ]
            if CostState.HARD in states:
                return CostState.HARD
            if CostState.SOFT in states:
                return CostState.SOFT
            return CostState.OK

    def charge_batch(self, tokens_list: list[int]) -> CostState:
        last = CostState.OK
        for t in tokens_list:
            last = self.charge(t)
        return last

    def reset_task(self) -> None:
        with self._lock:
            self._task_used = 0

    def reset_session(self) -> None:
        with self._lock:
            self._task_used = 0
            self._session_used = 0

    @property
    def task_used(self) -> int:
        with self._lock:
            return self._task_used

    @property
    def session_used(self) -> int:
        with self._lock:
            return self._session_used

    @property
    def horizon_used(self) -> int:
        with self._lock:
            return self._horizon_used
