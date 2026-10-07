"""Adaptive Autonomy — лестница R0–R5.

Effective R = Nominal R - (criticality + risk + complexity)
Человек вовлекается только там, где цена ошибки высока.

Особенности:
- decide() возвращает AutonomyDecision с полной историей (для аудита)
- effective() сохранён для обратной совместимости
- run_with_approval() ограничен таймаутом; approve, бросивший исключение,
  или зависший — приводит к DENY (fail-safe)
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Awaitable, Callable

import asyncio


class RLevel(IntEnum):
    R0 = 0  # интерактивный HITL
    R1 = 1
    R2 = 2  # HITL по исключению
    R3 = 3  # batch
    R4 = 4
    R5 = 5  # HOTL, только аудит


@dataclass(frozen=True)
class AutonomyDecision:
    """Полное решение об уровне автономии — для аудита и отладки."""
    level: RLevel
    nominal: RLevel
    criticality: int
    risk: int
    complexity: int
    reason: str


DEFAULT_APPROVAL_TIMEOUT = 30.0


@dataclass
class AutonomyLadder:
    nominal: RLevel
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT

    def effective(
        self,
        criticality: int = 0,
        risk: int = 0,
        complexity: int = 0,
    ) -> RLevel:
        """Уровень автономии (обратная совместимость)."""
        return self.decide(criticality, risk, complexity).level

    def decide(
        self,
        criticality: int = 0,
        risk: int = 0,
        complexity: int = 0,
    ) -> AutonomyDecision:
        """Полное решение об уровне автономии с обоснованием."""
        delta = criticality + risk + complexity
        level = RLevel(max(0, int(self.nominal) - delta))
        reason = (
            f"nominal={self.nominal.name} "
            f"- (criticality={criticality} + risk={risk} + complexity={complexity}) "
            f"-> {level.name}"
        )
        return AutonomyDecision(
            level=level,
            nominal=self.nominal,
            criticality=criticality,
            risk=risk,
            complexity=complexity,
            reason=reason,
        )

    def mode(self, level: RLevel) -> str:
        if level <= RLevel.R1:
            return "interactive"
        if level == RLevel.R2:
            return "on_exception"
        if level <= RLevel.R4:
            return "batch"
        return "autonomous"


ApprovalFn = Callable[[dict], Awaitable[bool]]


async def run_with_approval(
    ladder: AutonomyLadder,
    action: dict,
    approve: ApprovalFn,
    ctx: dict | None = None,
) -> bool:
    """Возвращает True если действие разрешено.

    Fail-safe:
    - approve, бросивший исключение -> False
    - approve, зависший дольше approval_timeout -> False
    """
    ctx = ctx or {}
    decision = ladder.decide(
        criticality=ctx.get("criticality", 0),
        risk=ctx.get("risk", 0),
        complexity=ctx.get("complexity", 0),
    )
    mode = ladder.mode(decision.level)

    if mode == "autonomous":
        return True

    request = {**action, "_batch": True} if mode == "batch" else action

    try:
        if ladder.approval_timeout > 0:
            return await asyncio.wait_for(
                approve(request),
                timeout=ladder.approval_timeout,
            )
        return await approve(request)
    except asyncio.TimeoutError:
        return False
    except Exception:
        return False
