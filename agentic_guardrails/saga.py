"""Лёгкая Saga — компенсация при сбоях в цепочке шагов.

Каждый шаг имеет компенсацию. При падении — откат в обратном порядке.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


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


async def run_saga(
    steps: list[SagaStep],
    context: dict[str, Any] | None = None,
) -> SagaResult:
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
            for s, o in reversed(done):
                try:
                    await s.compensate(ctx, o)
                except Exception:
                    pass
            return result

    result.success = True
    return result
