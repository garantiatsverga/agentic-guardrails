"""Failure Circuit Breaker (чистый Python).

State machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.
Потокобезопасен через threading.Lock.

Особенность: сбой в HALF_OPEN немедленно возвращает в OPEN,
минуя ожидание failure_threshold (probe провален).

Замена Rust-версии (PyO3) на Python. API 1:1.
"""
from __future__ import annotations

import threading
import time
from enum import Enum


class CBState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class FailureBreaker:
    """Потокобезопасный Failure Circuit Breaker."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_probes: int = 1,
    ) -> None:
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_probes = half_open_probes
        self._lock = threading.Lock()
        self._state = CBState.CLOSED
        self._failures = 0
        self._probes = 0
        self._opened_at = time.monotonic()

    def _transition_if_needed(self) -> None:
        """OPEN -> HALF_OPEN, если прошло recovery_timeout."""
        if self._state == CBState.OPEN:
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                self._state = CBState.HALF_OPEN
                self._probes = 0

    @property
    def state(self) -> CBState:
        with self._lock:
            self._transition_if_needed()
            return self._state

    def allow(self) -> bool:
        with self._lock:
            self._transition_if_needed()
            if self._state == CBState.CLOSED:
                return True
            if self._state == CBState.OPEN:
                return False
            # HALF_OPEN: разрешаем до half_open_probes
            if self._probes < self._half_open_probes:
                self._probes += 1
                return True
            return False

    def on_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._probes = 0
            self._state = CBState.CLOSED

    def on_failure(self) -> None:
        with self._lock:
            if self._state == CBState.HALF_OPEN:
                self._state = CBState.OPEN
                self._opened_at = time.monotonic()
                return
            self._failures += 1
            if self._failures >= self._threshold:
                self._state = CBState.OPEN
                self._opened_at = time.monotonic()

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    @property
    def probes_count(self) -> int:
        with self._lock:
            return self._probes
