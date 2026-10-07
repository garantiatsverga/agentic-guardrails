"""Лёгкая Saga — компенсация при сбоях в цепочке шагов.

Каждый шаг имеет компенсацию. При падении — откат в обратном порядке.
Ошибки компенсаций собираются и повторяются; окончательный провал
помечается флагом inconsistent.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import asyncio


Step = Callable[[dict[str, Any]], Awaitable[Any]]
Compensation = Callable[[dict[str, Any], Any], Awaitable[None]]


@dataclass
class SagaStep:
    name: str
    action: Step
    compensate: Compensation


@dataclass
class SagaResult:
    success: bool
    completed: list[str] = field(default_factory=list)
    failed_at: str | None = None
    error: Exception | None = None
    context: dict[str, Any] = field(default_factory=dict)
    # Новые поля:
    compensation_errors: list[Exception] = field(default_factory=list)
    inconsistent: bool = False
    compensation_attempts: dict[str, int] = field(default_factory=dict)


# ==== Параметры retry компенсаций ====
COMPENSATION_MAX_ATTEMPTS = 3
COMPENSATION_BACKOFF_BASE = 0.05  # 50 мс
COMPENSATION_BACKOFF_FACTOR = 4.0  # 50, 200, 800 мс


async def _run_compensation_with_retry(
    step: SagaStep,
    ctx: dict[str, Any],
    out: Any,
    result: SagaResult,
) -> bool:
    """Пытается выполнить компенсацию с retry.

    Возвращает True при успехе, False — при окончательном провале.
    Записывает попытки в result.compensation_attempts.
    """
    last_exc: Exception | None = None
    delay = COMPENSATION_BACKOFF_BASE

    for attempt in range(1, COMPENSATION_MAX_ATTEMPTS + 1):
        result.compensation_attempts[step.name] = attempt
        try:
            await step.compensate(ctx, out)
            return True
        except Exception as exc:
            last_exc = exc
            if attempt < COMPENSATION_MAX_ATTEMPTS:
                await asyncio.sleep(delay)
                delay *= COMPENSATION_BACKOFF_FACTOR

    if last_exc is not None:
        result.compensation_errors.append(last_exc)
    return False


async def run_saga(
    steps: list[SagaStep],
    context: dict[str, Any] | None = None,
) -> SagaResult:
    """Выполняет цепочку шагов с компенсацией при сбое.

    При падении шага — откат всех ранее завершённых в обратном порядке.
    Ошибки компенсаций собираются в result.compensation_errors;
    если хотя бы одна компенсация провалилась после всех retry —
    result.inconsistent=True.
    """
    ctx = context or {}
    done: list[tuple[SagaStep, Any]] = []
    result = SagaResult(success=False, context=ctx)

    for step in steps:
        try:
            out = await step.action(ctx)
            done.append((step, out))
            result.completed.append(step.name)
        except Exception as exc:
            result.failed_at = step.name
            result.error = exc

            # Откат в обратном порядке
            for s, o in reversed(done):
                ok = await _run_compensation_with_retry(s, ctx, o, result)
                if not ok:
                    result.inconsistent = True

            return result

    result.success = True
    return result
