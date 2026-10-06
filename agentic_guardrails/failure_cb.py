"""Failure Circuit Breaker — защита от каскадных сбоев внешних вызовов.

CLOSED -> OPEN -> HALF_OPEN -> CLOSED
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from time import monotonic


class CBState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class FailureBreaker:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_probes: int = 1

    _state: CBState = CBState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes: int = 0

    @property
    def state(self) -> CBState:
        if self._state is CBState.OPEN and \
           monotonic() - self._opened_at >= self.recovery_timeout:
            self._state = CBState.HALF_OPEN
            self._probes = 0
        return self._state

    def allow(self) -> bool:
        s = self.state
        if s is CBState.CLOSED:
            return True
        if s is CBState.HALF_OPEN and self._probes < self.half_open_probes:
            self._probes += 1
            return True
        return False

    def on_success(self) -> None:
        self._failures = 0
        self._state = CBState.CLOSED
        self._probes = 0

    def on_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = CBState.OPEN
            self._opened_at = monotonic()
